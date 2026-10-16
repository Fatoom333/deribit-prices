import asyncio
import functools
import logging
import socket
import time
from collections.abc import Callable

import aiohttp
import asyncpg
from celery import Celery, Task
from sqlalchemy.exc import DBAPIError, InterfaceError

from app.clients.deribit import DeribitClient
from app.config import Settings
from app.db.database import Database
from app.domain import PriceSourceError
from app.repositories.prices import PriceRepository
from app.services.collector import PriceCollector, slot_timestamp
from app.tickers import Ticker

logger = logging.getLogger(__name__)

COLLECT_TASK_NAME = "collect_prices"

# The spec fixes the period at one minute; it is the slot length and the task lifetime.
COLLECTION_PERIOD_SECONDS = 60

# Retry budget. `expires` is inherited by retries as an absolute time, so every attempt
# must finish inside the slot. One attempt takes at most deribit timeout (10 s) + database
# connect timeout (5 s) + database command timeout (5 s) = 20 s (the upper bounds of the
# Settings fields), so the worst case is
# (MAX_RETRIES + 1) * 20 + MAX_RETRIES * RETRY_DELAY_SECONDS = 2 * 20 + 5 = 45 s < 60 s.
MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 5

_MAX_CAUSE_DEPTH = 10

# Server-side conditions that mean "the database is (temporarily) unreachable".
_DB_UNAVAILABLE = (
    asyncpg.PostgresConnectionError,
    asyncpg.exceptions.AdminShutdownError,
    asyncpg.exceptions.CrashShutdownError,
    asyncpg.exceptions.CannotConnectNowError,
)

# Network-level failures. OSError as a whole is too wide (it includes FileNotFoundError,
# PermissionError, ...), so only connection, timeout and name-resolution errors are listed.
_NETWORK_ERRORS = (ConnectionError, TimeoutError, socket.gaierror, asyncio.TimeoutError)


def _has_db_unavailable_cause(exc: BaseException) -> bool:
    """Whether `exc` or anything in its orig/__cause__ chain is a connection-level asyncpg error.

    SQLAlchemy does not raise asyncpg errors directly: it wraps them in DBAPIError, whose
    `orig` is an adapter exception chained (`__cause__`) to the real asyncpg exception.
    """
    current: BaseException | None = exc
    for _ in range(_MAX_CAUSE_DEPTH):
        if current is None:
            return False
        if isinstance(current, _DB_UNAVAILABLE):
            return True
        current = getattr(current, "orig", None) or current.__cause__
    return False


def is_retryable(exc: BaseException) -> bool:
    """Whether a failure is transient (exchange or database unreachable) and worth a retry.

    Programming errors, data errors (bad SQL, IntegrityError, bugs) and a query cancelled
    by the command timeout (QueryCanceledError) are not retryable: repeating them cannot
    help, or would only spend more of the slot.
    """
    if isinstance(exc, (PriceSourceError, *_NETWORK_ERRORS, InterfaceError)):
        return True
    if isinstance(exc, DBAPIError):
        return exc.connection_invalidated or _has_db_unavailable_cause(exc)
    return isinstance(exc, _DB_UNAVAILABLE)


def current_slot(time_source: Callable[[], float], interval_seconds: int) -> int:
    """Timestamp of the current schedule slot, read from `time_source` (time.time in production)."""
    return slot_timestamp(time_source(), interval_seconds)


async def run_collection(settings: Settings, clock: Callable[[], int] | None = None) -> int:
    """Wire the collector together, run it once and return the number of inserted prices.

    Everything is created here and closed here: asyncio.run() gives every task run a
    new event loop, and asyncpg connections cannot be reused across loops, so the
    Database (and its connection pool) must live exactly as long as this coroutine.

    `clock` defaults to the start of the current schedule slot; tests pass their own.
    """
    if clock is None:
        clock = functools.partial(current_slot, time.time, COLLECTION_PERIOD_SECONDS)
    database = Database(
        settings.database_url,
        settings.database_connect_timeout_seconds,
        settings.database_command_timeout_seconds,
    )
    try:
        # The request timeout is configured once, on DeribitClient.
        async with aiohttp.ClientSession() as http_session:
            client = DeribitClient(
                http_session,
                settings.deribit_base_url,
                settings.deribit_timeout_seconds,
            )
            async with database.session() as db_session:
                collector = PriceCollector(
                    source=client,
                    writer=PriceRepository(db_session),
                    clock=clock,
                )
                result = await collector.collect(list(Ticker))
        return result.inserted
    finally:
        await database.dispose()


def register_tasks(app: Celery, settings: Settings) -> Task:
    """Register the collection task on the given Celery app and return it."""

    @app.task(
        bind=True,
        name=COLLECT_TASK_NAME,
        max_retries=MAX_RETRIES,
        ignore_result=True,
    )
    def collect_prices(self: Task) -> int:
        try:
            return asyncio.run(run_collection(settings))
        except Exception as exc:
            if not is_retryable(exc):
                raise
            logger.warning("Collection failed, retrying: %r", exc)
            raise self.retry(exc=exc, countdown=RETRY_DELAY_SECONDS)

    return collect_prices
