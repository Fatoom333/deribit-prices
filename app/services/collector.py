"""Collection of index prices: ask the exchange for every ticker and store the results."""

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.domain import PriceRecord, PriceSource, PriceSourceError, PriceWriter
from app.tickers import Ticker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """Outcome of one collection run.

    `fetched` are the prices obtained from the source; `inserted` is how many of them
    were actually new rows (a retry in the same time slot inserts nothing).
    """

    fetched: list[PriceRecord]
    inserted: int


def slot_timestamp(now: float, interval_seconds: int) -> int:
    """Start of the schedule slot that contains `now`, as UNIX seconds.

    Every run inside one slot gets the same value, so a retry or a redelivered task
    produces the same (ticker, timestamp) key and the database deduplicates it.
    """
    if interval_seconds < 1:
        raise ValueError("interval_seconds must be at least 1")
    return int(now) // interval_seconds * interval_seconds


class PriceCollector:
    """Fetches current prices from a source and saves them through a writer.

    The clock is injected so that tests (and callers) control the timestamp.
    """

    def __init__(
        self,
        source: PriceSource,
        writer: PriceWriter,
        clock: Callable[[], int],
    ) -> None:
        self._source = source
        self._writer = writer
        self._clock = clock

    async def collect(self, tickers: Sequence[Ticker]) -> CollectionResult:
        """Fetch all tickers concurrently and save every price that was obtained.

        All records of one run share a single timestamp (the injected clock). A failed ticker is logged
        and skipped. If nothing could be fetched at all, PriceSourceError is raised
        so the caller (the Celery task) can retry.
        """
        timestamp = self._clock()
        results = await asyncio.gather(
            *(self._source.get_index_price(ticker) for ticker in tickers),
            return_exceptions=True,
        )

        records: list[PriceRecord] = []
        failures: list[PriceSourceError] = []
        for ticker, result in zip(tickers, results, strict=True):
            if isinstance(result, PriceSourceError):
                logger.warning("Could not fetch %s: %s", ticker, result)
                failures.append(result)
            elif isinstance(result, BaseException):
                # Unexpected errors (bugs) must not be hidden as "price unavailable".
                raise result
            else:
                records.append(self._to_record(ticker, result, timestamp))

        if not records:
            if failures:
                raise PriceSourceError("No prices could be fetched") from failures[0]
            return CollectionResult(fetched=[], inserted=0)

        inserted = await self._writer.add_many(records)
        logger.info(
            "Fetched %d price(s), inserted %d new at %d", len(records), inserted, timestamp
        )
        return CollectionResult(fetched=records, inserted=inserted)

    @staticmethod
    def _to_record(ticker: Ticker, price: Decimal, timestamp: int) -> PriceRecord:
        return PriceRecord(ticker=ticker, price=price, timestamp=timestamp)
