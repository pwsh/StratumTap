"""The ``/api/v1`` router.

Handlers never touch gpsd or chrony; they only read the in-memory snapshots
published by the background collectors. Every handler stamps ``server.t_send``
as the last thing it does, and derives all ``*_age_s`` values from it.
"""

from __future__ import annotations

import socket
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from . import __version__
from .models import (
    ConfigResponse,
    GpsResponse,
    GpsSnapshot,
    HealthResponse,
    HistoryResponse,
    MqttStatus,
    NtpResponse,
    NtpSnapshot,
    NtpSources,
    NtpSourcesResponse,
    RawNmeaResponse,
    SatellitesResponse,
    ServerInfo,
    StatusResponse,
    TimeResponse,
)
from .state import HISTORY_COLUMNS
from .stream import EVENTS, Broadcaster, TooManyClients

NO_STORE = "no-store"

_HOSTNAME = socket.gethostname()


def _display_hostname(request: Request) -> str:
    """Configured display name (``STRATUMTAP_HOSTNAME``) or the machine hostname."""
    return _settings(request).hostname or _HOSTNAME


T0 = Query(default=None, description="Client send time (Unix seconds); echoed as server.t0")


async def _no_store(response: Response) -> None:
    """Attach ``Cache-Control: no-store`` to every JSON response."""
    response.headers["Cache-Control"] = NO_STORE


router = APIRouter(prefix="/api/v1", dependencies=[Depends(_no_store)])


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _store(request: Request):
    return request.app.state.store


def _settings(request: Request):
    return request.app.state.settings


def _broadcaster(request: Request) -> Broadcaster:
    return request.app.state.broadcaster


def _mqtt_status(request: Request) -> MqttStatus:
    publisher = getattr(request.app.state, "mqtt", None)
    if publisher is None:
        return MqttStatus()
    return MqttStatus(**publisher.status())


def _loop_lag_ms(request: Request) -> float:
    monitor = getattr(request.app.state, "loopmon", None)
    return monitor.max_lag_ms() if monitor is not None else 0.0


def _t_recv(request: Request) -> float:
    value = getattr(request.state, "t_recv", None)
    return float(value) if isinstance(value, (int, float)) else time.time()


def server_info(request: Request, t0: float | None, t_send: float | None = None) -> ServerInfo:
    """Build the ``server`` block. Call this LAST, just before returning."""
    if t_send is None:
        t_send = time.time()
    store = _store(request)
    return ServerInfo(
        t_recv=_t_recv(request),
        t_send=t_send,
        t0=t0,
        hostname=_display_hostname(request),
        version=__version__,
        demo=bool(_settings(request).demo),
        uptime_s=store.uptime_s(t_send),
    )


def _aged(snapshot, t_send: float):
    """Return *snapshot* with ``age_s`` filled in relative to *t_send*."""
    if snapshot.collected_at is None:
        return snapshot
    return snapshot.model_copy(update={"age_s": t_send - snapshot.collected_at})


def _gps_aged(gps: GpsSnapshot, t_send: float) -> GpsSnapshot:
    """Fill in ``age_s``, ``fix.time_age_s`` and ``cgps_time_offset_text``."""
    update: dict = {}
    if gps.collected_at is not None:
        update["age_s"] = t_send - gps.collected_at
    if gps.fix.time_unix is not None:
        time_age = t_send - gps.fix.time_unix
        update["fix"] = gps.fix.model_copy(update={"time_age_s": time_age})
        update["cgps_time_offset_text"] = f"{time_age:.9f} s"
    return gps.model_copy(update=update) if update else gps


def _ntp(request: Request, t_send: float) -> NtpSnapshot:
    return _aged(_store(request).ntp, t_send)


def _sources(request: Request, t_send: float) -> NtpSources:
    return _aged(_store(request).ntp_sources, t_send)


def _gps(request: Request, t_send: float) -> GpsSnapshot:
    return _gps_aged(_store(request).gps, t_send)


def _raw_text(body: str | None, what: str, error: str | None) -> PlainTextResponse:
    if body is None:
        detail = error or "not collected yet"
        body = f"{what} unavailable: {detail}\n"
    return PlainTextResponse(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": NO_STORE},
    )


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------


@router.get("/time", response_model=TimeResponse, summary="Cheap timestamp exchange")
async def get_time(request: Request, t0: float | None = T0) -> TimeResponse:
    """Smallest possible response for the browser's NTP-style clock sync."""
    ntp = _store(request).ntp
    return TimeResponse(
        ntp_synchronized=bool(ntp.available and ntp.synchronized),
        ntp_system_offset_s=ntp.system_offset_s if ntp.available else None,
        ntp_stratum=ntp.stratum if ntp.available else None,
        server=server_info(request, t0),
    )


