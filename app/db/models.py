"""SQLAlchemy table definitions."""

from decimal import Decimal

from sqlalchemy import BigInteger, Identity, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base; its metadata is used by tests and Alembic."""


class PriceRow(Base):
    """One stored index price. timestamp is UNIX seconds (UTC)."""

    __tablename__ = "prices"
    # The unique constraint also creates the btree index (ticker, timestamp) that serves
    # every query by ticker and time range, so no separate index is declared.
    __table_args__ = (UniqueConstraint("ticker", "timestamp", name="uq_prices_ticker_timestamp"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
