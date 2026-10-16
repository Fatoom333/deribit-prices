from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict

from app.domain import PriceRecord
from app.tickers import Ticker


class PriceOut(BaseModel):
    """Public representation of a stored price.

    The price is a Decimal and is serialized as a JSON string, so clients never
    lose precision through a binary float.
    """

    model_config = ConfigDict(frozen=True)

    ticker: Ticker
    price: Decimal
    timestamp: int

    @classmethod
    def from_record(cls, record: PriceRecord) -> Self:
        return cls(ticker=record.ticker, price=record.price, timestamp=record.timestamp)
