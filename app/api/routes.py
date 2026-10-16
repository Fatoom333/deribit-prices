from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.exceptions import RequestValidationError

from app.api.dependencies import get_price_reader
from app.api.schemas import PriceOut
from app.domain import PriceReader
from app.tickers import Ticker

router = APIRouter(prefix="/prices", tags=["prices"])

TickerQuery = Annotated[Ticker, Query(description="Index name, e.g. btc_usd")]
# Largest value of a PostgreSQL BIGINT; anything bigger would make the query fail with a 500.
MAX_BIGINT = 2**63 - 1
UnixSeconds = Annotated[
    int, Query(ge=0, le=MAX_BIGINT, description="UNIX timestamp in seconds")
]


@router.get("", response_model=list[PriceOut])
async def list_prices(
    ticker: TickerQuery,
    reader: Annotated[PriceReader, Depends(get_price_reader)],
    limit: Annotated[int | None, Query(ge=1, le=10_000)] = None,
    offset: Annotated[int, Query(ge=0, le=MAX_BIGINT)] = 0,
) -> list[PriceOut]:
    """All stored prices of a ticker, oldest first."""
    records = await reader.get_all(ticker, limit=limit, offset=offset)
    return [PriceOut.from_record(record) for record in records]


@router.get("/latest", response_model=PriceOut)
async def latest_price(
    ticker: TickerQuery,
    reader: Annotated[PriceReader, Depends(get_price_reader)],
) -> PriceOut:
    """The most recent stored price of a ticker."""
    record = await reader.get_latest(ticker)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"No prices stored yet for ticker '{ticker}'"
        )
    return PriceOut.from_record(record)


@router.get("/range", response_model=list[PriceOut])
async def prices_in_range(
    ticker: TickerQuery,
    date_from: UnixSeconds,
    date_to: UnixSeconds,
    reader: Annotated[PriceReader, Depends(get_price_reader)],
) -> list[PriceOut]:
    """Prices with date_from <= timestamp <= date_to (both ends inclusive)."""
    if date_from > date_to:
        # Same error structure as FastAPI's own validation errors.
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("query", "date_from"),
                    "msg": "date_from must not be greater than date_to",
                    "input": date_from,
                }
            ]
        )
    records = await reader.get_range(ticker, date_from, date_to)
    return [PriceOut.from_record(record) for record in records]
