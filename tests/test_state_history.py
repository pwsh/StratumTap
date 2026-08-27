"""History ring buffer, downsampling and CSV rendering."""

from __future__ import annotations

import asyncio

import pytest

from stratumtap.config import Settings
from stratumtap.models import (
    GpsAccuracy,
    GpsDop,
    GpsFix,
    GpsPosition,
    GpsSnapshot,
    GpsTimeOffset,
    NtpSnapshot,
    Satellites,
)
from stratumtap.state import HISTORY_COLUMNS, StateStore


def _settings(**kw):
    return Settings(_env_file=None, **kw)


def _ntp(offset=3.7e-7):
    return NtpSnapshot(
        available=True,
        collected_at=1000.0,
        system_offset_s=offset,
        last_offset_s=9.94e-7,
        rms_offset_s=6.9e-7,
        frequency_ppm=17.97,
        stratum=1,
        leap_status="Normal",
        synchronized=True,
    )


def _gps():
    return GpsSnapshot(
        available=True,
        connected=True,
        collected_at=1000.0,
        fix=GpsFix(mode=3, mode_text="3D"),
        position=GpsPosition(lat=41.7134, lon=-91.6627, alt_hae_m=198.4),
        accuracy=GpsAccuracy(eph_m=4.0),
        dop=GpsDop(hdop=0.97),
        time_offset=GpsTimeOffset(source="PPS", offset_s=1.234e-6),
        satellites=Satellites(seen=12, used=9),
    )


def test_columns_match_the_row_width():
    store = StateStore(_settings())
    store.set_ntp(_ntp())
    store.set_gps(_gps())
    row = store.sample(now=1000.0)
    assert len(row) == len(HISTORY_COLUMNS)
    assert HISTORY_COLUMNS[0] == "t"
    assert row[0] == 1000.0
    named = dict(zip(HISTORY_COLUMNS, row, strict=True))
    assert named["ntp_system_offset_s"] == pytest.approx(3.7e-7)
    assert named["ntp_stratum"] == 1
    assert named["gps_mode"] == 3
    assert named["gps_sats_used"] == 9
    assert named["gps_sats_seen"] == 12
    assert named["gps_hdop"] == pytest.approx(0.97)
    assert named["gps_eph_m"] == pytest.approx(4.0)
    assert named["gps_time_offset_s"] == pytest.approx(1.234e-6)
    assert named["lat"] == pytest.approx(41.7134)
    assert named["alt_hae_m"] == pytest.approx(198.4)


def test_unavailable_sources_produce_nulls():
    store = StateStore(_settings())
    row = store.sample(now=1000.0)
    named = dict(zip(HISTORY_COLUMNS, row, strict=True))
    assert named["t"] == 1000.0
    assert all(named[c] is None for c in HISTORY_COLUMNS[1:])


def test_ring_buffer_is_bounded():
    store = StateStore(_settings(history_size=10))
    for i in range(50):
        store.sample(now=float(i))
    assert len(store.history) == 10
    assert store.history[0][0] == 40.0
    assert store.history[-1][0] == 49.0


def test_history_rows_filters_by_age(monkeypatch):
    store = StateStore(_settings(history_size=1000))
    now = 10_000.0
    for i in range(100):
        store.sample(now=now - 100 + i)  # one row per second, ending "now"
    monkeypatch.setattr("stratumtap.state.time.time", lambda: now)
    rows = store.history_rows(seconds=10, max_points=1000)
    assert len(rows) == 10  # t >= now-10
    assert rows[0][0] == pytest.approx(now - 10)
    assert rows[-1][0] == pytest.approx(now - 1)


def test_history_rows_downsamples(monkeypatch):
    store = StateStore(_settings(history_size=10_000))
    now = 10_000.0
    for i in range(1000):
        store.sample(now=now - 1000 + i)
    monkeypatch.setattr("stratumtap.state.time.time", lambda: now)

    rows = store.history_rows(seconds=100_000, max_points=100)
    assert len(rows) <= 100
    assert len(rows) == 100
    # every k-th row, k = ceil(1000/100) = 10
    assert rows[1][0] - rows[0][0] == pytest.approx(10.0)

    assert len(store.history_rows(seconds=100_000, max_points=1)) == 1
    assert len(store.history_rows(seconds=100_000, max_points=99)) <= 99
    # No downsampling needed.
    assert len(store.history_rows(seconds=100_000, max_points=5000)) == 1000


def test_history_rows_are_ascending_and_plain_lists():
    store = StateStore(_settings())
    for i in range(5):
        store.sample(now=float(i))
    rows = store.history_rows(seconds=1e9, max_points=100)
    assert all(isinstance(r, list) for r in rows)
    assert [r[0] for r in rows] == sorted(r[0] for r in rows)


def test_history_csv(monkeypatch):
    store = StateStore(_settings())
    store.set_ntp(_ntp())
    store.set_gps(_gps())
    now = 1787753871.0
    store.sample(now=now)
    store.set_ntp(NtpSnapshot(available=False, error="down"))
    store.set_gps(GpsSnapshot(available=False, connected=False))
    store.sample(now=now + 5)
    monkeypatch.setattr("stratumtap.state.time.time", lambda: now + 5)

    lines = list(store.history_csv(seconds=1e9, max_points=100))
    header = lines[0].strip().split(",")
    assert header[0] == "t_iso"
    assert header[1:] == HISTORY_COLUMNS
    assert len(lines) == 3

    first = lines[1].strip().split(",")
    assert first[0] == "2026-08-26T14:17:51.000Z"
    assert float(first[1]) == pytest.approx(now)
    assert float(first[2]) == pytest.approx(3.7e-7)
    assert first[6] == "1"  # stratum, an int

    second = lines[2].strip().split(",")
    assert second[0] == "2026-08-26T14:17:56.000Z"
    assert second[2:] == [""] * (len(HISTORY_COLUMNS) - 1)


async def test_sampler_task_runs_and_stops():
    store = StateStore(_settings(history_interval_s=0.05))
    await store.start_sampler()
    try:
        await asyncio.sleep(0.25)
    finally:
        await store.stop_sampler()
    assert len(store.history) >= 2
    count = len(store.history)
    await asyncio.sleep(0.15)
    assert len(store.history) == count  # stopped


def test_uptime_is_monotonic_and_non_negative():
    store = StateStore(_settings())
    assert store.uptime_s(store.started_at - 5) == 0.0
    assert store.uptime_s(store.started_at + 5) == pytest.approx(5.0)


def test_downsampling_keeps_newest_row(settings_factory=None):
    """Downsampling must never drop the most recent sample."""
    from types import SimpleNamespace

    from stratumtap.state import StateStore

    store = StateStore(SimpleNamespace(history_size=1000, history_interval_s=1.0))
    import time as _time

    now = _time.time()
    for i in range(100):
        store.sample(now - 100 + i)
    rows = store.history_rows(seconds=1000, max_points=10)
    assert len(rows) <= 10
    assert rows[-1][0] == round(now - 1, 3)
    assert rows == sorted(rows, key=lambda r: r[0])
