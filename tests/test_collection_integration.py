"""run_collection end to end: a fake Deribit over HTTP and a real PostgreSQL."""

from collections.abc import AsyncIterator, Callable

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.db.database import Database
from app.repositories.prices import PriceRepository
from app.tickers import Ticker
from app.worker.tasks import is_retryable, run_collection

pytestmark = pytest.mark.integration

SLOT = 1_760_000_040
PRICES = {"btc_usd": "82190.06", "eth_usd": "3100.5"}


async def fake_index_price(request: web.Request) -> web.Response:
    index_name = request.query["index_name"]
    body = f'{{"jsonrpc":"2.0","result":{{"index_price":{PRICES[index_name]}}}}}'
    return web.Response(text=body, content_type="application/json")


@pytest.fixture
async def deribit_url() -> AsyncIterator[str]:
    app = web.Application()
    app.router.add_get("/api/v2/public/get_index_price", fake_index_price)
    server = TestServer(app)
    await server.start_server()
    yield f"http://{server.host}:{server.port}/api/v2"
    await server.close()


def settings_for(database: Database, deribit_url: str) -> Settings:
    return Settings(
        database_url=database.engine.url.render_as_string(hide_password=False),
        deribit_base_url=deribit_url,
        deribit_timeout_seconds=2.0,
    )


def slot_clock(value: int) -> Callable[[], int]:
    def clock() -> int:
        return value

    return clock


async def test_two_runs_in_one_slot_store_each_price_once(
    database: Database, deribit_url: str
) -> None:
    settings = settings_for(database, deribit_url)

    first = await run_collection(settings, clock=slot_clock(SLOT))
    second = await run_collection(settings, clock=slot_clock(SLOT))

    assert (first, second) == (2, 0)
    async with database.session() as session:
        repo = PriceRepository(session)
        for ticker in Ticker:
            stored = await repo.get_all(ticker)
            assert [r.timestamp for r in stored] == [SLOT]
        (btc,) = await repo.get_all(Ticker.BTC_USD)
    assert str(btc.price) == "82190.06000000"


async def test_next_slot_adds_new_rows(database: Database, deribit_url: str) -> None:
    settings = settings_for(database, deribit_url)

    await run_collection(settings, clock=slot_clock(SLOT))
    inserted = await run_collection(settings, clock=slot_clock(SLOT + 60))

    assert inserted == 2
    async with database.session() as session:
        stored = await PriceRepository(session).get_all(Ticker.ETH_USD)
    assert [r.timestamp for r in stored] == [SLOT, SLOT + 60]


async def test_unreachable_database_makes_the_task_retry(deribit_url: str) -> None:
    # Nothing listens on port 1, so connecting fails the way a stopped database does.
    settings = Settings(
        database_url="postgresql+asyncpg://postgres:postgres@127.0.0.1:1/prices",
        deribit_base_url=deribit_url,
        deribit_timeout_seconds=2.0,
    )

    with pytest.raises(Exception) as caught:
        await run_collection(settings, clock=slot_clock(SLOT))

    assert is_retryable(caught.value)


async def test_connection_terminated_by_the_server_is_retryable(database: Database) -> None:
    async with database.engine.connect() as victim, database.engine.connect() as killer:
        pid = (await victim.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        terminated = (await killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})).scalar_one()
        assert terminated is True

        with pytest.raises(DBAPIError) as caught:
            await victim.execute(text("SELECT 1"))

    assert is_retryable(caught.value)
