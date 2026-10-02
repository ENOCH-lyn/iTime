import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from alembic.script import ScriptDirectory
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from itime import __version__
from itime.admin.management import router as admin_management_router
from itime.admin.router import router as admin_auth_router
from itime.auth.router import router as auth_router
from itime.auth.security import Security
from itime.config import Settings, get_settings
from itime.database import Database
from itime.errors import AppError, install_handlers
from itime.middleware import RequestEnvelope
from itime.observability import configure_logging
from itime.tasks.router import router as tasks_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    database = Database(settings)
    schema_revision = ScriptDirectory(str(Path(__file__).parent / "migrations")).get_current_head()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await database.close()

    app = FastAPI(
        title="iTime API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.environment != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.environment != "production" else None,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.security = Security(settings)
    install_handlers(app)
    app.add_middleware(RequestEnvelope, settings=settings)
    app.include_router(auth_router)
    app.include_router(admin_auth_router)
    app.include_router(admin_management_router)
    app.include_router(tasks_router)

    @app.get("/api/health", tags=["运行状态"])
    async def health() -> dict:
        return {"status": "alive", "version": __version__, "server_time": datetime.now(UTC)}

    @app.get("/api/ready", tags=["运行状态"])
    async def ready() -> dict:
        try:
            async with asyncio.timeout(settings.readiness_timeout_seconds):
                async with database.engine.connect() as connection:
                    revision = await connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                    if revision != schema_revision:
                        raise AppError(
                            "schema_not_ready",
                            "数据结构正在更新，请稍后重试。",
                            status=503,
                            retryable=True,
                        )
        except (SQLAlchemyError, TimeoutError) as exc:
            raise AppError(
                "service_not_ready",
                "数据服务暂时不可用，请稍后重试。",
                status=503,
                retryable=True,
            ) from exc
        return {"status": "ready", "version": __version__}

    return app
