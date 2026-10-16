import asyncio
import socket

import asyncpg
import pytest
from celery import Celery
from celery.exceptions import Retry
from celery.schedules import crontab
from annotated_types import Le
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_connection, AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, ProgrammingError

from app.config import Settings
from app.domain import PriceSourceError
from app.worker import tasks
from app.worker.celery_app import create_celery_app


@pytest.fixture
def settings() -> Settings:
    return Settings(redis_url="redis://localhost:6379/9")


@pytest.fixture
def celery_app(settings: Settings) -> Celery:
    return create_celery_app(settings)


def test_beat_schedule_runs_collect_task_every_minute_at_second_zero(celery_app: Celery) -> None:
    entry = celery_app.conf.beat_schedule["collect-prices"]

    assert entry["task"] == tasks.COLLECT_TASK_NAME
    assert entry["schedule"] == crontab()
    assert entry["schedule"].minute == set(range(60))
    assert entry["schedule"].hour == set(range(24))


def test_beat_entry_expires_stale_runs_after_one_period(celery_app: Celery) -> None:
    options = celery_app.conf.beat_schedule["collect-prices"]["options"]

    assert options == {"expires": 60}


def test_task_is_registered(celery_app: Celery) -> None:
    assert tasks.COLLECT_TASK_NAME in celery_app.tasks


def test_current_slot_rounds_the_clock_down_to_the_interval() -> None:
    assert tasks.current_slot(lambda: 1_760_000_099.9, 60) == 1_760_000_040


def test_task_runs_collection_and_returns_inserted_count(
    celery_app: Celery, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_collection(settings: Settings) -> int:
        return 2

    monkeypatch.setattr(tasks, "run_collection", fake_run_collection)

    result = celery_app.tasks[tasks.COLLECT_TASK_NAME].apply()

    assert result.successful()
    assert result.result == 2


TIMEOUT_FIELDS = (
    "deribit_timeout_seconds",
    "database_connect_timeout_seconds",
    "database_command_timeout_seconds",
)


def max_allowed(field_name: str) -> float:
    """The upper bound (`le`) declared for a Settings field."""
    (bound,) = [m.le for m in Settings.model_fields[field_name].metadata if isinstance(m, Le)]
    return bound


def test_retry_budget_fits_into_one_slot_with_the_maximum_allowed_timeouts() -> None:
    attempt = sum(max_allowed(name) for name in TIMEOUT_FIELDS)
    worst_case = (tasks.MAX_RETRIES + 1) * attempt + tasks.MAX_RETRIES * tasks.RETRY_DELAY_SECONDS

    assert attempt == 20
    assert worst_case == 45
    assert worst_case < tasks.COLLECTION_PERIOD_SECONDS


@pytest.mark.parametrize("name", TIMEOUT_FIELDS)
def test_timeouts_above_the_budget_are_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{name: max_allowed(name) + 1})


@pytest.mark.parametrize("name", TIMEOUT_FIELDS)
def test_non_positive_timeouts_are_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{name: 0})


class _FakeAsyncpgConnection:
    """Just enough of an asyncpg connection for the adapter's error handling."""

    def is_closed(self) -> bool:
        return True


def _dbapi() -> AsyncAdapt_asyncpg_dbapi:
    return AsyncAdapt_asyncpg_dbapi(asyncpg)


def _wrapped(cause: Exception) -> DBAPIError:
    """An exception built the way SQLAlchemy builds it for a driver error.

    The adapter translates the asyncpg error (`raise translated from cause`), and the
    engine wraps the adapter error with `DBAPIError.instance` (`raise wrapped from orig`).
    """
    dbapi = AsyncAdapt_asyncpg_dbapi(asyncpg)
    connection = AsyncAdapt_asyncpg_connection(dbapi, _FakeAsyncpgConnection())
    try:
        connection._handle_exception(cause)
    except Exception as translated:
        wrapper = DBAPIError.instance("SELECT 1", {}, translated, dbapi.Error)
        try:
            raise wrapper from translated
        except DBAPIError as raised:
            return raised
    raise AssertionError("the adapter did not raise")


