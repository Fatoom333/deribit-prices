from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import Database
from app.domain import PriceReader
from app.repositories.prices import PriceRepository


def get_database(request: Request) -> Database:
    """Return the Database created by the application lifespan."""
    return request.app.state.database


async def get_session(database: Database = Depends(get_database)) -> AsyncIterator[AsyncSession]:
    """Open a session for one request; it is committed or rolled back on exit."""
    async with database.session() as session:
        yield session


def get_price_reader(session: AsyncSession = Depends(get_session)) -> PriceReader:
    """Build the price repository bound to the request's session."""
    return PriceRepository(session)
