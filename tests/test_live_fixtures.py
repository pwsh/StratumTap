"""Tests against output captured from the real target host (``tests/fixtures/live_*``).

The target is a Raspberry Pi running Debian 12, chrony 4.3, gpsd 3.22 with an
MTK-3301 receiver on ``/dev/ttyAMA0`` and KPPS on ``/dev/pps0``.
"""

from __future__ import annotations

import json

import pytest

from stratumtap.chrony import (
    parse_sources_csv,
    parse_sourcestats_csv,
    parse_tracking_csv,
    parse_tracking_text,
)
from stratumtap.gpsd import GpsdState
from tests.conftest import fixture_text


@pytest.fixture
def live_gpsd_messages() -> list[dict]:
    """The captured gpsd JSON stream, one decoded message per line."""
    text = fixture_text("live_gpsd.jsonl")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture
def live_state(live_gpsd_messages) -> GpsdState:
    """A GpsdState with the whole captured stream folded in."""
    state = GpsdState()
    state.on_connect()
    for i, msg in enumerate(live_gpsd_messages):
        state.fold(msg, now=1787757845.0 + i * 0.25)
    return state


# --------------------------------------------------------------------------
# chrony
# --------------------------------------------------------------------------


def test_live_tracking_csv():
    parsed = parse_tracking_csv(fixture_text("live_chronyc_tracking.csv"))
    assert parsed["reference_id"] == "50505300"
    assert parsed["reference_name"] == "PPS"
    assert parsed["stratum"] == 1
    assert parsed["ref_time_unix"] == pytest.approx(1787757845.6833595)
    assert parsed["ref_time"].startswith("2026-08-26T15:24:05.")
    # CSV field 4 is chrony's current_correction: + means the system clock is SLOW.
    assert parsed["system_offset_s"] == pytest.approx(-4.57e-7)
    assert parsed["last_offset_s"] == pytest.approx(-7.49e-7)
    assert parsed["rms_offset_s"] == pytest.approx(7.29e-7)
    assert parsed["frequency_ppm"] == pytest.approx(18.086)
    assert parsed["residual_freq_ppm"] == pytest.approx(-0.002)
    assert parsed["skew_ppm"] == pytest.approx(0.140)
    assert parsed["root_delay_s"] == pytest.approx(1e-9)
    assert parsed["root_dispersion_s"] == pytest.approx(9.131e-6)
    assert parsed["update_interval_s"] == pytest.approx(8.0)
    assert parsed["leap_status"] == "Normal"
    assert parsed["synchronized"] is True


def test_live_tracking_text():
    parsed = parse_tracking_text(fixture_text("live_chronyc_tracking.txt"))
    assert parsed["reference_id"] == "50505300"
    assert parsed["reference_name"] == "PPS"
    assert parsed["stratum"] == 1
    assert parsed["ref_time_unix"] == pytest.approx(1787757845.0)
    # "0.000000469 seconds slow of NTP time" -> negative
    assert parsed["system_offset_s"] == pytest.approx(-4.69e-7)
    assert parsed["last_offset_s"] == pytest.approx(-7.49e-7)
    assert parsed["frequency_ppm"] == pytest.approx(18.086)  # "18.086 ppm fast"
    assert parsed["residual_freq_ppm"] == pytest.approx(-0.002)
    assert parsed["synchronized"] is True


def test_live_tracking_formats_agree_on_the_stable_fields():
    """The two captures are a fraction of a second apart, so only compare stable fields."""
    csv_parsed = parse_tracking_csv(fixture_text("live_chronyc_tracking.csv"))
    text_parsed = parse_tracking_text(fixture_text("live_chronyc_tracking.txt"))
    for key in (
        "reference_id",
        "reference_name",
        "stratum",
        "last_offset_s",
        "rms_offset_s",
        "frequency_ppm",
        "residual_freq_ppm",
        "skew_ppm",
        "root_delay_s",
        "update_interval_s",
        "leap_status",
        "synchronized",
    ):
        assert csv_parsed[key] == text_parsed[key], key
    # Both agree the system clock is slow (negative offset).
    assert csv_parsed["system_offset_s"] < 0
    assert text_parsed["system_offset_s"] < 0


