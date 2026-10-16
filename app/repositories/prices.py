"""Price storage on top of PostgreSQL."""

from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PriceRow
from app.domain import PriceRecord
from app.tickers import Ticker


class PriceRepository:
    """Reads and writes prices; implements PriceReader and PriceWriter.

    The repository never commits: the session owner decides when the transaction ends.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(self, records: Sequence[PriceRecord]) -> int:
        """Insert records, skipping duplicates of (ticker, timestamp); return inserted count."""
        if not records:
            return 0
        values = [
            {"ticker": str(r.ticker), "price": r.price, "timestamp": r.timestamp}
            for r in records
        ]
        statement = (
            insert(PriceRow)
            .values(values)
            .on_conflict_do_nothing(index_elements=["ticker", "timestamp"])
            .returning(PriceRow.id)
        )
        result = await self._session.execute(statement)
        return len(result.scalars().all())

    async def get_all(
        self, ticker: Ticker, limit: int | None = None, offset: int = 0
    ) -> list[PriceRecord]:
        """All records of a ticker ordered by timestamp ascending."""
        statement = (
            select(PriceRow)
            .where(PriceRow.ticker == str(ticker))
            .order_by(PriceRow.timestamp)
            .limit(limit)
            .offset(offset)
        )
        return await self._fetch(statement)

    async def get_latest(self, ticker: Ticker) -> PriceRecord | None:
        """The record with the greatest timestamp, or None if there is no data."""
        statement = (
            select(PriceRow)
            .where(PriceRow.ticker == str(ticker))
            .order_by(PriceRow.timestamp.desc())
            .limit(1)
        )
        records = await self._fetch(statement)
        return records[0] if records else None

    async def get_range(
        self, ticker: Ticker, date_from: int, date_to: int
    ) -> list[PriceRecord]:
        """Records with date_from <= timestamp <= date_to, ordered by timestamp ascending."""
        statement = (
            select(PriceRow)
            .where(
                PriceRow.ticker == str(ticker),
                PriceRow.timestamp >= date_from,
                PriceRow.timestamp <= date_to,
            )
            .order_by(PriceRow.timestamp)
        )
        return await self._fetch(statement)

    async def _fetch(self, statement: Select[tuple[PriceRow]]) -> list[PriceRecord]:
        result = await self._session.execute(statement)
        return [self._to_record(row) for row in result.scalars()]

    @staticmethod
    def _to_record(row: PriceRow) -> PriceRecord:
        return PriceRecord(ticker=Ticker(row.ticker), price=row.price, timestamp=row.timestamp)
