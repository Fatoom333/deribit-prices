"""The schema produced by Alembic migrations must match the SQLAlchemy models."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.db.database import Database
from app.db.models import Base

pytestmark = pytest.mark.integration


def _diff(connection: Connection) -> list[object]:
    context = MigrationContext.configure(connection)
    return compare_metadata(context, Base.metadata)


def _ticker_timestamp_indexes(connection: Connection) -> list[str]:
    """Names of all indexes on exactly (ticker, timestamp) in the prices table."""
    indexes = inspect(connection).get_indexes("prices")
    return [i["name"] for i in indexes if list(i["column_names"]) == ["ticker", "timestamp"]]


async def test_migrated_schema_matches_models(database: Database) -> None:
    async with database.engine.connect() as connection:
        differences = await connection.run_sync(_diff)

    assert differences == []


async def test_only_one_index_covers_ticker_and_timestamp(database: Database) -> None:
    async with database.engine.connect() as connection:
        names = await connection.run_sync(_ticker_timestamp_indexes)

    # The unique constraint creates this index; there must be no duplicate beside it.
    assert names == ["uq_prices_ticker_timestamp"]
