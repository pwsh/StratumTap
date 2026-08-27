"""chronyc output parsers, sign conventions, and the collector's error path."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from stratumtap import chrony
from stratumtap.chrony import (
    ChronycError,
    ChronyCollector,
    parse_sources_csv,
    parse_sourcestats_csv,
    parse_tracking_csv,
    parse_tracking_text,
    run_chronyc,
)
from stratumtap.config import Settings
from stratumtap.state import StateStore

EXPECTED = {
    "reference_id": "50505300",
    "reference_name": "PPS",
    "stratum": 1,
    "ref_time_unix": 1787753832.0,
    "leap_status": "Normal",
    "synchronized": True,
}


def _check_common(parsed):
    for key, value in EXPECTED.items():
        assert parsed[key] == value, key
    assert parsed["system_offset_s"] == pytest.approx(3.72e-7, rel=1e-9)
    assert parsed["last_offset_s"] == pytest.approx(9.94e-7, rel=1e-9)
    assert parsed["rms_offset_s"] == pytest.approx(6.90e-7, rel=1e-9)
    assert parsed["frequency_ppm"] == pytest.approx(17.97, rel=1e-9)
    assert parsed["residual_freq_ppm"] == pytest.approx(0.004)
    assert parsed["skew_ppm"] == pytest.approx(0.098)
    assert parsed["root_delay_s"] == pytest.approx(1e-9)
    assert parsed["root_dispersion_s"] == pytest.approx(1.0513e-5)
    assert parsed["update_interval_s"] == pytest.approx(8.0)
    assert parsed["ref_time"] == "2026-08-26T14:17:12.000000Z"


def test_tracking_csv_matches_fixture(tracking_csv):
    _check_common(parse_tracking_csv(tracking_csv))


def test_tracking_text_matches_fixture(tracking_text):
    _check_common(parse_tracking_text(tracking_text))


def test_both_formats_agree(tracking_csv, tracking_text):
    assert parse_tracking_csv(tracking_csv) == parse_tracking_text(tracking_text)


def test_csv_system_time_sign_is_negated():
    """chrony's CSV 'System time' is current_correction: + means system SLOW."""
    row = (
        "AABBCCDD,1.2.3.4,2,1787753832.000000000,"
        "0.000000500,-0.000000100,0.0,-3.5,0,0,0,0,64.0,Normal"
    )
    parsed = parse_tracking_csv(row)
    assert parsed["system_offset_s"] == pytest.approx(-5e-7)  # correction + -> system slow
    assert parsed["last_offset_s"] == pytest.approx(-1e-7)  # passed through
    assert parsed["frequency_ppm"] == pytest.approx(-3.5)  # passed through


def test_text_slow_gives_negative_offset():
    text = (
        "Reference ID    : 7F7F0101 (GPS)\n"
        "Stratum         : 2\n"
        "System time     : 0.000004000 seconds slow of NTP time\n"
        "Frequency       : 3.500 ppm slow\n"
        "Leap status     : Normal\n"
    )
    parsed = parse_tracking_text(text)
    assert parsed["system_offset_s"] == pytest.approx(-4e-6)
    assert parsed["frequency_ppm"] == pytest.approx(-3.5)
    assert parsed["reference_name"] == "GPS"
    assert parsed["synchronized"] is True


def test_not_synchronised_csv():
    row = "00000000,,16,0.000000000,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,Not synchronised"
    parsed = parse_tracking_csv(row)
    assert parsed["stratum"] == 16
    assert parsed["leap_status"] == "Not synchronised"
    assert parsed["synchronized"] is False
    assert parsed["reference_name"] is None


def test_not_synchronised_text():
    text = (
        "Reference ID    : 00000000 ()\nStratum         : 0\nLeap status     : Not synchronised\n"
    )
    parsed = parse_tracking_text(text)
    assert parsed["synchronized"] is False
    assert parsed["reference_name"] is None


def test_text_missing_and_garbled_lines():
    text = (
        "Reference ID    : 50505300 (PPS)\n"
        "Stratum         : not-a-number\n"
        "Ref time (UTC)  : garbage\n"
        "System time     : \n"
        "this line has no colon\n"
        "Leap status     : Normal\n"
    )
    parsed = parse_tracking_text(text)
    assert parsed["reference_id"] == "50505300"
    assert parsed["stratum"] is None
    assert parsed["ref_time_unix"] is None
    assert parsed["system_offset_s"] is None
    assert parsed["synchronized"] is False  # stratum unknown


def test_tracking_csv_rejects_short_row():
    with pytest.raises(ValueError):
        parse_tracking_csv("a,b,c\n")


def test_tracking_text_rejects_empty():
    with pytest.raises(ValueError):
        parse_tracking_text("")


