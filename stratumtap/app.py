"""Application factory: settings, collectors, router, static SPA, timing middleware."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .api import router as api_router
from .chrony import ChronyCollector
from .config import Settings, get_settings
from .demo import DemoSource
from .gpsd import GpsdClient
from .loopmon import LoopMonitor
from .mqtt import MqttPublisher
from .state import StateStore
from .stream import Broadcaster

log = logging.getLogger("stratumtap.app")

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
API_PREFIX = "/api/"


class TimingMiddleware:
    """Pure-ASGI middleware stamping ``request.state.t_recv`` as early as possible."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        """Record the arrival wall-clock time on the request scope."""
        if scope["type"] == "http":
            state = scope.get("state")
            if state is None:
                state = {}
                scope["state"] = state
            state["t_recv"] = time.time()
        await self.app(scope, receive, send)


class StaticNoCacheMiddleware:
    """Make browsers revalidate static assets so a redeploy is picked up immediately.

    StaticFiles already emits ETag/Last-Modified, so revalidation is a cheap 304.
    API responses set their own ``Cache-Control: no-store`` and are left alone.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        """Add ``Cache-Control: no-cache`` to non-API responses lacking one."""
        if scope["type"] != "http" or scope.get("path", "").startswith(API_PREFIX):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-cache"))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application."""
    settings = settings or get_settings()
    store = StateStore(settings)
    broadcaster = Broadcaster(settings)
    loopmon = LoopMonitor()
    mqtt = MqttPublisher(settings, store, version=__version__)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        sources: list = []
        if settings.demo:
            sources.append(DemoSource(settings, store, broadcaster=broadcaster))
        else:
            sources.append(ChronyCollector(settings, store, broadcaster=broadcaster))
            sources.append(GpsdClient(settings, store, broadcaster=broadcaster))
        app.state.sources = sources
        for source in sources:
            try:
                await source.start()
            except Exception:  # pragma: no cover - defensive
                log.exception("failed to start %s", type(source).__name__)
        await store.start_sampler()
        await loopmon.start()
        try:
            await mqtt.start()
        except Exception:  # pragma: no cover - defensive
            log.exception("failed to start the MQTT publisher")
        try:
            yield
        finally:
            try:
                await mqtt.stop()
            except Exception:  # pragma: no cover - defensive
                log.exception("failed to stop the MQTT publisher")
            await loopmon.stop()
            await store.stop_sampler()
            for source in reversed(sources):
                try:
                    await source.stop()
                except Exception:  # pragma: no cover - defensive
                    log.exception("failed to stop %s", type(source).__name__)

    app = FastAPI(
        title="StratumTap",
        version=__version__,
        description="Web front end and JSON API for a GPS-disciplined chrony NTP server.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.broadcaster = broadcaster
    app.state.loopmon = loopmon
    app.state.mqtt = mqtt

    origins = settings.cors_origin_list
    if origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "OPTIONS"],
            allow_headers=["*"],
        )

    app.add_middleware(StaticNoCacheMiddleware)
    # Added last so that it wraps everything else and sees the request first.
    app.add_middleware(TimingMiddleware)

    app.include_router(api_router)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        """404s under ``/api/`` are JSON; elsewhere they fall back to the SPA."""
        spa = (
            exc.status_code == 404
            and not request.url.path.startswith(API_PREFIX)
            and request.method in ("GET", "HEAD")
            and INDEX_HTML.is_file()
        )
        if spa:
            return FileResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})
        detail = exc.detail if exc.status_code != 404 else "Not found"
        return JSONResponse(
            {"detail": detail},
            status_code=exc.status_code,
            headers={"Cache-Control": "no-store"},
        )

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    else:  # pragma: no cover - only when the frontend has not been built yet
        log.warning("static directory %s is missing; serving API only", STATIC_DIR)

    return app
