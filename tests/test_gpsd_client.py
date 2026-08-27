"""End-to-end GpsdClient test against an in-process fake gpsd server."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from pathlib import Path

import pytest

from stratumtap import gpsd as gpsd_mod
from stratumtap.config import Settings
from stratumtap.gpsd import WATCH_COMMAND, GpsdClient
from stratumtap.state import StateStore
from tests.conftest import wait_for
from tests.test_gpsd_fold import DEVICES, SKY, TPV, VERSION

RMC_LINE = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
GGA_LINE = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
BAD_LINE = "$GPVTG,63.51,T,64.40,M,0.04,N,0.08,K,D*00"

PPS = {
    "class": "PPS",
    "device": "/dev/ttyAMA0",
    "real_sec": 1787753871,
    "real_nsec": 0,
    "clock_sec": 1787753871,
    "clock_nsec": 1234,
    "precision": -20,
}


class FakeGpsd:
    """Minimal gpsd: greets with VERSION, records ?WATCH, then streams messages."""

    def __init__(self, messages):
        self.messages = messages
        self.watch_commands: list[str] = []
        self.connections = 0
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self._writers: list[asyncio.StreamWriter] = []
        self.close_after_first = False

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()
        if self.server is not None:
            self.server.close()
            # asyncio.Server.wait_closed() can block forever once every handler
            # has already returned, so never wait on it unbounded here.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.server.wait_closed(), timeout=2.0)

    async def drop_clients(self) -> None:
        """Close every open connection so the client has to reconnect."""
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.append(writer)
        try:
            writer.write((json.dumps(VERSION) + "\r\n").encode())
            await writer.drain()
            line = await reader.readline()
            self.watch_commands.append(line.decode())
            for msg in self.messages:
                # ``str`` entries are raw NMEA sentences, which gpsd interleaves
                # with the JSON on the same socket when watched with "nmea":true.
                payload = msg if isinstance(msg, str) else json.dumps(msg)
                writer.write((payload + "\r\n").encode())
            await writer.drain()
            while await reader.readline():
                pass
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            if writer in self._writers:
                self._writers.remove(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)


@pytest.fixture
async def fake_gpsd():
    server = FakeGpsd([DEVICES, TPV, SKY, PPS])
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def client(fake_gpsd, monkeypatch):
    monkeypatch.setattr(gpsd_mod, "BACKOFF_MIN_S", 0.02)
    monkeypatch.setattr(gpsd_mod, "BACKOFF_MAX_S", 0.05)
    settings = Settings(_env_file=None, gpsd_host="127.0.0.1", gpsd_port=fake_gpsd.port)
    store = StateStore(settings)
    gps_client = GpsdClient(settings, store)
    await gps_client.start()
    try:
        yield gps_client, store
    finally:
        await gps_client.stop()


async def test_client_sends_watch_and_folds_the_stream(fake_gpsd, client):
    gps_client, store = client
    await wait_for(lambda: store.gps.available)

    assert fake_gpsd.watch_commands[0] == WATCH_COMMAND
    assert json.loads(fake_gpsd.watch_commands[0].strip()[len("?WATCH=") : -1]) == {
        "enable": True,
        "json": True,
        "pps": True,
        "nmea": True,
    }

    await wait_for(lambda: store.gps.satellites.seen == 12)
    snap = store.gps
    assert snap.connected is True
    assert snap.error is None
    assert snap.gpsd_version == "3.22"
    assert snap.device.path == "/dev/ttyAMA0"
    assert snap.fix.fix_text == "3D DGPS FIX"
    assert snap.position.grid_square == "EN41er01"
    assert snap.dop.hdop == pytest.approx(0.97)
    assert snap.satellites.used == 9
    assert [s.svid for s in snap.satellites.list] == [5, 10, 133, 71]

    await wait_for(lambda: store.gps.time_offset.source == "PPS")
    assert store.gps.time_offset.offset_s == pytest.approx(1.234e-6, abs=1e-12)

    assert set(store.raw_gpsd) == {"VERSION", "DEVICES", "TPV", "SKY", "PPS"}


async def test_client_reconnects_after_the_server_closes(fake_gpsd, client):
    gps_client, store = client
    await wait_for(lambda: store.gps.available)
    assert fake_gpsd.connections == 1

    await fake_gpsd.drop_clients()
    await wait_for(lambda: fake_gpsd.connections >= 2, timeout=5.0)
    await wait_for(lambda: store.gps.available, timeout=5.0)
    assert len(fake_gpsd.watch_commands) >= 2
    assert store.gps.connected is True
    assert store.gps.fix.mode == 3


async def test_client_reports_connection_refused(monkeypatch):
    monkeypatch.setattr(gpsd_mod, "BACKOFF_MIN_S", 0.02)
    monkeypatch.setattr(gpsd_mod, "BACKOFF_MAX_S", 0.05)
    # Bind and immediately release a port so that nothing is listening on it.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    settings = Settings(_env_file=None, gpsd_host="127.0.0.1", gpsd_port=port)
    store = StateStore(settings)
    gps_client = GpsdClient(settings, store)
    await gps_client.start()
    try:
        await wait_for(lambda: store.gps.error and "refused" in store.gps.error)
    finally:
        await gps_client.stop()
    assert store.gps.connected is False
    assert store.gps.available is False
    assert store.gps.error == f"connection refused (127.0.0.1:{port})"


async def test_client_ignores_undecodable_lines(fake_gpsd, client):
    gps_client, store = client
    await wait_for(lambda: store.gps.available)
    gps_client._handle_line(b"not json at all\r\n")
    gps_client._handle_line(b"\r\n")
    assert store.gps.available is True


async def test_client_replays_the_live_capture(monkeypatch):
    """Stream the captured gpsd session at the client and check the snapshot."""
    monkeypatch.setattr(gpsd_mod, "BACKOFF_MIN_S", 0.02)
    monkeypatch.setattr(gpsd_mod, "BACKOFF_MAX_S", 0.05)
    text = (Path(__file__).resolve().parent / "fixtures" / "live_gpsd.jsonl").read_text()
    messages = [json.loads(line) for line in text.splitlines() if line.strip()]
    # The server greets with VERSION itself, so do not replay it twice.
    server = FakeGpsd([m for m in messages if m["class"] != "VERSION"])
    await server.start()
    settings = Settings(_env_file=None, gpsd_host="127.0.0.1", gpsd_port=server.port)
    store = StateStore(settings)
    gps_client = GpsdClient(settings, store)
    await gps_client.start()
    try:
        await wait_for(lambda: store.gps.gst is not None and store.gps.satellites.seen == 12)
        snap = store.gps
        assert snap.available is True
        assert snap.fix.fix_text == "3D DGPS FIX"
        assert snap.position.grid_square == "EN41er01"
        assert [d.path for d in snap.devices] == ["/dev/ttyAMA0", "/dev/pps0"]
        assert snap.device.driver == "MTK-3301"
        assert snap.gst.major_m == pytest.approx(4.0)
        assert snap.time_offset.source == "PPS"
        assert snap.time_offset.toff_offset_s is None
        assert store.raw_gpsd["WATCH"]["pps"] is False
    finally:
        await gps_client.stop()
        await server.stop()


class RecordingBroadcaster:
    """Stands in for the real Broadcaster and records every publish() call."""

    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    def publish(self, event: str, payload) -> int:
        self.published.append((event, payload))
        return 1

    def events(self, name: str) -> list:
        return [payload for event, payload in self.published if event == name]


async def test_watch_command_enables_raw_nmea():
    assert '"nmea":true' in WATCH_COMMAND


async def test_client_rings_and_broadcasts_nmea_without_rebuilding_the_snapshot(monkeypatch):
    """Rule 3: raw NMEA fills the ring and the stream, but never the snapshot."""
    monkeypatch.setattr(gpsd_mod, "BACKOFF_MIN_S", 0.02)
    monkeypatch.setattr(gpsd_mod, "BACKOFF_MAX_S", 0.05)
    server = FakeGpsd([DEVICES, RMC_LINE, TPV, GGA_LINE, SKY, PPS])
    await server.start()
    settings = Settings(_env_file=None, gpsd_host="127.0.0.1", gpsd_port=server.port)
    store = StateStore(settings)
    broadcaster = RecordingBroadcaster()
    gps_client = GpsdClient(settings, store, broadcaster=broadcaster)

    publishes = 0
    original_publish = gps_client._publish

    def counting_publish(now=None):
        nonlocal publishes
        publishes += 1
        original_publish(now)

    gps_client._publish = counting_publish
    await gps_client.start()
    try:
        await wait_for(lambda: len(store.nmea_ring) >= 2 and store.gps.satellites.seen == 12)

        # (a) the ring holds the parsed sentences, oldest first
        assert [entry["line"] for entry in store.nmea_ring] == [RMC_LINE, GGA_LINE]
        assert [entry["type"] for entry in store.nmea_ring] == ["RMC", "GGA"]
        assert all(entry["talker"] == "GP" for entry in store.nmea_ring)
        assert all(entry["checksum_ok"] is True for entry in store.nmea_ring)
        assert all(entry["t"] > 0 for entry in store.nmea_ring)
        assert store.nmea_rate() > 0

        # (b) both sentences were broadcast as nmea events, JSON as gpsd events
        assert broadcaster.events("nmea") == list(store.nmea_ring)
        classes = [msg["class"] for msg in broadcaster.events("gpsd")]
        assert classes == ["VERSION", "DEVICES", "TPV", "SKY", "PPS"]
        assert all(msg["_t"] > 0 for msg in broadcaster.events("gpsd"))

        # (c) an NMEA line does not rebuild the snapshot
        before = publishes
        collected_at = store.gps.collected_at
        gps_client._handle_line(BAD_LINE.encode() + b"\r\n")
        assert publishes == before
        assert store.gps.collected_at == collected_at
        assert len(store.nmea_ring) == 3
        assert store.nmea_ring[-1]["checksum_ok"] is False
        assert broadcaster.events("nmea")[-1]["type"] == "VTG"

        # ... while a JSON message still does
        gps_client._handle_line(json.dumps(TPV).encode() + b"\r\n")
        assert publishes == before + 1

        # (d) the watch command asks gpsd for raw NMEA
        assert json.loads(server.watch_commands[0].strip()[len("?WATCH=") : -1])["nmea"] is True
    finally:
        await gps_client.stop()
        await server.stop()


async def test_the_nmea_ring_survives_a_reconnect(monkeypatch):
    monkeypatch.setattr(gpsd_mod, "BACKOFF_MIN_S", 0.02)
    monkeypatch.setattr(gpsd_mod, "BACKOFF_MAX_S", 0.05)
    server = FakeGpsd([DEVICES, RMC_LINE, TPV])
    await server.start()
    settings = Settings(_env_file=None, gpsd_host="127.0.0.1", gpsd_port=server.port)
    store = StateStore(settings)
    gps_client = GpsdClient(settings, store)
    await gps_client.start()
    try:
        await wait_for(lambda: len(store.nmea_ring) >= 1)
        await server.drop_clients()
        await wait_for(lambda: server.connections >= 2, timeout=5.0)
        await wait_for(lambda: len(store.nmea_ring) >= 2, timeout=5.0)
        assert [entry["line"] for entry in store.nmea_ring] == [RMC_LINE, RMC_LINE]
    finally:
        await gps_client.stop()
        await server.stop()


async def test_the_nmea_ring_is_bounded():
    settings = Settings(_env_file=None, nmea_ring=3)
    store = StateStore(settings)
    gps_client = GpsdClient(settings, store)
    for i in range(6):
        gps_client._handle_line(f"$GPTXT,{i}*00".encode())
    assert len(store.nmea_ring) == 3
    assert store.nmea_ring.maxlen == 3
    assert [entry["line"][-4:-3] for entry in store.nmea_ring] == ["3", "4", "5"]
