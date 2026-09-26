from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from deal_intel.api.errors import register_error_handlers
from deal_intel.api.middleware import (
    BodySizeLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from deal_intel.api.request_context import install_request_id_log_filter
from deal_intel.api.routes import approvals, health, runs, ui
from deal_intel.api.runtime import AppRuntime, build_runtime, running_under_pytest
from deal_intel.api.templating import STATIC_DIR, STATIC_MOUNT_NAME, STATIC_MOUNT_PATH
from deal_intel.config import get_settings
from deal_intel.observability.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings().log_level)
    install_request_id_log_filter()
    runtime = getattr(app.state, "runtime", None)
    if runtime is None and not running_under_pytest():
        runtime = build_runtime()
        app.state.runtime = runtime
    if runtime is not None:
        runtime.start()
    try:
        yield
    finally:
        if runtime is not None:
            runtime.shutdown()


def install_middleware(app: FastAPI, max_body_bytes: int) -> None:
    # Each call wraps the previous stack, so the last one added runs outermost.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=max_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)


def create_app(runtime: AppRuntime | None = None) -> FastAPI:
    # Swagger UI and ReDoc load CDN and inline scripts, which the content security policy blocks.
    app = FastAPI(
        title="Deal Intelligence Assistant", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    if runtime is not None:
        app.state.runtime = runtime
    register_error_handlers(app)
    install_middleware(app, get_settings().api_max_request_body_bytes)
    app.mount(STATIC_MOUNT_PATH, StaticFiles(directory=STATIC_DIR), name=STATIC_MOUNT_NAME)
    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(approvals.router)
    app.include_router(ui.router)
    return app


app = create_app()