@router.get("/status", response_model=StatusResponse, summary="Everything")
async def get_status(request: Request, t0: float | None = T0) -> StatusResponse:
    """``server`` + ``ntp`` + ``gps`` (including satellites)."""
    t_send = time.time()
    return StatusResponse(
        ntp=_ntp(request, t_send),
        gps=_gps(request, t_send),
        server=server_info(request, t0, t_send),
    )


@router.get("/ntp", response_model=NtpResponse, summary="chrony tracking")
async def get_ntp(request: Request, t0: float | None = T0) -> NtpResponse:
    """``server`` + ``ntp``."""
    t_send = time.time()
    return NtpResponse(ntp=_ntp(request, t_send), server=server_info(request, t0, t_send))


@router.get("/ntp/sources", response_model=NtpSourcesResponse, summary="chrony sources")
async def get_ntp_sources(request: Request, t0: float | None = T0) -> NtpSourcesResponse:
    """``server`` + parsed ``chronyc sources`` / ``sourcestats``."""
    t_send = time.time()
    return NtpSourcesResponse(
        ntp_sources=_sources(request, t_send),
        server=server_info(request, t0, t_send),
    )


@router.get("/gps", response_model=GpsResponse, summary="gpsd snapshot")
async def get_gps(request: Request, t0: float | None = T0) -> GpsResponse:
    """``server`` + ``gps``."""
    t_send = time.time()
    return GpsResponse(gps=_gps(request, t_send), server=server_info(request, t0, t_send))


@router.get("/gps/satellites", response_model=SatellitesResponse, summary="Satellites only")
async def get_satellites(request: Request, t0: float | None = T0) -> SatellitesResponse:
    """``server`` + ``satellites``."""
    t_send = time.time()
    return SatellitesResponse(
        satellites=_store(request).gps.satellites,
        server=server_info(request, t0, t_send),
    )


@router.get("/history", response_model=None, summary="History ring buffer")
async def get_history(
    request: Request,
    seconds: float = Query(3600.0, ge=0.0, le=30 * 24 * 3600.0),
    max_points: int = Query(720, alias="max", ge=1, le=100_000),
    format: str = Query("json", pattern="^(json|csv)$"),
    t0: float | None = T0,
):
    """Downsampled server-side history; ``format=csv`` streams a CSV attachment."""
    store = _store(request)
    settings = _settings(request)
    if format == "csv":
        return StreamingResponse(
            store.history_csv(seconds, max_points),
            media_type="text/csv; charset=utf-8",
            headers={
                "Cache-Control": NO_STORE,
                "Content-Disposition": 'attachment; filename="stratumtap-history.csv"',
            },
        )
    rows = store.history_rows(seconds, max_points)
    return HistoryResponse(
        interval_s=float(settings.history_interval_s),
        requested_seconds=float(seconds),
        points=len(rows),
        columns=list(HISTORY_COLUMNS),
        rows=rows,
        server=server_info(request, t0),
    )


@router.get("/raw/chronyc/tracking", response_class=PlainTextResponse, summary="Raw tracking")
async def get_raw_tracking(request: Request) -> PlainTextResponse:
    """Verbatim ``chronyc tracking`` output."""
    ntp = _store(request).ntp
    return _raw_text(ntp.raw, "chronyc tracking", ntp.error)


@router.get("/raw/chronyc/sources", response_class=PlainTextResponse, summary="Raw sources")
async def get_raw_sources(request: Request) -> PlainTextResponse:
    """Verbatim ``chronyc sources -v`` output."""
    src = _store(request).ntp_sources
    return _raw_text(src.raw_sources, "chronyc sources -v", src.error)


@router.get("/raw/chronyc/sourcestats", response_class=PlainTextResponse, summary="Raw sourcestats")
async def get_raw_sourcestats(request: Request) -> PlainTextResponse:
    """Verbatim ``chronyc sourcestats -v`` output."""
    src = _store(request).ntp_sources
    return _raw_text(src.raw_sourcestats, "chronyc sourcestats -v", src.error)


@router.get("/raw/gpsd", summary="Last raw gpsd message per class")
async def get_raw_gpsd(request: Request) -> JSONResponse:
    """The last message received for each gpsd ``class`` (missing classes omitted)."""
    return JSONResponse(
        dict(_store(request).raw_gpsd),
        headers={"Cache-Control": NO_STORE},
    )


