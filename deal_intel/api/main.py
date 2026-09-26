from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from deal_intel.api.routes import health
from deal_intel.config import get_settings
from deal_intel.observability.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings().log_level)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Deal Intelligence Assistant", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
