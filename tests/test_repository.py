"""Integration tests of PriceRepository against a real PostgreSQL (TEST_DATABASE_URL).

The schema comes from the `database` fixture in conftest.py (built by Alembic)."""

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import Database
from app.domain import PriceRecord
from app.repositories.prices import PriceRepository
from app.tickers import Ticker

pytestmark = pytest.mark.integration


def record(ticker: Ticker, timestamp: int, price: str = "100.5") -> PriceRecord:
    return PriceRecord(ticker=ticker, price=Decimal(price), timestamp=timestamp)


@pytest.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    async with database.session() as s:
        yield s


@pytest.fixture
def repo(session: AsyncSession) -> PriceRepository:
    return PriceRepository(session)


async def test_add_many_returns_inserted_count(repo: PriceRepository) -> None:
    inserted = await repo.add_many([record(Ticker.BTC_USD, 1), record(Ticker.ETH_USD, 1)])
    assert inserted == 2


async def test_add_many_empty_is_noop(repo: PriceRepository) -> None:
    assert await repo.add_many([]) == 0


async def test_add_many_skips_duplicates(repo: PriceRepository) -> None:
    await repo.add_many([record(Ticker.BTC_USD, 1, "10")])
    inserted = await repo.add_many([record(Ticker.BTC_USD, 1, "99"), record(Ticker.BTC_USD, 2)])
    assert inserted == 1
    stored = await repo.get_all(Ticker.BTC_USD)
    assert [(r.timestamp, r.price) for r in stored] == [(1, Decimal("10")), (2, Decimal("100.5"))]


async def test_price_keeps_decimal_precision(repo: PriceRepository) -> None:
    await repo.add_many([record(Ticker.BTC_USD, 1, "82190.06123456")])
    (stored,) = await repo.get_all(Ticker.BTC_USD)
    assert stored.price == Decimal("82190.06123456")
    assert isinstance(stored.ticker, Ticker)


async def test_get_all_is_ordered_by_timestamp(repo: PriceRepository) -> None:
    await repo.add_many([record(Ticker.BTC_USD, t) for t in (3, 1, 2)])
    assert [r.timestamp for r in await repo.get_all(Ticker.BTC_USD)] == [1, 2, 3]


async def test_get_all_limit_and_offset(repo: PriceRepository) -> None:
    await repo.add_many([record(Ticker.BTC_USD, t) for t in range(1, 6)])
    page = await repo.get_all(Ticker.BTC_USD, limit=2, offset=1)
    assert [r.timestamp for r in page] == [2, 3]
    assert [r.timestamp for r in await repo.get_all(Ticker.BTC_USD, offset=3)] == [4, 5]


async def test_get_latest_returns_greatest_timestamp(repo: PriceRepository) -> None:
    await repo.add_many([record(Ticker.BTC_USD, t) for t in (5, 9, 7)])
    latest = await repo.get_latest(Ticker.BTC_USD)
    assert latest is not None and latest.timestamp == 9


async def test_get_latest_without_data_is_none(repo: PriceRepository) -> None:
    assert await repo.get_latest(Ticker.BTC_USD) is None


async def test_get_range_is_inclusive(repo: PriceRepository) -> None:
    await repo.add_many([record(Ticker.BTC_USD, t) for t in (1, 2, 3, 4, 5)])
    found = await repo.get_range(Ticker.BTC_USD, 2, 4)
    assert [r.timestamp for r in found] == [2, 3, 4]


async def test_get_range_outside_data_is_empty(repo: PriceRepository) -> None:
    await repo.add_many([record(Ticker.BTC_USD, 1)])
    assert await repo.get_range(Ticker.BTC_USD, 10, 20) == []


async def test_tickers_are_isolated(repo: PriceRepository) -> None:
    await repo.add_many([record(Ticker.BTC_USD, 1), record(Ticker.ETH_USD, 2)])
    assert [r.ticker for r in await repo.get_all(Ticker.BTC_USD)] == [Ticker.BTC_USD]
    latest = await repo.get_latest(Ticker.ETH_USD)
    assert latest is not None and latest.timestamp == 2
    assert await repo.get_range(Ticker.BTC_USD, 0, 10) == [record(Ticker.BTC_USD, 1)]


async def test_database_session_commits_on_success(database: Database) -> None:
    async with database.session() as s:
        await PriceRepository(s).add_many([record(Ticker.BTC_USD, 1)])
    async with database.session() as s:
        assert len(await PriceRepository(s).get_all(Ticker.BTC_USD)) == 1


async def test_database_session_rolls_back_on_error(database: Database) -> None:
    with pytest.raises(RuntimeError):
        async with database.session() as s:
            await PriceRepository(s).add_many([record(Ticker.BTC_USD, 1)])
            raise RuntimeError("boom")
    async with database.session() as s:
        assert await PriceRepository(s).get_all(Ticker.BTC_USD) == []