def test_live_sources_csv():
    rows = parse_sources_csv(fixture_text("live_chronyc_sources.csv"))
    assert [r["name"] for r in rows] == [
        "NMEA",
        "PPS",
        "132.163.97.2",  # CSV mode never reverse-resolves; the -v text does
        "162.159.200.123",
        "128.138.140.44",
        "10.0.0.24",
    ]

    nmea = rows[0]
    assert nmea["mode"] == "#" and nmea["mode_text"] == "refclock"
    assert nmea["state"] == "x" and nmea["state_text"] == "falseticker"
    assert nmea["stratum"] == 0
    assert nmea["poll"] == 0
    assert nmea["poll_interval_s"] == 1
    assert nmea["reach"] == 255  # printed in octal even in CSV mode
    assert nmea["reach_octal"] == "377"
    assert nmea["last_rx_s"] == pytest.approx(2.0)
    assert nmea["last_sample_adjusted_offset_s"] == pytest.approx(-0.011335462)
    assert nmea["last_sample_offset_s"] == pytest.approx(-0.011335462)
    assert nmea["last_sample_error_s"] == pytest.approx(0.001000001)

    pps = rows[1]
    assert pps["state_text"] == "current best"
    assert pps["poll"] == 3 and pps["poll_interval_s"] == 8
    assert pps["last_rx_s"] == pytest.approx(7.0)
    assert pps["last_sample_adjusted_offset_s"] == pytest.approx(-8.13e-7)
    assert pps["last_sample_offset_s"] == pytest.approx(-1.509e-6)

    server = rows[2]
    assert server["mode_text"] == "server"
    assert server["state_text"] == "unusable"
    assert server["poll"] == 10 and server["poll_interval_s"] == 1024

    # LastRx == uint32 max means "never received" -> null.
    assert rows[4]["name"] == "128.138.140.44"  # india.colorado.edu in the -v text
    assert rows[4]["last_rx_s"] is None
    assert rows[4]["reach"] == 0
    assert rows[4]["reach_octal"] == "0"
    assert rows[5]["last_rx_s"] is None
    assert rows[5]["reach"] == 255
    assert rows[5]["reach_octal"] == "377"
    assert [r["reach"] for r in rows] == [255, 255, 255, 255, 0, 255]
    assert [r["reach_octal"] for r in rows] == ["377", "377", "377", "377", "0", "377"]


def test_live_sourcestats_csv():
    rows = parse_sourcestats_csv(fixture_text("live_chronyc_sourcestats.csv"))
    assert len(rows) == 6
    nmea = rows[0]
    assert nmea["name"] == "NMEA"
    assert nmea["np"] == 58 and nmea["nr"] == 36
    assert nmea["span_s"] == pytest.approx(57.0)
    assert nmea["frequency_ppm"] == pytest.approx(99.236)
    assert nmea["freq_skew_ppm"] == pytest.approx(115.649)
    assert nmea["offset_s"] == pytest.approx(-0.011862504)
    assert nmea["std_dev_s"] == pytest.approx(0.004213131)

    pps = rows[1]
    assert pps["name"] == "PPS"
    assert pps["offset_s"] == pytest.approx(-9e-9)
    assert pps["std_dev_s"] == pytest.approx(1.176e-6)


def test_live_verbose_text_has_a_multiline_header():
    """The ``-v`` output is stored verbatim; we never parse it."""
    sources = fixture_text("live_chronyc_sources.txt")
    assert "MS Name/IP address" in sources
    assert "Source mode" in sources  # the legend block above the table
    assert "time-b-wwv.nist.gov" in sources  # hostnames only appear here
    stats = fixture_text("live_chronyc_sourcestats.txt")
    assert "Name/IP Address" in stats
    assert "Number of sample points" in stats


# --------------------------------------------------------------------------
# gpsd
# --------------------------------------------------------------------------


def test_live_stream_classes(live_gpsd_messages):
    classes = {m["class"] for m in live_gpsd_messages}
    assert classes == {"VERSION", "DEVICES", "WATCH", "TPV", "SKY", "PPS", "GST"}
    # gpsd 3.22 sends PPS to JSON watchers even though it echoed "pps": false.
    watch = next(m for m in live_gpsd_messages if m["class"] == "WATCH")
    assert watch["pps"] is False
    assert sum(1 for m in live_gpsd_messages if m["class"] == "PPS") > 1


def test_live_snapshot_fix_and_position(live_state):
    snap = live_state.snapshot(now=1787757865.0)
    assert snap.available is True
    assert snap.connected is True
    assert snap.gpsd_version == "3.22"

    assert snap.fix.mode == 3
    assert snap.fix.mode_text == "3D"
    assert snap.fix.status == 2
    assert snap.fix.status_text == "DGPS"
    assert snap.fix.fix_text == "3D DGPS FIX"
    assert snap.fix.time == "2026-08-26T15:24:23.000Z"
    assert snap.fix.time_unix == pytest.approx(1787757863.0)
    assert snap.fix.ept_s == pytest.approx(0.005)
    assert snap.fix.leapseconds is None  # the MTK-3301 does not report it

    assert snap.position.lat == pytest.approx(41.713396667)
    assert snap.position.lon == pytest.approx(-91.662675)
    assert snap.position.alt_hae_m == pytest.approx(200.3)  # altHAE wins over alt
    assert snap.position.alt_msl_m == pytest.approx(233.8)
    assert snap.position.geoid_sep_m == pytest.approx(-33.5)
    assert snap.position.grid_square == "EN41er01"

    assert snap.motion.speed_mps == pytest.approx(0.01)
    assert snap.motion.track_deg == pytest.approx(63.45)
    assert snap.motion.mag_track_deg == pytest.approx(62.521)
    assert snap.motion.mag_var_deg == pytest.approx(-0.9)

    assert snap.accuracy.eph_m == pytest.approx(4.418)
    assert snap.accuracy.sep_m == pytest.approx(5.747)
    assert snap.accuracy.epc_mps == pytest.approx(8.86)
    assert snap.accuracy.epd_deg is None

    assert snap.dop.hdop == pytest.approx(0.93)
    assert snap.dop.gdop == pytest.approx(1.75)
    assert snap.ecef.x_m is None  # no ecef* in the NMEA feed


