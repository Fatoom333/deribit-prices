"""Core types shared by every layer: the price record and the contracts between layers."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.tickers import Ticker


@dataclass(frozen=True, slots=True)
class PriceRecord:
    """One observed index price.

    timestamp is a UNIX timestamp in seconds (UTC): the start of the schedule slot
    in which the price was collected.
    """

    ticker: Ticker
    price: Decimal
    timestamp: int


class PriceSourceError(Exception):
    """Raised when a price cannot be obtained from the exchange (network, HTTP or API error)."""


class PriceSource(Protocol):
    """Anything that can tell the current index price of a ticker (Deribit in production)."""

    async def get_index_price(self, ticker: Ticker) -> Decimal: ...


class PriceReader(Protocol):
    """Read side of the price storage, used by the API.

    All lists are ordered by timestamp ascending.
    """

    async def get_all(
        self, ticker: Ticker, limit: int | None = None, offset: int = 0
    ) -> list[PriceRecord]: ...

    async def get_latest(self, ticker: Ticker) -> PriceRecord | None: ...

    async def get_range(
        self, ticker: Ticker, date_from: int, date_to: int
    ) -> list[PriceRecord]:
        """Records with date_from <= timestamp <= date_to (inclusive on both ends)."""
        ...


class PriceWriter(Protocol):
    """Write side of the price storage, used by the collector."""

    async def add_many(self, records: Sequence[PriceRecord]) -> int:
        """Insert records, silently skipping duplicates of (ticker, timestamp).

        Returns the number of rows actually inserted. Does not commit.
        """
        ...
