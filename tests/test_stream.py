"""Broadcaster fan-out, SSE framing and the streaming endpoints.

The non-starvation rules from ``docs/api-contract.md`` are what these tests exist for:
a slow client must lose *its own* events, never delay a producer, and it must
always be unsubscribed when its generator goes away.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import httpx
import pytest

from stratumtap import demo as demo_mod
from stratumtap import stream as stream_mod
from stratumtap.app import create_app
from stratumtap.config import Settings
from stratumtap.loopmon import LoopMonitor
from stratumtap.models import HealthResponse, RawNmeaResponse
from stratumtap.stream import Broadcaster, Subscriber, TooManyClients, sse_frame
from tests.conftest import wait_for


def make_broadcaster(max_clients: int = 4, queue: int = 8) -> Broadcaster:
    return Broadcaster(Settings(_env_file=None, stream_max_clients=max_clients, stream_queue=queue))


def drain(sub: Subscriber) -> list[tuple[str, str]]:
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


# --------------------------------------------------------------------------
# broadcaster
# --------------------------------------------------------------------------


async def test_publish_reaches_only_the_subscribed_events():
    b = make_broadcaster()
    nmea = b.subscribe({"nmea"})
    both = b.subscribe({"nmea", "ntp"})

    assert b.publish("nmea", {"line": "$GPRMC"}) == 2
    assert b.publish("ntp", {"stratum": 1}) == 1
    assert b.publish("gpsd", {"class": "TPV"}) == 0

    assert [event for event, _ in drain(nmea)] == ["nmea"]
    assert [event for event, _ in drain(both)] == ["nmea", "ntp"]
    assert b.client_count == 2


async def test_payload_is_serialised_once_per_publish():
    b = make_broadcaster()
    a = b.subscribe({"nmea"})
    c = b.subscribe({"nmea"})
    b.publish("nmea", {"line": "$GPRMC,1", "checksum_ok": True})
    _, first = a.queue.get_nowait()
    _, second = c.queue.get_nowait()
    # The very same string object, i.e. json.dumps ran once, not once per client.
    assert first is second
    assert first == '{"line":"$GPRMC,1","checksum_ok":true}'


async def test_string_payloads_pass_through_unencoded():
    b = make_broadcaster()
    sub = b.subscribe({"nmea"})
    b.publish("nmea", "$GPRMC,123519,A*6A")
    assert sub.queue.get_nowait() == ("nmea", "$GPRMC,123519,A*6A")


async def test_full_queue_drops_the_oldest_event_and_counts_it():
    b = make_broadcaster(queue=3)
    sub = b.subscribe({"nmea"})
    for i in range(5):
        b.publish("nmea", {"i": i})

    assert sub.queue.qsize() == 3
    assert sub.dropped == 2
    assert sub.sent == 5
    # The newest events survive: stale raw data is worthless to a live view.
    assert [json.loads(data)["i"] for _, data in drain(sub)] == [2, 3, 4]

    # After draining, the subscriber is healthy again and keeps its counters.
    b.publish("nmea", {"i": 99})
    assert sub.dropped == 2
    assert sub.sent == 6


async def test_a_stalled_subscriber_does_not_starve_the_others():
    b = make_broadcaster(queue=2)
    stalled = b.subscribe({"nmea"})
    healthy = b.subscribe({"nmea"})
    for i in range(6):
        b.publish("nmea", {"i": i})
        # The healthy client keeps up; the stalled one never reads.
        healthy.queue.get_nowait()
    assert stalled.dropped == 4
    assert healthy.dropped == 0


async def test_subscribe_is_capped():
    b = make_broadcaster(max_clients=2)
    first = b.subscribe({"nmea"})
    b.subscribe({"nmea"})
    with pytest.raises(TooManyClients):
        b.subscribe({"nmea"})
    # A departing client frees a slot again.
    b.unsubscribe(first)
    assert b.client_count == 1
    b.subscribe({"nmea"})
    assert b.client_count == 2


async def test_unsubscribe_is_idempotent():
    b = make_broadcaster()
    sub = b.subscribe({"nmea"})
    b.unsubscribe(sub)
    b.unsubscribe(sub)
    assert b.client_count == 0
    # Publishing to nobody is a no-op, not an error.
    assert b.publish("nmea", {"x": 1}) == 0


async def test_publish_to_a_full_queue_is_fast():
    """Rule 1: the producer never waits for a consumer (1000 publishes < 50 ms)."""
    b = make_broadcaster(queue=10)
    sub = b.subscribe({"nmea"})
    for _ in range(20):  # fill it up first
        b.publish("nmea", {"warm": True})
    assert sub.queue.full()

    payload = {"t": 1787758473.0123, "line": "$GPRMC,155953.000,A,4402.7,N*6A", "type": "RMC"}
    started = time.perf_counter()
    for _ in range(1000):
        b.publish("nmea", payload)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.05, f"1000 publishes took {elapsed * 1000:.1f} ms"
    assert sub.dropped >= 1000


# --------------------------------------------------------------------------
# SSE generator
# --------------------------------------------------------------------------


def parse_frame(chunk: str) -> dict:
    """Turn one SSE frame into ``{"id":…, "event":…, "data":…}``."""
    out: dict = {"comment": None}
    for line in chunk.strip("\n").split("\n"):
        if line.startswith(":"):
            out["comment"] = line[1:].strip()
        elif line.startswith("id: "):
            out["id"] = int(line[4:])
        elif line.startswith("event: "):
            out["event"] = line[7:]
        elif line.startswith("data: "):
            out["data"] = line[6:]
    return out


def test_sse_frame_shape():
    frame = sse_frame(7, "nmea", '{"line":"$GPRMC"}')
    assert frame == 'id: 7\nevent: nmea\ndata: {"line":"$GPRMC"}\n\n'
    assert frame.endswith("\n\n")
    # Multi-line data becomes several data: lines, as the SSE spec requires.
    assert sse_frame(1, "x", "a\nb") == "id: 1\nevent: x\ndata: a\ndata: b\n\n"


async def test_hello_comes_first_then_queued_events():
    b = make_broadcaster()
    sub = b.subscribe({"nmea", "gpsd"})
    gen = b.sse_generator(sub, server_info_provider=lambda: {"hostname": "pi"})
    try:
        hello = parse_frame(await gen.__anext__())
        assert hello["event"] == "hello"
        assert hello["id"] == 1
        payload = json.loads(hello["data"])
        assert payload["client_id"] == sub.id
        assert payload["events"] == ["nmea", "gpsd"]
        assert payload["queue"] == sub.queue.maxsize
        assert payload["server"] == {"hostname": "pi"}

        b.publish("nmea", {"line": "$GPRMC,1"})
        b.publish("gpsd", {"class": "TPV"})
        first = parse_frame(await gen.__anext__())
        second = parse_frame(await gen.__anext__())
        assert (first["event"], first["id"]) == ("nmea", 2)
        assert (second["event"], second["id"]) == ("gpsd", 3)
        assert json.loads(first["data"])["line"] == "$GPRMC,1"
    finally:
        await gen.aclose()


async def test_generator_unsubscribes_on_close():
    b = make_broadcaster()
    sub = b.subscribe({"nmea"})
    gen = b.sse_generator(sub)
    await gen.__anext__()
    assert b.client_count == 1
    await gen.aclose()
    assert b.client_count == 0


async def test_generator_unsubscribes_when_the_task_is_cancelled():
    b = make_broadcaster()
    sub = b.subscribe({"nmea"})
    started = asyncio.Event()

    async def consume():
        async for _ in b.sse_generator(sub):
            started.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert b.client_count == 0


async def test_stats_event_is_emitted_periodically(monkeypatch):
    monkeypatch.setattr(stream_mod, "STATS_INTERVAL_S", 0.05)
    monkeypatch.setattr(stream_mod, "KEEPALIVE_INTERVAL_S", 10.0)
    b = make_broadcaster(queue=2)
    sub = b.subscribe({"nmea"})
    for _ in range(4):  # provoke a drop so stats has something to report
        b.publish("nmea", {"x": 1})
    gen = b.sse_generator(sub)
    try:
        await gen.__anext__()  # hello
        frames = [parse_frame(await gen.__anext__()) for _ in range(3)]
        assert [f["event"] for f in frames[:2]] == ["nmea", "nmea"]
        stats = frames[2]
        assert stats["event"] == "stats"
        data = json.loads(stats["data"])
        assert data["dropped"] == 2
        assert data["sent"] == 4
        assert data["queue_len"] == 0
        assert data["clients"] == 1
        assert isinstance(data["t"], float)
    finally:
        await gen.aclose()


async def test_keepalive_comment_after_silence(monkeypatch):
    monkeypatch.setattr(stream_mod, "STATS_INTERVAL_S", 10.0)
    monkeypatch.setattr(stream_mod, "KEEPALIVE_INTERVAL_S", 0.05)
    b = make_broadcaster()
    sub = b.subscribe({"nmea"})

    class NeverDisconnected:
        def __init__(self):
            self.checks = 0

        async def is_disconnected(self):
            self.checks += 1
            return False

    request = NeverDisconnected()
    gen = b.sse_generator(sub, request=request)
    try:
        await gen.__anext__()  # hello
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert chunk == ": keepalive\n\n"
        assert request.checks >= 1  # rule 4: disconnects are noticed on the tick
    finally:
        await gen.aclose()


async def test_generator_stops_when_the_client_has_disconnected(monkeypatch):
    monkeypatch.setattr(stream_mod, "KEEPALIVE_INTERVAL_S", 0.02)
    b = make_broadcaster()
    sub = b.subscribe({"nmea"})

    class Gone:
        async def is_disconnected(self):
            return True

    frames = [chunk async for chunk in b.sse_generator(sub, request=Gone())]
    assert len(frames) == 1  # hello only, then the generator returns
    assert b.client_count == 0


async def test_status_event_is_emitted_on_its_own_interval(monkeypatch):
    monkeypatch.setattr(stream_mod, "STATS_INTERVAL_S", 10.0)
    monkeypatch.setattr(stream_mod, "KEEPALIVE_INTERVAL_S", 10.0)
    b = make_broadcaster()
    sub = b.subscribe({"status"})
    calls = []

    def status_provider():
        calls.append(time.perf_counter())
        return {"n": len(calls)}

    gen = b.sse_generator(sub, status_provider=status_provider, status_interval=0.05)
    try:
        await gen.__anext__()  # hello
        first = parse_frame(await asyncio.wait_for(gen.__anext__(), timeout=2.0))
        second = parse_frame(await asyncio.wait_for(gen.__anext__(), timeout=2.0))
        assert first["event"] == "status"
        assert json.loads(first["data"]) == {"n": 1}
        assert json.loads(second["data"]) == {"n": 2}
        assert second["id"] == first["id"] + 1
    finally:
        await gen.aclose()


async def test_raw_generator_yields_only_nmea_lines():
    b = make_broadcaster()
    sub = b.subscribe({"nmea"})
    gen = b.raw_generator(sub)
    try:
        b.publish("nmea", {"t": 1.0, "line": "$GPRMC,1*00"})
        b.publish("nmea", {"t": 2.0, "line": "$GPGGA,2*00"})
        assert await asyncio.wait_for(gen.__anext__(), timeout=2.0) == "$GPRMC,1*00\n"
        assert await asyncio.wait_for(gen.__anext__(), timeout=2.0) == "$GPGGA,2*00\n"
    finally:
        await gen.aclose()
    assert b.client_count == 0


# --------------------------------------------------------------------------
# endpoints (demo mode)
# --------------------------------------------------------------------------


class AsgiStream:
    """Read an endless streaming response by driving the ASGI app directly.

    httpx's ASGITransport buffers the whole body before returning, which never
    happens for an SSE stream, so the endpoint tests talk to the app itself.
    """

    def __init__(self, app, path: str) -> None:
        self.app = app
        self.path, _, self.query = path.partition("?")
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.raw_headers: list[tuple[str, str]] = []
        self._chunks: asyncio.Queue = asyncio.Queue()
        self._started = asyncio.Event()
        self._disconnect = asyncio.Event()
        self._body_sent = False
        self._task: asyncio.Task | None = None
        self._buffer = ""

    async def __aenter__(self) -> AsgiStream:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": self.query.encode(),
            "root_path": "",
            "headers": [(b"host", b"testserver"), (b"accept", b"text/event-stream")],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }
        self._task = asyncio.create_task(self.app(scope, self._receive, self._send))
        await asyncio.wait_for(self._started.wait(), timeout=5.0)
        return self

    async def __aexit__(self, *exc) -> None:
        self._disconnect.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _receive(self) -> dict:
        if not self._body_sent:
            self._body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.raw_headers = [
                (k.decode().lower(), v.decode()) for k, v in message.get("headers", [])
            ]
            self.headers = dict(self.raw_headers)
            self._started.set()
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                await self._chunks.put(body.decode())
            if not message.get("more_body", False):
                await self._chunks.put(None)

    async def read_frame(self, timeout: float = 5.0) -> str:
        """Read up to the next blank-line-terminated SSE frame."""
        while "\n\n" not in self._buffer:
            chunk = await asyncio.wait_for(self._chunks.get(), timeout=timeout)
            if chunk is None:
                raise AssertionError("stream ended")
            self._buffer += chunk
        frame, _, self._buffer = self._buffer.partition("\n\n")
        return frame + "\n\n"

    async def read_line(self, timeout: float = 5.0) -> str:
        while "\n" not in self._buffer:
            chunk = await asyncio.wait_for(self._chunks.get(), timeout=timeout)
            if chunk is None:
                raise AssertionError("stream ended")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition("\n")
        return line


@pytest.fixture
async def app(monkeypatch):
    monkeypatch.setattr(demo_mod, "TICK_S", 0.05)
    application = create_app(
        Settings(
            _env_file=None,
            demo=True,
            history_interval_s=0.5,
            history_size=100,
            stream_max_clients=2,
            stream_queue=16,
            nmea_ring=64,
        )
    )
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_stream_endpoint_emits_hello_then_live_events(app):
    async with AsgiStream(app, "/api/v1/stream?events=nmea,gpsd") as stream:
        assert stream.status == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["cache-control"] == "no-store"
        assert stream.headers["x-accel-buffering"] == "no"
        assert stream.headers["connection"] == "keep-alive"
        # exactly one Cache-Control, not one from the handler plus one from the
        # router-wide dependency
        assert [k for k, _ in stream.raw_headers].count("cache-control") == 1

        hello = parse_frame(await stream.read_frame())
        assert hello["event"] == "hello"
        assert hello["id"] == 1
        payload = json.loads(hello["data"])
        assert payload["events"] == ["nmea", "gpsd"]
        assert payload["server"]["demo"] is True

        seen = []
        ids = []
        for _ in range(20):
            frame = parse_frame(await stream.read_frame())
            seen.append(frame["event"])
            ids.append(frame["id"])
            if frame["event"] == "nmea":
                line = json.loads(frame["data"])
                assert line["line"].startswith("$GP")
                assert line["checksum_ok"] is True
                assert line["talker"] == "GP"
            elif frame["event"] == "gpsd":
                msg = json.loads(frame["data"])
                assert msg["class"] in {"TPV", "SKY", "GST", "PPS", "TOFF"}
                assert msg["_t"] > 0
            if "nmea" in seen and "gpsd" in seen:
                break
        assert "nmea" in seen and "gpsd" in seen
        assert ids == list(range(2, 2 + len(ids)))  # ids increment, hello was 1
    assert app.state.broadcaster.client_count == 0


async def test_stream_status_event(app):
    async with AsgiStream(app, "/api/v1/stream?events=status&status_interval=1") as stream:
        await stream.read_frame()  # hello
        frame = parse_frame(await stream.read_frame(timeout=5.0))
        assert frame["event"] == "status"
        body = json.loads(frame["data"])
        assert body["gps"]["available"] is True
        assert body["ntp"]["available"] is True
        assert body["server"]["demo"] is True


async def test_stream_rejects_unknown_events(client):
    resp = await client.get("/api/v1/stream?events=nmea,nope")
    assert resp.status_code == 400
    assert "nope" in resp.json()["detail"]
    resp = await client.get("/api/v1/stream?events=")
    assert resp.status_code == 400


async def test_stream_status_interval_is_bounded(client):
    assert (await client.get("/api/v1/stream?events=status&status_interval=0")).status_code == 422
    assert (await client.get("/api/v1/stream?events=status&status_interval=99")).status_code == 422


async def test_stream_returns_503_over_the_client_cap(app, client):
    async with (
        AsgiStream(app, "/api/v1/stream") as one,
        AsgiStream(app, "/api/v1/stream") as two,
    ):
        await one.read_frame()
        await two.read_frame()
        assert app.state.broadcaster.client_count == 2
        resp = await client.get("/api/v1/stream")
        assert resp.status_code == 503
        assert resp.json() == {"detail": "too many stream clients"}


async def test_stream_nmea_txt_yields_raw_lines(app):
    async with AsgiStream(app, "/api/v1/stream/nmea.txt") as stream:
        assert stream.status == 200
        assert stream.headers["content-type"].startswith("text/plain")
        lines = [await stream.read_line() for _ in range(4)]
    assert all(line.startswith("$GP") and "*" in line for line in lines)
    assert any(line.startswith("$GPRMC") for line in lines)
    assert app.state.broadcaster.client_count == 0


async def test_raw_nmea_endpoint(client):
    resp = await client.get("/api/v1/raw/nmea?n=5")
    model = RawNmeaResponse.model_validate(resp.json())
    assert model.ring_size == 64
    assert 0 < model.count <= 5
    assert model.rate_per_s > 0
    last = model.lines[-1]
    assert last.line.startswith("$GP")
    assert last.talker == "GP"
    assert last.checksum_ok is True
    assert last.t > 0
    assert resp.headers["cache-control"] == "no-store"


async def test_raw_nmea_clamps_n_to_the_ring_size(client):
    resp = await client.get("/api/v1/raw/nmea?n=100000")
    body = resp.json()
    assert body["count"] <= body["ring_size"] == 64
    assert (await client.get("/api/v1/raw/nmea?n=0")).status_code == 422


async def test_health_reports_loop_lag_and_stream_clients(client):
    resp = await client.get("/api/v1/health")
    model = HealthResponse.model_validate(resp.json())
    assert model.loop_lag_ms >= 0.0
    assert model.stream_clients == 0
    assert "loop_lag_ms" in resp.json()
    assert "stream_clients" in resp.json()


async def test_health_counts_open_stream_clients(app, client):
    async with AsgiStream(app, "/api/v1/stream") as stream:
        await stream.read_frame()
        body = (await client.get("/api/v1/health")).json()
        assert body["stream_clients"] == 1


# --------------------------------------------------------------------------
# loop-lag monitor
# --------------------------------------------------------------------------


async def test_loop_monitor_records_overshoot():
    monitor = LoopMonitor(interval_s=0.01, samples=5)
    assert monitor.max_lag_ms() == 0.0
    await monitor.start()
    try:
        await wait_for(lambda: len(monitor.overshoots) >= 3, timeout=2.0)
    finally:
        await monitor.stop()
    assert monitor.max_lag_ms() >= 0.0
    assert all(value >= 0.0 for value in monitor.overshoots)


async def test_loop_monitor_keeps_a_bounded_window():
    monitor = LoopMonitor(interval_s=1.0, samples=3)
    for value in (0.001, 0.050, -0.5, 0.002):
        monitor.record(value)
    assert len(monitor.overshoots) == 3
    assert monitor.max_lag_ms() == 50.0  # the 0.001 sample fell out of the window
    await monitor.stop()  # never started: a no-op