def test_live_snapshot_devices(live_state):
    snap = live_state.snapshot(now=1787757865.0)
    assert [d.path for d in snap.devices] == ["/dev/ttyAMA0", "/dev/pps0"]

    serial, pps = snap.devices
    assert serial.driver == "MTK-3301"
    assert serial.subtype == "AXN_2.51_3339_17112000-0004"
    assert serial.bps == 9600
    assert serial.cycle_s == pytest.approx(1.0)
    assert serial.activated == "2026-08-26T15:24:15.000Z"

    assert pps.driver == "PPS"
    assert pps.bps is None
    assert pps.cycle_s is None

    # `device` is the one named in the last TPV.
    assert snap.device.path == "/dev/ttyAMA0"
    assert snap.device.driver == "MTK-3301"


def test_active_device_falls_back_to_the_first_non_pps_device(live_gpsd_messages):
    state = GpsdState()
    state.on_connect()
    for msg in live_gpsd_messages:
        if msg["class"] == "DEVICES":
            state.fold(msg, now=1.0)
    # No TPV yet -> skip /dev/pps0 and pick the receiver.
    assert state.device().path == "/dev/ttyAMA0"


def test_live_snapshot_gst(live_state):
    gst = live_state.snapshot(now=1787757865.0).gst
    assert gst is not None
    assert gst.time_unix == pytest.approx(1787757863.0)
    assert gst.rms_m == pytest.approx(5.7)
    assert gst.major_m == pytest.approx(4.0)
    assert gst.minor_m == pytest.approx(2.4)
    assert gst.orient_deg == pytest.approx(10.2)
    assert gst.lat_err_m == pytest.approx(4.0)
    assert gst.lon_err_m == pytest.approx(2.5)
    assert gst.alt_err_m == pytest.approx(4.6)


def test_gst_is_null_when_never_seen():
    state = GpsdState()
    state.on_connect()
    state.fold({"class": "TPV", "mode": 3}, now=1.0)
    assert state.snapshot(now=1.0).gst is None


def test_live_snapshot_time_offset_is_pps_only(live_state):
    off = live_state.snapshot(now=1787757865.0).time_offset
    assert off.source == "PPS"
    assert off.toff_offset_s is None  # no TOFF on this host
    assert off.precision == -20
    # Last PPS: clock 1787757864.000001637 - real 1787757864.0
    assert off.offset_s == pytest.approx(1.637e-6, abs=1e-12)
    assert off.pps_offset_s == pytest.approx(1.637e-6, abs=1e-12)


def test_live_duplicate_pps_takes_the_latest(live_gpsd_messages):
    state = GpsdState()
    state.on_connect()
    pps = [m for m in live_gpsd_messages if m["class"] == "PPS"]
    # The two devices report identical values, so either ordering gives the same offset.
    state.fold(pps[0], now=10.0)
    first = state.snapshot(now=10.0).time_offset.offset_s
    state.fold(pps[1], now=10.1)
    assert state.snapshot(now=10.1).time_offset.offset_s == pytest.approx(first)
    assert state.raw["PPS"] is pps[1]


def test_live_snapshot_satellites(live_state):
    sats = live_state.snapshot(now=1787757865.0).satellites
    assert sats.seen == 12
    assert sats.used == 9
    assert len(sats.list) == 12
    # SKY carries no "time" on this receiver -> fall back to the arrival time.
    assert sats.collected_at is not None

    keys = [(s.gnssid, s.svid, s.prn) for s in sats.list]
    assert keys == sorted(keys)

    sbas = next(s for s in sats.list if s.gnssid == 1)
    assert (sbas.gnss, sbas.gnss_name) == ("SB", "SBAS")
    assert sbas.svid == 133  # the number cgps shows first
    assert sbas.prn == 46  # gpsd's internal PRN
    assert sbas.used is False
    assert sbas.snr_db == pytest.approx(47.0)

    assert all(s.sigid is None for s in sats.list)  # not reported by NMEA
    assert all(s.health is None for s in sats.list)


def test_live_raw_keeps_every_class(live_state):
    assert set(live_state.raw) == {"VERSION", "DEVICES", "WATCH", "TPV", "SKY", "PPS", "GST"}
