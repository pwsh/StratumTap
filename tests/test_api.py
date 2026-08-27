"""Full API surface in demo mode, driven through httpx's ASGI transport."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import httpx
import pytest

from stratumtap import __version__
from stratumtap.app import create_app
from stratumtap.config import Settings
from stratumtap.models import (
    ConfigResponse,
    GpsResponse,
    HealthResponse,
    HistoryResponse,
    NtpResponse,
    NtpSourcesResponse,
    SatellitesResponse,
    StatusResponse,
    TimeResponse,
)
from stratumtap.state import HISTORY_COLUMNS

INDEX_HTML = Path(__file__).resolve().parents[1] / "stratumtap" / "static" / "index.html"

ENDPOINTS = [
    "/api/v1/time",
    "/api/v1/status",
    "/api/v1/ntp",
    "/api/v1/ntp/sources",
    "/api/v1/gps",
    "/api/v1/gps/satellites",
    "/api/v1/history",
    "/api/v1/config",
    "/api/v1/health",
]


@pytest.fixture
async def app():
    application = create_app(
        Settings(_env_file=None, demo=True, history_interval_s=0.05, history_size=500)
    )
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# --------------------------------------------------------------------------
# server block
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ENDPOINTS)
async def test_every_endpoint_returns_a_server_block(client, path):
    resp = await client.get(path)
    assert resp.status_code == 200
    body = resp.json()
    server = body["server"]
    assert server["t_recv"] <= server["t_send"]
    assert server["hostname"]
    assert server["version"] == __version__
    assert server["demo"] is True
    assert server["uptime_s"] >= 0
    assert server["t0"] is None


@pytest.mark.parametrize("path", ENDPOINTS)
async def test_t0_is_echoed(client, path):
    resp = await client.get(path, params={"t0": 1787753871.125})
    assert resp.status_code == 200
    assert resp.json()["server"]["t0"] == pytest.approx(1787753871.125)


@pytest.mark.parametrize(
    "path",
    ENDPOINTS
    + [
        "/api/v1/raw/gpsd",
        "/api/v1/raw/chronyc/tracking",
        "/api/v1/raw/chronyc/sources",
        "/api/v1/raw/chronyc/sourcestats",
        "/api/v1/history?format=csv",
    ],
)
async def test_no_store_header(client, path):
    resp = await client.get(path)
    assert resp.headers["cache-control"] == "no-store"


# --------------------------------------------------------------------------
# individual endpoints, validated against the models
# --------------------------------------------------------------------------


async def test_time(client):
    resp = await client.get("/api/v1/time")
    model = TimeResponse.model_validate(resp.json())
    assert model.ntp_synchronized is True
    assert model.ntp_stratum == 1
    assert model.ntp_system_offset_s is not None


async def test_status_validates_and_is_complete(client):
    resp = await client.get("/api/v1/status")
    model = StatusResponse.model_validate(resp.json())

    ntp = model.ntp
    assert ntp.available is True
    assert ntp.error is None
    assert ntp.reference_id == "50505300"
    assert ntp.reference_name == "PPS"
    assert ntp.stratum == 1
    assert ntp.leap_status == "Normal"
    assert ntp.synchronized is True
    assert ntp.age_s is not None and ntp.age_s >= 0
    assert ntp.raw and "Reference ID" in ntp.raw

    gps = model.gps
    assert gps.available is True
    assert gps.connected is True
    assert gps.gpsd_version == "3.22"
    assert gps.device.path == "/dev/ttyAMA0"
    assert gps.fix.mode in (2, 3)
    assert gps.fix.fix_text in ("2D DGPS FIX", "3D DGPS FIX")
    assert gps.fix.fix_age_s is not None
    assert gps.fix.time_unix is not None
    assert gps.fix.time_age_s is not None
    assert gps.position.grid_square == "IO91xl94"
    assert gps.satellites.seen == 12
    assert gps.satellites.list
    assert [d.path for d in gps.devices] == ["/dev/ttyAMA0", "/dev/pps0"]
    assert gps.device.path == "/dev/ttyAMA0"
    assert gps.device.driver == "MTK-3301"
    assert gps.device.subtype
    assert gps.device.bps == 9600
    assert gps.device.cycle_s == pytest.approx(1.0)
    assert gps.devices[1].driver == "PPS"
    assert gps.devices[1].bps is None
    assert gps.gst is not None
    assert 1.5 <= gps.gst.major_m <= 5.0
    assert 1.0 <= gps.gst.minor_m <= 4.0
    assert gps.gst.orient_deg is not None
    assert gps.gst.time_unix is not None
    assert gps.time_offset.source == "PPS"  # PPS wins while both are fresh
    assert gps.time_offset.toff_offset_s is not None
    assert gps.cgps_time_offset_text is not None
    assert gps.cgps_time_offset_text.endswith(" s")
    assert len(gps.cgps_time_offset_text.split(".")[1].split(" ")[0]) == 9


async def test_status_ages_are_relative_to_t_send(client):
    body = (await client.get("/api/v1/status")).json()
    t_send = body["server"]["t_send"]
    assert body["ntp"]["age_s"] == pytest.approx(t_send - body["ntp"]["collected_at"], abs=1e-6)
    assert body["gps"]["age_s"] == pytest.approx(t_send - body["gps"]["collected_at"], abs=1e-6)
    assert body["gps"]["fix"]["time_age_s"] == pytest.approx(
        t_send - body["gps"]["fix"]["time_unix"], abs=1e-6
    )


async def test_ntp_and_sources(client):
    NtpResponse.model_validate((await client.get("/api/v1/ntp")).json())
    model = NtpSourcesResponse.model_validate((await client.get("/api/v1/ntp/sources")).json())
    src = model.ntp_sources
    assert src.available is True
    assert [s.name for s in src.sources] == ["PPS", "time.cloudflare.com"]
    assert src.sources[0].mode_text == "refclock"
    assert src.sources[0].state_text == "current best"
    assert src.sources[0].poll == 3
    assert src.sources[0].poll_interval_s == 8
    assert src.sources[1].poll_interval_s == 64
    assert src.sources[0].reach == 255
    assert src.sources[0].reach_octal == "377"
    assert [s.name for s in src.sourcestats] == ["PPS", "time.cloudflare.com"]
    assert src.raw_sources and "Name/IP address" in src.raw_sources
    assert src.raw_sourcestats and "Std Dev" in src.raw_sourcestats


async def test_gps_and_satellites(client):
    GpsResponse.model_validate((await client.get("/api/v1/gps")).json())
    model = SatellitesResponse.model_validate((await client.get("/api/v1/gps/satellites")).json())
    sats = model.satellites
    assert sats.seen == 12
    assert len(sats.list) == 12
    keys = [(s.gnssid, s.svid, s.prn) for s in sats.list]
    assert keys == sorted(keys)
    assert {s.gnss for s in sats.list} == {"GP", "SB", "GL"}


async def test_config(client, app):
    model = ConfigResponse.model_validate((await client.get("/api/v1/config")).json())
    assert model.default_refresh_s == 2
    assert model.refresh_choices_s == [1, 2, 5, 10, 30, 60]
    assert model.tile_url.startswith("https://")
    assert model.tile_attribution
    assert model.demo is True
    assert model.history_interval_s == pytest.approx(0.05)
    assert model.history_size == 500
    assert model.version == __version__


async def test_health(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    model = HealthResponse.model_validate(resp.json())
    assert model.ntp_ok is True
    assert model.gpsd_connected is True
    assert model.gps_fix is True
    assert model.ok is True


async def test_health_strict_returns_503_when_degraded(client, app):
    app.state.store.gps.connected = False
    resp = await client.get("/api/v1/health", params={"strict": 1})
    assert resp.status_code == 503
    body = resp.json()
    assert body["ok"] is False
    assert body["gpsd_connected"] is False
    # Without strict it is still a 200 so the UI can show a degraded state.
    assert (await client.get("/api/v1/health")).status_code == 200


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------


async def test_history_json(client, app):
    store = app.state.store
    for _ in range(20):
        store.sample()
    resp = await client.get("/api/v1/history", params={"seconds": 60, "max": 10})
    model = HistoryResponse.model_validate(resp.json())
    assert model.columns == HISTORY_COLUMNS
    assert model.requested_seconds == pytest.approx(60.0)
    assert model.interval_s == pytest.approx(0.05)
    assert 0 < model.points <= 10
    assert model.points == len(model.rows)
    assert all(len(row) == len(HISTORY_COLUMNS) for row in model.rows)
    assert [r[0] for r in model.rows] == sorted(r[0] for r in model.rows)


async def test_history_csv(client, app):
    store = app.state.store
    for _ in range(5):
        store.sample()
    resp = await client.get("/api/v1/history", params={"seconds": 600, "format": "csv"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == 'attachment; filename="stratumtap-history.csv"'
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0] == ["t_iso", *HISTORY_COLUMNS]
    assert len(rows) >= 2
    assert rows[1][0].endswith("Z")


async def test_history_rejects_bad_format(client):
    assert (await client.get("/api/v1/history", params={"format": "xml"})).status_code == 422


# --------------------------------------------------------------------------
# raw
# --------------------------------------------------------------------------


async def test_raw_chronyc(client):
    tracking = await client.get("/api/v1/raw/chronyc/tracking")
    assert tracking.headers["content-type"].startswith("text/plain")
    assert "Reference ID" in tracking.text
    assert "Leap status     : Normal" in tracking.text
    assert "Name/IP address" in (await client.get("/api/v1/raw/chronyc/sources")).text
    assert "Std Dev" in (await client.get("/api/v1/raw/chronyc/sourcestats")).text


async def test_raw_gpsd(client):
    body = (await client.get("/api/v1/raw/gpsd")).json()
    assert set(body) >= {"VERSION", "DEVICES", "TPV", "SKY", "PPS", "TOFF"}
    assert body["TPV"]["class"] == "TPV"
    assert body["SKY"]["satellites"]
    json.dumps(body)  # must be plain JSON-serialisable


# --------------------------------------------------------------------------
# errors and the SPA fallback
# --------------------------------------------------------------------------


async def test_unknown_api_path_is_json_404(client):
    resp = await client.get("/api/v1/nope")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"detail": "Not found"}


async def test_unknown_nested_api_path_is_json_404(client):
    resp = await client.get("/api/v1/raw/chronyc/nope")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not found"}


async def test_openapi_and_docs(client):
    schema = (await client.get("/openapi.json")).json()
    assert schema["info"]["title"] == "StratumTap"
    assert schema["info"]["version"] == __version__
    for path in ENDPOINTS:
        assert path in schema["paths"]
    assert (await client.get("/docs")).status_code == 200


@pytest.mark.skipif(not INDEX_HTML.is_file(), reason="frontend index.html not built yet")
async def test_spa_fallback_serves_index(client):
    root = await client.get("/")
    assert root.status_code == 200
    assert root.headers["content-type"].startswith("text/html")

    deep = await client.get("/detail/whatever")
    assert deep.status_code == 200
    assert deep.headers["content-type"].startswith("text/html")
    assert deep.text == INDEX_HTML.read_text()


@pytest.mark.skipif(INDEX_HTML.is_file(), reason="frontend index.html exists")
async def test_missing_spa_gives_json_404(client):
    resp = await client.get("/nope")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not found"}


async def test_static_vendor_files_are_served(client):
    resp = await client.get("/vendor/leaflet/leaflet.css")
    assert resp.status_code == 200
    assert "leaflet" in resp.text.lower()