def test_parse_sources_csv():
    text = (
        "#,*,PPS,0,3,377,5,1.0000e-06,9.9400e-07,8.2000e-07\n"
        "^,-,time.cloudflare.com,3,6,177,41,-1.234e-04,-1.200e-04,4.0e-03\n"
        "\n"
        "?,?,short-row,1\n"
    )
    rows = parse_sources_csv(text)
    assert len(rows) == 2
    first = rows[0]
    assert first["mode"] == "#" and first["mode_text"] == "refclock"
    assert first["state"] == "*" and first["state_text"] == "current best"
    assert first["name"] == "PPS"
    assert first["stratum"] == 0
    assert first["poll"] == 3
    assert first["reach"] == 255  # 377 octal
    assert first["reach_octal"] == "377"
    assert first["last_rx_s"] == pytest.approx(5.0)
    assert first["last_sample_adjusted_offset_s"] == pytest.approx(1.0e-6)
    assert first["last_sample_offset_s"] == pytest.approx(9.94e-7)
    assert first["last_sample_error_s"] == pytest.approx(8.2e-7)
    assert rows[1]["mode_text"] == "server"
    assert rows[1]["state_text"] == "not combined"
    assert rows[1]["reach"] == 0o177
    assert rows[1]["reach_octal"] == "177"


def test_parse_sourcestats_csv():
    text = "PPS,32,15,248,0.001,0.050,1.100e-07,4.000e-07\nbroken,1\n"
    rows = parse_sourcestats_csv(text)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "PPS"
    assert row["np"] == 32 and row["nr"] == 15
    assert row["span_s"] == pytest.approx(248.0)
    assert row["frequency_ppm"] == pytest.approx(0.001)
    assert row["freq_skew_ppm"] == pytest.approx(0.05)
    assert row["offset_s"] == pytest.approx(1.1e-7)
    assert row["std_dev_s"] == pytest.approx(4.0e-7)


# --------------------------------------------------------------------------
# run_chronyc / collector error paths
# --------------------------------------------------------------------------


async def test_run_chronyc_missing_binary():
    with pytest.raises(ChronycError) as excinfo:
        await run_chronyc(["-c", "tracking"], bin="/nonexistent/chronyc-xyz")
    assert "not found" in str(excinfo.value)


async def test_run_chronyc_reads_stdout():
    out = await run_chronyc(["hello"], bin="/bin/echo")
    assert out.strip() == "hello"


async def test_run_chronyc_nonzero_exit():
    with pytest.raises(ChronycError):
        await run_chronyc(["-c", "exit 3"], bin="/bin/sh")


async def test_run_chronyc_timeout():
    with pytest.raises(ChronycError) as excinfo:
        await run_chronyc(["-c", "sleep 5"], bin="/bin/sh", timeout=0.2)
    assert "timed out" in str(excinfo.value)


async def test_run_chronyc_detects_daemon_error():
    with pytest.raises(ChronycError) as excinfo:
        await run_chronyc(["506 Cannot talk to daemon"], bin="/bin/echo")
    assert str(excinfo.value) == "506 Cannot talk to daemon"


def _settings(**kw):
    return Settings(_env_file=None, **kw)


async def test_collector_marks_unavailable_on_daemon_error(monkeypatch):
    settings = _settings()
    store = StateStore(settings)
    collector = ChronyCollector(settings, store)

    async def fake_run(args, bin="chronyc", timeout=3.0):
        raise ChronycError("506 Cannot talk to daemon")

    monkeypatch.setattr(chrony, "run_chronyc", fake_run)
    snapshot = await collector.poll_tracking()
    assert snapshot.available is False
    assert snapshot.error == "506 Cannot talk to daemon"
    assert snapshot.system_offset_s is None
    assert snapshot.raw is None
    assert store.ntp is snapshot

    sources = await collector.poll_sources()
    assert sources.available is False
    assert sources.error == "506 Cannot talk to daemon"
    assert sources.sources == []


async def test_collector_falls_back_to_text_when_csv_fails(
    monkeypatch, tracking_text, tracking_csv
):
    settings = _settings()
    store = StateStore(settings)
    collector = ChronyCollector(settings, store)

    async def fake_run(args, bin="chronyc", timeout=3.0):
        if args[0] == "-c":
            raise ChronycError("boom")
        return tracking_text

    monkeypatch.setattr(chrony, "run_chronyc", fake_run)
    snapshot = await collector.poll_tracking()
    assert snapshot.available is True
    assert snapshot.error is None
    assert snapshot.system_offset_s == pytest.approx(3.72e-7)
    assert snapshot.raw == tracking_text


async def test_collector_prefers_csv_and_stores_text_as_raw(
    monkeypatch, tracking_text, tracking_csv
):
    settings = _settings()
    store = StateStore(settings)
    collector = ChronyCollector(settings, store)

    async def fake_run(args, bin="chronyc", timeout=3.0):
        return tracking_csv if args[0] == "-c" else tracking_text

    monkeypatch.setattr(chrony, "run_chronyc", fake_run)
    snapshot = await collector.poll_tracking()
    assert snapshot.available is True
    assert snapshot.reference_name == "PPS"
    assert snapshot.raw == tracking_text
    assert snapshot.collected_at is not None


async def test_collector_loop_survives_exceptions(monkeypatch):
    settings = _settings(chrony_poll_s=0.01)
    store = StateStore(settings)
    collector = ChronyCollector(settings, store)
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise RuntimeError("kaboom")

    task = asyncio.get_running_loop().create_task(collector._loop(boom, 0.01))
    await asyncio.sleep(0.35)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert calls["n"] >= 2  # the loop kept going after the exception
