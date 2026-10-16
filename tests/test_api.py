from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_price_reader
from app.api.routes import MAX_BIGINT, MAX_PAGE_SIZE
from app.config import Settings
from app.domain import PriceRecord
from app.main import create_app
from app.tickers import Ticker


class InMemoryPriceReader:
    """PriceReader fake that keeps records in a list and records the last call."""

    def __init__(self, records: list[PriceRecord]) -> None:
        self._records = sorted(records, key=lambda record: record.timestamp)
        self.last_all_args: tuple[Ticker, int | None, int] | None = None
        self.last_range_args: tuple[Ticker, int, int, int | None, int] | None = None

    def _for(self, ticker: Ticker) -> list[PriceRecord]:
        return [record for record in self._records if record.ticker == ticker]

    async def get_all(
        self, ticker: Ticker, limit: int | None = None, offset: int = 0
    ) -> list[PriceRecord]:
        self.last_all_args = (ticker, limit, offset)
        records = self._for(ticker)[offset:]
        return records if limit is None else records[:limit]

    async def get_latest(self, ticker: Ticker) -> PriceRecord | None:
        records = self._for(ticker)
        return records[-1] if records else None

    async def get_range(
        self,
        ticker: Ticker,
        date_from: int,
        date_to: int,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[PriceRecord]:
        self.last_range_args = (ticker, date_from, date_to, limit, offset)
        records = [r for r in self._for(ticker) if date_from <= r.timestamp <= date_to][offset:]
        return records if limit is None else records[:limit]


def make_records() -> list[PriceRecord]:
    return [
        PriceRecord(Ticker.BTC_USD, Decimal("82190.06000000"), 1000),
        PriceRecord(Ticker.BTC_USD, Decimal("82200.10000000"), 1060),
        PriceRecord(Ticker.BTC_USD, Decimal("82210.12345678"), 1120),
        PriceRecord(Ticker.ETH_USD, Decimal("3100.50000000"), 1060),
    ]


@pytest.fixture
def reader() -> InMemoryPriceReader:
    return InMemoryPriceReader(make_records())


@pytest.fixture
def client(reader: InMemoryPriceReader) -> Iterator[TestClient]:
    app = create_app(Settings())
    app.dependency_overrides[get_price_reader] = lambda: reader
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "path", ["/prices", "/prices/latest", "/prices/range?date_from=0&date_to=10"]
)
def test_ticker_is_required(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/prices?ticker=doge_usd",
        "/prices/latest?ticker=doge_usd",
        "/prices/range?ticker=doge_usd&date_from=0&date_to=10",
    ],
)
def test_unknown_ticker_is_rejected(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 422


def test_list_returns_all_records_ordered(client: TestClient) -> None:
    response = client.get("/prices", params={"ticker": "btc_usd"})

    assert response.status_code == 200
    assert [item["timestamp"] for item in response.json()] == [1000, 1060, 1120]
    assert {item["ticker"] for item in response.json()} == {"btc_usd"}


def test_list_passes_limit_and_offset(client: TestClient, reader: InMemoryPriceReader) -> None:
    response = client.get("/prices", params={"ticker": "btc_usd", "limit": 1, "offset": 1})

    assert response.status_code == 200
    assert reader.last_all_args == (Ticker.BTC_USD, 1, 1)
    assert [item["timestamp"] for item in response.json()] == [1060]


def test_list_defaults_to_max_page_size(client: TestClient, reader: InMemoryPriceReader) -> None:
    client.get("/prices", params={"ticker": "eth_usd"})

    assert reader.last_all_args == (Ticker.ETH_USD, MAX_PAGE_SIZE, 0)


PAGING_ERRORS = [{"limit": 0}, {"limit": MAX_PAGE_SIZE + 1}, {"offset": -1}]


@pytest.mark.parametrize("params", PAGING_ERRORS)
def test_list_validates_paging(client: TestClient, params: dict[str, int]) -> None:
    response = client.get("/prices", params={"ticker": "btc_usd", **params})

    assert response.status_code == 422


def test_range_passes_limit_and_offset(client: TestClient, reader: InMemoryPriceReader) -> None:
    response = client.get(
        "/prices/range",
        params={"ticker": "btc_usd", "date_from": 0, "date_to": 2000, "limit": 1, "offset": 1},
    )

    assert response.status_code == 200
    assert reader.last_range_args == (Ticker.BTC_USD, 0, 2000, 1, 1)
    assert [item["timestamp"] for item in response.json()] == [1060]


def test_range_defaults_to_max_page_size(client: TestClient, reader: InMemoryPriceReader) -> None:
    client.get("/prices/range", params={"ticker": "btc_usd", "date_from": 0, "date_to": 2000})

    assert reader.last_range_args == (Ticker.BTC_USD, 0, 2000, MAX_PAGE_SIZE, 0)


@pytest.mark.parametrize("params", PAGING_ERRORS)
def test_range_validates_paging(client: TestClient, params: dict[str, int]) -> None:
    response = client.get(
        "/prices/range", params={"ticker": "btc_usd", "date_from": 0, "date_to": 2000, **params}
    )

    assert response.status_code == 422


def test_latest_returns_404_when_empty() -> None:
    app = create_app(Settings())
    app.dependency_overrides[get_price_reader] = lambda: InMemoryPriceReader([])
    with TestClient(app) as empty_client:
        response = empty_client.get("/prices/latest", params={"ticker": "btc_usd"})

    assert response.status_code == 404
    assert "btc_usd" in response.json()["detail"]


def test_latest_returns_newest_record(client: TestClient) -> None:
    response = client.get("/prices/latest", params={"ticker": "btc_usd"})

    assert response.status_code == 200
    assert response.json()["timestamp"] == 1120


def test_range_is_inclusive_on_both_ends(client: TestClient) -> None:
    response = client.get(
        "/prices/range", params={"ticker": "btc_usd", "date_from": 1000, "date_to": 1060}
    )

    assert response.status_code == 200
    assert [item["timestamp"] for item in response.json()] == [1000, 1060]


def test_range_rejects_reversed_dates_in_validation_error_format(client: TestClient) -> None:
    response = client.get(
        "/prices/range", params={"ticker": "btc_usd", "date_from": 2000, "date_to": 1000}
    )

    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert set(error) == {"type", "loc", "msg", "input"}
    assert error["loc"] == ["query", "date_from"]
    assert error["input"] == 2000
    assert "date_from" in error["msg"]



@pytest.mark.parametrize(
    ("path", "field"),
    [
        (f"/prices/range?ticker=btc_usd&date_from={MAX_BIGINT + 1}&date_to={MAX_BIGINT}", "date_from"),
        (f"/prices/range?ticker=btc_usd&date_from=0&date_to={MAX_BIGINT + 1}", "date_to"),
        ("/prices/range?ticker=btc_usd&date_from=0&date_to=99999999999999999999", "date_to"),
        (f"/prices?ticker=btc_usd&offset={MAX_BIGINT + 1}", "offset"),
        ("/prices?ticker=btc_usd&offset=99999999999999999999", "offset"),
    ],
)
def test_values_beyond_bigint_are_rejected_with_422(
    client: TestClient, path: str, field: str
) -> None:
    response = client.get(path)

    assert response.status_code == 422
    assert [error["loc"][-1] for error in response.json()["detail"]] == [field]


def test_bigint_maximum_itself_is_accepted(client: TestClient) -> None:
    response = client.get(
        "/prices/range",
        params={"ticker": "btc_usd", "date_from": 0, "date_to": MAX_BIGINT},
    )

    assert response.status_code == 200


def test_range_requires_both_dates(client: TestClient) -> None:
    response = client.get("/prices/range", params={"ticker": "btc_usd", "date_from": 0})

    assert response.status_code == 422


def test_range_rejects_negative_dates(client: TestClient) -> None:
    response = client.get(
        "/prices/range", params={"ticker": "btc_usd", "date_from": -1, "date_to": 10}
    )

    assert response.status_code == 422


def test_price_is_string_with_exact_digits_and_timestamp_is_int(client: TestClient) -> None:
    response = client.get("/prices/latest", params={"ticker": "btc_usd"})
    body = response.json()

    assert body["price"] == "82210.12345678"
    assert isinstance(body["timestamp"], int)
