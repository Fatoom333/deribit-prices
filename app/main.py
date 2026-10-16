from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import Settings, get_settings
from app.db.database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory; run with `uvicorn --factory app.main:create_app`."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # The engine connects lazily, so startup does not need a live database.
        database = Database(
            settings.database_url,
            settings.database_connect_timeout_seconds,
            settings.database_command_timeout_seconds,
        )
        app.state.database = database
        try:
            yield
        finally:
            await database.dispose()

    app = FastAPI(title="Deribit prices", lifespan=lifespan)
    app.include_router(router)
    return app