RETRYABLE = [
    PriceSourceError("exchange is down"),
    ConnectionRefusedError("database is not reachable"),
    TimeoutError("timed out"),
    ConnectionResetError("reset by peer"),
    socket.gaierror("name resolution failed"),
    asyncio.TimeoutError(),
    InterfaceError("SELECT 1", {}, _dbapi().InterfaceError("connection is closed")),
    DBAPIError("SELECT 1", {}, Exception("server closed"), connection_invalidated=True),
    _wrapped(asyncpg.exceptions.AdminShutdownError("terminating")),
    _wrapped(asyncpg.exceptions.CannotConnectNowError("starting up")),
    _wrapped(asyncpg.exceptions.ConnectionDoesNotExistError("gone")),
    asyncpg.PostgresConnectionError("connection lost"),
    asyncpg.exceptions.CannotConnectNowError("the database system is starting up"),
    asyncpg.exceptions.AdminShutdownError("terminating connection"),
    asyncpg.exceptions.CrashShutdownError("crash"),
]

NOT_RETRYABLE = [
    ProgrammingError("SELECT bad", {}, _dbapi().ProgrammingError("relation does not exist")),
    IntegrityError("INSERT", {}, _dbapi().IntegrityError("duplicate key")),
    _wrapped(asyncpg.exceptions.UndefinedTableError("no table")),
    DBAPIError("SELECT 1", {}, Exception("something else")),
    _wrapped(asyncpg.exceptions.QueryCanceledError("command timeout")),
    asyncpg.exceptions.QueryCanceledError("canceling statement"),
    asyncpg.exceptions.UniqueViolationError("duplicate"),
    FileNotFoundError("no such file"),
    PermissionError("denied"),
    ValueError("bug"),
    RuntimeError("bug"),
    KeyError("bug"),
]


@pytest.mark.parametrize("error", RETRYABLE, ids=lambda e: f"{type(e).__name__}-{e}")
def test_is_retryable_true_for_transient_failures(error: BaseException) -> None:
    assert tasks.is_retryable(error) is True


@pytest.mark.parametrize("error", NOT_RETRYABLE, ids=lambda e: f"{type(e).__name__}-{e}")
def test_is_retryable_false_for_programming_and_data_errors(error: BaseException) -> None:
    assert tasks.is_retryable(error) is False


def test_is_retryable_survives_a_cyclic_cause_chain() -> None:
    first, second = Exception("a"), Exception("b")
    first.__cause__, second.__cause__ = second, first

    assert tasks.is_retryable(DBAPIError("S", {}, first)) is False


def _run_failing_task(
    celery_app: Celery, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> list[Exception | None]:
    """Run the task body with run_collection raising `error`; return the retry() calls."""

    async def failing_run_collection(settings: Settings) -> int:
        raise error

    monkeypatch.setattr(tasks, "run_collection", failing_run_collection)
    task = celery_app.tasks[tasks.COLLECT_TASK_NAME]
    retries: list[Exception | None] = []

    def fake_retry(*, exc: Exception | None = None, countdown: int = 0, **_: object) -> Retry:
        retries.append(exc)
        return Retry(exc=exc)

    monkeypatch.setattr(task, "retry", fake_retry)
    try:
        task.run()
    except Retry:
        pass
    return retries


@pytest.mark.parametrize("error", RETRYABLE[:6], ids=lambda e: type(e).__name__)
def test_task_retries_on_transient_failures(
    celery_app: Celery, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    retries = _run_failing_task(celery_app, monkeypatch, error)

    assert retries == [error]


@pytest.mark.parametrize("error", NOT_RETRYABLE[:2] + NOT_RETRYABLE[-2:], ids=lambda e: type(e).__name__)
def test_task_does_not_retry_programming_errors(
    celery_app: Celery, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    async def failing_run_collection(settings: Settings) -> int:
        raise error

    monkeypatch.setattr(tasks, "run_collection", failing_run_collection)
    task = celery_app.tasks[tasks.COLLECT_TASK_NAME]

    with pytest.raises(type(error)):
        task.run()


def test_retries_are_bounded(celery_app: Celery) -> None:
    task = celery_app.tasks[tasks.COLLECT_TASK_NAME]

    assert task.max_retries == tasks.MAX_RETRIES
