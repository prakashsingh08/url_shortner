"""url-service entry point: app factory, lifespan and graceful-shutdown server.

Run in a container with `python -m app.main` (one uvicorn process per container).
"""

import asyncio
import contextlib
import logging
import signal
import sys
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.errors import install_error_handlers
from app.api.middleware import install_middleware
from app.api.routes import health, testing
from app.application.health import HealthService
from app.config import Settings, load_settings_or_exit
from app.infrastructure.db import PostgresProbe, create_primary_pool
from app.infrastructure.logging import configure_logging

log = logging.getLogger("app")


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = create_primary_pool(settings.db_primary_url, settings.db_pool_size_write)
        # wait=False: start even if the database is down; readiness reports it.
        await pool.open(wait=False)
        app.state.health_service.primary.pool = pool
        log.info("startup complete", extra={"fields": {"env": settings.app_env}})
        try:
            yield
        finally:
            await pool.close()
            log.info("database pool closed")

    app = FastAPI(
        title="URL Shortener API",
        version="0.1.0",
        lifespan=lifespan,
        # Default /docs, /redoc, /openapi.json would compete with short codes.
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.shutting_down = False
    app.state.health_service = HealthService(
        primary=PostgresProbe(pool=None, timeout_seconds=settings.readiness_db_timeout_ms / 1000),
        is_shutting_down=lambda: app.state.shutting_down,
    )

    install_error_handlers(app)
    install_middleware(app, settings.max_body_bytes)
    app.include_router(health.router)
    if settings.test_routes_enabled:
        app.include_router(testing.router)
    return app


class GracefulServer(uvicorn.Server):
    """uvicorn server with a readiness drain window and exit code 0 (design D9).

    First SIGTERM/SIGINT: mark the instance as shutting down (readiness -> 503) but keep
    serving for `drain_seconds`, then start uvicorn's normal graceful shutdown, which
    waits up to `timeout_graceful_shutdown` for in-flight requests.
    """

    def __init__(self, config: uvicorn.Config, app: FastAPI, drain_seconds: float):
        super().__init__(config)
        self._app = app
        self._drain_seconds = drain_seconds
        self._draining = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def startup(self, sockets=None) -> None:
        self._loop = asyncio.get_running_loop()
        await super().startup(sockets)

    def handle_exit(self, sig, frame) -> None:
        if self._draining or self._drain_seconds <= 0 or self._loop is None:
            return super().handle_exit(sig, frame)
        self._draining = True
        self._app.state.shutting_down = True
        fields = {"signal": signal.Signals(sig).name, "drainSeconds": self._drain_seconds}
        log.info("shutdown requested, draining", extra={"fields": fields})
        finish = super().handle_exit
        loop = self._loop
        loop.call_soon_threadsafe(loop.call_later, self._drain_seconds, finish, sig, frame)

    @contextlib.contextmanager
    def capture_signals(self):
        # Same as uvicorn's, minus re-raising the signal afterwards (which would exit 143).
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        originals = {s: signal.signal(s, self.handle_exit) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            yield
        finally:
            for s, handler in originals.items():
                signal.signal(s, handler)


def run() -> None:
    configure_logging()
    settings = load_settings_or_exit(log)
    configure_logging(settings.log_level, settings.service_name)
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.app_port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        timeout_graceful_shutdown=settings.shutdown_timeout_seconds,
    )
    GracefulServer(config, app, settings.shutdown_drain_seconds).run()
    log.info("shutdown complete")
    sys.exit(0)


if __name__ == "__main__":
    run()