@router.get("/raw/nmea", response_model=RawNmeaResponse, summary="Raw NMEA ring buffer")
async def get_raw_nmea(
    request: Request,
    n: int = Query(200, ge=1),
    t0: float | None = T0,
) -> RawNmeaResponse:
    """The newest *n* raw sentences (oldest first) plus the current sentence rate."""
    store = _store(request)
    ring_size = store.nmea_ring.maxlen or 0
    lines = store.nmea_lines(min(int(n), ring_size or int(n)))
    return RawNmeaResponse(
        count=len(lines),
        ring_size=ring_size,
        rate_per_s=round(store.nmea_rate(), 2),
        lines=lines,
        server=server_info(request, t0),
    )


def _parse_events(events: str) -> set[str]:
    """Validate the ``events`` query parameter against the known event names."""
    wanted = {part.strip() for part in events.split(",") if part.strip()}
    unknown = sorted(wanted - set(EVENTS))
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown stream event(s): {', '.join(unknown)}",
        )
    if not wanted:
        raise HTTPException(status_code=400, detail="no stream events requested")
    return wanted


def _too_many_clients() -> JSONResponse:
    return JSONResponse(
        {"detail": "too many stream clients"},
        status_code=503,
        headers={"Cache-Control": NO_STORE},
    )


def _status_payload(request: Request) -> dict:
    """The ``/status`` body, rebuilt at each stream tick."""
    t_send = time.time()
    return StatusResponse(
        ntp=_ntp(request, t_send),
        gps=_gps(request, t_send),
        server=server_info(request, None, t_send),
    ).model_dump(mode="json")


@router.get("/stream", response_model=None, summary="Server-Sent Events stream")
async def get_stream(
    request: Request,
    events: str = Query("nmea,gpsd", description="Comma list: nmea,gpsd,ntp,status"),
    status_interval: float = Query(2.0, ge=1.0, le=60.0),
):
    """Push raw gpsd/NMEA/chrony data as SSE.

    A client that cannot keep up loses its oldest queued events, never the
    producers' time: publishing is synchronous and bounded (contract rule 1).
    """
    broadcaster = _broadcaster(request)
    wanted = _parse_events(events)
    try:
        sub = broadcaster.subscribe(wanted)
    except TooManyClients:
        return _too_many_clients()
    generator = broadcaster.sse_generator(
        sub,
        request=request,
        status_provider=(lambda: _status_payload(request)) if "status" in wanted else None,
        status_interval=status_interval,
        server_info_provider=lambda: server_info(request, None).model_dump(mode="json"),
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": NO_STORE,
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/stream/nmea.txt", response_model=None, summary="Raw NMEA text stream")
async def get_stream_nmea_text(request: Request):
    """Chunked ``text/plain`` of raw NMEA lines (``curl -N ... > capture.nmea``)."""
    broadcaster = _broadcaster(request)
    try:
        sub = broadcaster.subscribe({"nmea"})
    except TooManyClients:
        return _too_many_clients()
    return StreamingResponse(
        broadcaster.raw_generator(sub, request=request),
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": NO_STORE,
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/config", response_model=ConfigResponse, summary="Non-secret UI config")
async def get_config(request: Request, t0: float | None = T0) -> ConfigResponse:
    """Everything the SPA needs to configure itself."""
    settings = _settings(request)
    return ConfigResponse(
        default_refresh_s=int(settings.default_refresh_s),
        refresh_choices_s=settings.refresh_choices,
        tile_url=settings.tile_url,
        tile_attribution=settings.tile_attribution,
        hostname=_display_hostname(request),
        demo=bool(settings.demo),
        history_interval_s=float(settings.history_interval_s),
        history_size=int(settings.history_size),
        version=__version__,
        server=server_info(request, t0),
    )


@router.get("/health", response_model=None, summary="Health flags")
async def get_health(
    request: Request,
    strict: int = Query(0, ge=0, le=1),
    t0: float | None = T0,
):
    """Always 200 unless ``?strict=1`` and something is wrong (then 503)."""
    store = _store(request)
    ntp = store.ntp
    gps = store.gps
    ntp_ok = bool(ntp.available and ntp.synchronized)
    gpsd_connected = bool(gps.connected)
    gps_fix = bool(gps.fix.mode is not None and gps.fix.mode >= 2)
    payload = HealthResponse(
        ok=ntp_ok and gpsd_connected and gps_fix,
        ntp_ok=ntp_ok,
        gpsd_connected=gpsd_connected,
        gps_fix=gps_fix,
        loop_lag_ms=_loop_lag_ms(request),
        stream_clients=_broadcaster(request).client_count,
        mqtt=_mqtt_status(request),
        server=server_info(request, t0),
    )
    status_code = 503 if (strict and not payload.ok) else 200
    return JSONResponse(
        payload.model_dump(mode="json"),
        status_code=status_code,
        headers={"Cache-Control": NO_STORE},
    )
