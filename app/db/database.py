"""Database access: engine and session factory wrapped in one object."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Database:
    """Owns the async engine. Create one per event loop and dispose it when done."""

    def __init__(
        self,
        url: str,
        connect_timeout_seconds: float = 5.0,
        command_timeout_seconds: float = 5.0,
    ) -> None:
        # asyncpg's `timeout` bounds establishing a connection, `command_timeout` bounds
        # each query; without them a hung database would block a task attempt for a minute.
        self._engine = create_async_engine(
            url,
            pool_pre_ping=True,
            connect_args={
                "timeout": connect_timeout_seconds,
                "command_timeout": command_timeout_seconds,
            },
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        """The underlying engine (used by tests to reset and inspect the schema and to read the URL)."""
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session in one transaction: commit on success, roll back on error."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        """Close all pooled connections."""
        await self._engine.dispose()
