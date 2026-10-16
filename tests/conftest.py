"""Shared fixtures: a PostgreSQL schema built by the real Alembic migrations."""

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db.database import Database


def alembic_config(database_url: str) -> Config:
    """Alembic config pointing at `database_url` instead of the application settings."""
    config = Config("alembic.ini")
    # '%' is special in Alembic's ini interpolation.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    """A Database whose schema was created by `alembic upgrade head` on a clean test DB."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    db = Database(url)
    async with db.engine.begin() as connection:
        await connection.execute(text("DROP TABLE IF EXISTS prices, alembic_version"))
    # env.py starts its own event loop, which is impossible inside a running one.
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
    yield db
    await db.dispose()
