from collections.abc import Sequence
from decimal import Decimal

import pytest

from app.domain import PriceRecord, PriceSourceError
from app.services.collector import PriceCollector, slot_timestamp
from app.tickers import Ticker

FIXED_TIMESTAMP = 1_760_000_000


class FakeSource:
    """Returns prepared prices; a ticker mapped to an exception raises it."""

    def __init__(self, prices: dict[Ticker, Decimal | Exception]) -> None:
        self._prices = prices

    async def get_index_price(self, ticker: Ticker) -> Decimal:
        value = self._prices[ticker]
        if isinstance(value, Exception):
            raise value
        return value


class FakeWriter:
    """Remembers what it was asked to save; `already_stored` records count as duplicates."""

    def __init__(self, already_stored: int = 0) -> None:
        self.saved: list[PriceRecord] = []
        self._already_stored = already_stored

    async def add_many(self, records: Sequence[PriceRecord]) -> int:
        self.saved.extend(records)
        return max(len(records) - self._already_stored, 0)


def make_collector(source: FakeSource, writer: FakeWriter) -> PriceCollector:
    return PriceCollector(source, writer, clock=lambda: FIXED_TIMESTAMP)


async def test_saves_all_tickers_with_one_timestamp() -> None:
    source = FakeSource(
        {Ticker.BTC_USD: Decimal("82190.06"), Ticker.ETH_USD: Decimal("3100.5")}
    )
    writer = FakeWriter()

    result = await make_collector(source, writer).collect(list(Ticker))
    records = result.fetched

    assert result.inserted == 2
    assert len(records) == 2
    assert {record.timestamp for record in records} == {FIXED_TIMESTAMP}
    assert {record.ticker for record in records} == {Ticker.BTC_USD, Ticker.ETH_USD}
    assert writer.saved == records


async def test_failed_ticker_does_not_block_the_other() -> None:
    source = FakeSource(
        {Ticker.BTC_USD: PriceSourceError("timeout"), Ticker.ETH_USD: Decimal("3100.5")}
    )
    writer = FakeWriter()

    result = await make_collector(source, writer).collect(list(Ticker))

    expected = [PriceRecord(Ticker.ETH_USD, Decimal("3100.5"), FIXED_TIMESTAMP)]
    assert result.fetched == expected
    assert result.inserted == 1
    assert writer.saved == expected


async def test_all_failures_raise_and_write_nothing() -> None:
    source = FakeSource(
        {Ticker.BTC_USD: PriceSourceError("down"), Ticker.ETH_USD: PriceSourceError("down")}
    )
    writer = FakeWriter()

    with pytest.raises(PriceSourceError):
        await make_collector(source, writer).collect(list(Ticker))

    assert writer.saved == []


async def test_decimal_precision_is_preserved() -> None:
    price = Decimal("82190.06000001")
    source = FakeSource({Ticker.BTC_USD: price})
    writer = FakeWriter()

    result = await make_collector(source, writer).collect([Ticker.BTC_USD])

    assert result.fetched[0].price == price
    assert isinstance(result.fetched[0].price, Decimal)
    assert str(writer.saved[0].price) == "82190.06000001"


async def test_unexpected_error_is_not_swallowed() -> None:
    source = FakeSource({Ticker.BTC_USD: RuntimeError("bug"), Ticker.ETH_USD: Decimal("1")})
    writer = FakeWriter()

    with pytest.raises(RuntimeError):
        await make_collector(source, writer).collect(list(Ticker))

    assert writer.saved == []


async def test_inserted_counts_only_new_rows_not_fetched_ones() -> None:
    source = FakeSource(
        {Ticker.BTC_USD: Decimal("82190.06"), Ticker.ETH_USD: Decimal("3100.5")}
    )
    writer = FakeWriter(already_stored=2)

    result = await make_collector(source, writer).collect(list(Ticker))

    assert len(result.fetched) == 2
    assert result.inserted == 0


async def test_no_tickers_is_an_empty_result() -> None:
    result = await make_collector(FakeSource({}), FakeWriter()).collect([])

    assert result.fetched == []
    assert result.inserted == 0


@pytest.mark.parametrize(
    ("now", "interval", "expected"),
    [
        (1_760_000_039.9, 60, 1_759_999_980),
        (1_760_000_040.0, 60, 1_760_000_040),
        (1_760_000_099.99, 60, 1_760_000_040),
        (1_760_000_100.0, 60, 1_760_000_100),
        (125.7, 10, 120),
        (59.0, 1, 59),
    ],
)
def test_slot_timestamp_rounds_down_to_the_interval(
    now: float, interval: int, expected: int
) -> None:
    assert slot_timestamp(now, interval) == expected


def test_slot_timestamp_is_the_same_within_one_slot() -> None:
    assert slot_timestamp(1_760_000_041.0, 60) == slot_timestamp(1_760_000_098.5, 60)


@pytest.mark.parametrize("interval", [0, -60])
def test_slot_timestamp_rejects_non_positive_interval(interval: int) -> None:
    with pytest.raises(ValueError):
        slot_timestamp(1_760_000_000.0, interval)
