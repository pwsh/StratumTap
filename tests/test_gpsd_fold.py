"""Pure folding of gpsd messages into a GpsSnapshot."""

from __future__ import annotations

import pytest

from stratumtap.gpsd import (
    GpsdState,
    fix_text,
    fold_message,
    gnss_labels,
    mode_text,
    parse_gps_time,
    status_text,
)

VERSION = {"class": "VERSION", "release": "3.22", "rev": "3.22", "proto_major": 3}
DEVICES = {
    "class": "DEVICES",
    "devices": [
        {
            "class": "DEVICE",
            "path": "/dev/ttyAMA0",
            "driver": "NMEA0183",
            "activated": "2026-08-26T10:00:00.000Z",
            "bps": 9600,
        }
    ],
}
TPV = {
    "class": "TPV",
    "device": "/dev/ttyAMA0",
    "mode": 3,
    "status": 2,
    "time": "2026-08-26T14:17:51.000Z",
    "ept": 0.005,
    "lat": 41.71343333,
    "lon": -91.66269,
    "altHAE": 198.4,
    "altMSL": 231.9,
    "geoidSep": -33.5,
    "magvar": -0.9,
    "speed": 0.022,
    "climb": 0.0,
    "track": 63.5,
    "magtrack": 64.4,
    "eps": 6.08,
    "epx": 2.1,
    "epy": 3.0,
    "epv": 4.3,
    "eph": 4.0,
    "sep": 5.8,
    "leapseconds": 18,
}
SKY = {
    "class": "SKY",
    "device": "/dev/ttyAMA0",
    "time": "2026-08-26T14:17:51.000Z",
    "xdop": 0.56,
    "ydop": 0.81,
    "vdop": 0.75,
    "hdop": 0.97,
    "pdop": 1.23,
    "tdop": 0.69,
    "gdop": 1.65,
    "nSat": 12,
    "uSat": 9,
    "satellites": [
        {"gnssid": 1, "svid": 133, "PRN": 46, "el": 28.0, "az": 229.0, "ss": 47.0, "used": False},
        {
            "gnssid": 0,
            "svid": 5,
            "PRN": 5,
            "sigid": 0,
            "el": 29.0,
            "az": 64.0,
            "ss": 33.0,
            "used": True,
            "health": 1,
        },
        {"gnssid": 6, "svid": 71, "PRN": 71, "el": 21.0, "az": 46.0, "ss": 30.0, "used": True},
        {"gnssid": 0, "svid": 10, "PRN": 10, "el": 15.0, "az": 260.0, "ss": 45.0, "used": True},
    ],
}


def _connected_state() -> GpsdState:
    state = GpsdState()
    state.on_connect()
    return state


def test_fold_full_snapshot():
    state = _connected_state()
    for msg in (VERSION, DEVICES, TPV, SKY):
        fold_message(state, msg, now=1000.0)
    snap = state.snapshot(now=1000.0)

    assert snap.connected is True
    assert snap.available is True
    assert snap.error is None
    assert snap.gpsd_version == "3.22"
    assert snap.collected_at == 1000.0

    assert snap.device.path == "/dev/ttyAMA0"
    assert snap.device.driver == "NMEA0183"
    assert snap.device.bps == 9600
    assert snap.device.activated == "2026-08-26T10:00:00.000Z"

    assert snap.fix.mode == 3
    assert snap.fix.mode_text == "3D"
    assert snap.fix.status == 2
    assert snap.fix.status_text == "DGPS"
    assert snap.fix.fix_text == "3D DGPS FIX"
    assert snap.fix.fix_age_s == 0.0
    assert snap.fix.time == "2026-08-26T14:17:51.000Z"
    assert snap.fix.time_unix == pytest.approx(1787753871.0)
    assert snap.fix.time_age_s is None  # the API layer fills this in
    assert snap.fix.ept_s == pytest.approx(0.005)
    assert snap.fix.leapseconds == 18

    assert snap.position.lat == pytest.approx(41.71343333)
    assert snap.position.lon == pytest.approx(-91.66269)
    assert snap.position.alt_hae_m == pytest.approx(198.4)
    assert snap.position.alt_msl_m == pytest.approx(231.9)
    assert snap.position.geoid_sep_m == pytest.approx(-33.5)
    assert snap.position.grid_square == "EN41er01"

    assert snap.motion.speed_mps == pytest.approx(0.022)
    assert snap.motion.track_deg == pytest.approx(63.5)
    assert snap.motion.mag_track_deg == pytest.approx(64.4)
    assert snap.motion.mag_var_deg == pytest.approx(-0.9)
    assert snap.motion.climb_mps == pytest.approx(0.0)

    assert snap.accuracy.eph_m == pytest.approx(4.0)
    assert snap.accuracy.sep_m == pytest.approx(5.8)
    assert snap.accuracy.eps_mps == pytest.approx(6.08)
    assert snap.accuracy.epd_deg is None
    assert snap.accuracy.epc_mps is None
    assert snap.accuracy.ept_s == pytest.approx(0.005)

    assert snap.dop.hdop == pytest.approx(0.97)
    assert snap.dop.gdop == pytest.approx(1.65)

    assert snap.ecef.x_m is None and snap.ecef.v_acc_mps is None

    assert snap.satellites.seen == 12
    assert snap.satellites.used == 9
    assert snap.satellites.collected_at == pytest.approx(1787753871.0)


def test_satellite_sort_order_and_sbas_numbering():
    state = _connected_state()
    fold_message(state, SKY, now=1.0)
    sats = state.satellites().list
    assert [(s.gnssid, s.svid) for s in sats] == [(0, 5), (0, 10), (1, 133), (6, 71)]

    sbas = next(s for s in sats if s.gnssid == 1)
    assert sbas.gnss == "SB"
    assert sbas.gnss_name == "SBAS"
    assert sbas.svid == 133  # the conventional PRN cgps shows first
    assert sbas.prn == 46  # gpsd's internal PRN
    assert sbas.sigid is None
    assert sbas.used is False
    assert sbas.health is None

    gps5 = sats[0]
    assert (gps5.gnss, gps5.gnss_name, gps5.sigid, gps5.health) == ("GP", "GPS", 0, 1)

    glonass = next(s for s in sats if s.gnssid == 6)
    assert (glonass.gnss, glonass.gnss_name) == ("GL", "GLONASS")


def test_satellite_counts_fall_back_to_the_list():
    state = _connected_state()
    sky = {k: v for k, v in SKY.items() if k not in ("nSat", "uSat")}
    fold_message(state, sky, now=1.0)
    sats = state.satellites()
    assert sats.seen == 4
    assert sats.used == 3


def test_missing_keys_become_none():
    state = _connected_state()
    fold_message(state, {"class": "TPV", "mode": 1}, now=5.0)
    snap = state.snapshot(now=5.0)
    assert snap.fix.mode == 1
    assert snap.fix.mode_text == "no fix"
    assert snap.fix.status is None
    assert snap.fix.status_text is None
    assert snap.fix.fix_text == "NO FIX"
    assert snap.position.lat is None
    assert snap.position.grid_square is None
    assert snap.motion.speed_mps is None
    assert snap.accuracy.eph_m is None
    assert snap.available is True  # a TPV arrived, even without a fix


def test_fix_age_tracks_mode_changes():
    state = _connected_state()
    fold_message(state, {"class": "TPV", "mode": 3}, now=100.0)
    assert state.snapshot(now=106.0).fix.fix_age_s == pytest.approx(6.0)
    # Same mode: the age keeps growing.
    fold_message(state, {"class": "TPV", "mode": 3}, now=104.0)
    assert state.snapshot(now=106.0).fix.fix_age_s == pytest.approx(6.0)
    # Mode change resets it.
    fold_message(state, {"class": "TPV", "mode": 2}, now=110.0)
    assert state.snapshot(now=112.0).fix.fix_age_s == pytest.approx(2.0)


@pytest.mark.parametrize(
    "mode,status,expected",
    [
        (0, None, "NO FIX"),
        (1, None, "NO FIX"),
        (2, None, "2D FIX"),
        (3, None, "3D FIX"),
        (3, 2, "3D DGPS FIX"),
        (2, 2, "2D DGPS FIX"),
        (3, 3, "3D RTK FIX"),
        (3, 4, "3D RTK FIX"),
        (3, 5, "3D DR FIX"),
        (3, 6, "3D GNSSDR FIX"),
        (3, 7, "FIXED SURVEYED"),
        (3, 8, "3D SIM FIX"),
        (3, 9, "3D P(Y) FIX"),
        (1, 5, "NO DR FIX"),
        (None, 2, None),
    ],
)
def test_fix_text_table(mode, status, expected):
    assert fix_text(mode, status) == expected


@pytest.mark.parametrize(
    "mode,expected",
    [(None, None), (0, "unknown"), (1, "no fix"), (2, "2D"), (3, "3D"), (9, "unknown")],
)
def test_mode_text_table(mode, expected):
    assert mode_text(mode) == expected


@pytest.mark.parametrize(
    "status,expected",
    [
        (None, None),
        (2, "DGPS"),
        (3, "RTK"),
        (4, "RTK float"),
        (5, "DR"),
        (6, "GNSSDR"),
        (7, "FIXED"),
        (8, "SIM"),
        (9, "P(Y)"),
        (99, None),
    ],
)
def test_status_text_table(status, expected):
    assert status_text(status) == expected


@pytest.mark.parametrize(
    "gnssid,expected",
    [
        (0, ("GP", "GPS")),
        (1, ("SB", "SBAS")),
        (2, ("GA", "Galileo")),
        (3, ("BD", "BeiDou")),
        (4, ("IM", "IMES")),
        (5, ("QZ", "QZSS")),
        (6, ("GL", "GLONASS")),
        (7, ("IR", "NavIC")),
        (8, ("??", "unknown")),
        (None, ("??", "unknown")),
    ],
)
def test_gnss_labels_table(gnssid, expected):
    assert gnss_labels(gnssid) == expected


def test_device_removed_when_activated_is_zero():
    state = _connected_state()
    fold_message(state, DEVICES, now=1.0)
    assert state.snapshot(now=1.0).device.path == "/dev/ttyAMA0"
    fold_message(state, {"class": "DEVICE", "path": "/dev/ttyAMA0", "activated": 0}, now=2.0)
    snap = state.snapshot(now=2.0)
    assert snap.device.path is None
    assert snap.device.driver is None


def test_device_added_by_device_message():
    state = _connected_state()
    fold_message(
        state,
        {
            "class": "DEVICE",
            "path": "/dev/ttyUSB0",
            "driver": "u-blox",
            "activated": "2026-08-26T10:00:00.000Z",
            "bps": 38400,
        },
        now=1.0,
    )
    dev = state.snapshot(now=1.0).device
    assert dev.path == "/dev/ttyUSB0"
    assert dev.driver == "u-blox"
    assert dev.bps == 38400


def test_pps_and_toff_offsets():
    state = _connected_state()
    fold_message(
        state,
        {
            "class": "TOFF",
            "device": "/dev/ttyAMA0",
            "real_sec": 1787753871,
            "real_nsec": 0,
            "clock_sec": 1787753871,
            "clock_nsec": 412300000,
        },
        now=100.0,
    )
    off = state.snapshot(now=100.0).time_offset
    assert off.source == "TOFF"
    assert off.offset_s == pytest.approx(0.4123)
    assert off.toff_offset_s == pytest.approx(0.4123)
    assert off.pps_offset_s is None
    assert off.measured_at == 100.0
    assert off.precision is None

    fold_message(
        state,
        {
            "class": "PPS",
            "device": "/dev/ttyAMA0",
            "real_sec": 1787753871,
            "real_nsec": 0,
            "clock_sec": 1787753871,
            "clock_nsec": 1234,
            "precision": -20,
        },
        now=101.0,
    )
    off = state.snapshot(now=101.0).time_offset
    assert off.source == "PPS"  # both fresh -> PPS wins
    assert off.offset_s == pytest.approx(1.234e-6)
    assert off.real_s == pytest.approx(1787753871.0)
    assert off.clock_s == pytest.approx(1787753871.000001234)
    assert off.precision == -20
    assert off.toff_offset_s == pytest.approx(0.4123)

    # PPS goes stale (> 5 s) while TOFF keeps arriving -> TOFF becomes the source.
    fold_message(
        state,
        {
            "class": "TOFF",
            "real_sec": 1787753900,
            "real_nsec": 0,
            "clock_sec": 1787753900,
            "clock_nsec": 400000000,
        },
        now=130.0,
    )
    off = state.snapshot(now=130.0).time_offset
    assert off.source == "TOFF"
    assert off.offset_s == pytest.approx(0.4)


def test_time_offset_absent_when_never_seen():
    state = _connected_state()
    off = state.snapshot(now=1.0).time_offset
    assert off.source is None
    assert off.offset_s is None
    assert off.pps_offset_s is None
    assert off.toff_offset_s is None


def test_negative_pps_offset():
    state = _connected_state()
    fold_message(
        state,
        {
            "class": "PPS",
            "real_sec": 1787753872,
            "real_nsec": 0,
            "clock_sec": 1787753871,
            "clock_nsec": 999998000,
        },
        now=1.0,
    )
    assert state.snapshot(now=1.0).time_offset.offset_s == pytest.approx(-2e-6, abs=1e-12)


def test_raw_keeps_last_message_per_class():
    state = _connected_state()
    for msg in (VERSION, DEVICES, TPV, SKY):
        fold_message(state, msg, now=1.0)
    fold_message(state, {"class": "TPV", "mode": 2}, now=2.0)
    assert set(state.raw) == {"VERSION", "DEVICES", "TPV", "SKY"}
    assert state.raw["TPV"] == {"class": "TPV", "mode": 2}


def test_reconnect_clears_fix_but_keeps_devices():
    state = _connected_state()
    for msg in (VERSION, DEVICES, TPV, SKY):
        fold_message(state, msg, now=1.0)
    state.on_disconnect("connection refused (127.0.0.1:2947)")
    snap = state.snapshot(now=2.0)
    assert snap.connected is False
    assert snap.available is False
    assert snap.error == "connection refused (127.0.0.1:2947)"
    assert snap.fix.mode is None
    assert snap.device.path == "/dev/ttyAMA0"  # device info survives

    state.on_connect()
    snap = state.snapshot(now=3.0)
    assert snap.connected is True
    assert snap.available is False  # no TPV since reconnect
    assert snap.fix.mode is None
    assert snap.device.path == "/dev/ttyAMA0"


def test_unknown_and_malformed_messages_are_ignored():
    state = _connected_state()
    fold_message(state, {"no": "class"}, now=1.0)
    fold_message(state, {"class": 42}, now=1.0)
    fold_message(state, {"class": "WATCH", "enable": True}, now=1.0)
    fold_message(state, {"class": "ERROR", "message": "bad request"}, now=1.0)
    assert state.snapshot(now=1.0).available is False


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-08-26T14:17:51.000Z", 1787753871.0),
        ("2026-08-26T14:17:51Z", 1787753871.0),
        ("2026-08-26T14:17:51.500Z", 1787753871.5),
        ("2026-08-26T14:17:51.000+00:00", 1787753871.0),
        (1787753871.25, 1787753871.25),
        ("nonsense", None),
        (None, None),
    ],
)
def test_parse_gps_time(value, expected):
    result = parse_gps_time(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_alt_falls_back_to_alt_key():
    state = _connected_state()
    fold_message(state, {"class": "TPV", "mode": 3, "alt": 123.5}, now=1.0)
    assert state.snapshot(now=1.0).position.alt_hae_m == pytest.approx(123.5)


def test_connected_without_devices_explains_itself():
    from stratumtap.gpsd import GpsdState

    state = GpsdState()
    state.on_connect()
    snap = state.snapshot(now=100.0)
    assert snap.connected is True and snap.available is False
    assert "no GPS device" in (snap.error or "")

    state.fold(
        {"class": "DEVICES", "devices": [{"class": "DEVICE", "path": "/dev/ttyAMA0"}]}, 100.0
    )
    snap = state.snapshot(now=100.0)
    assert "waiting" in (snap.error or "")

    state.fold({"class": "TPV", "device": "/dev/ttyAMA0", "mode": 1}, 101.0)
    snap = state.snapshot(now=101.0)
    assert snap.available is True and snap.error is None


def test_disconnect_clears_stale_pps():
    from stratumtap.gpsd import GpsdState

    state = GpsdState()
    state.on_connect()
    state.fold(
        {"class": "PPS", "real_sec": 10, "real_nsec": 0, "clock_sec": 10, "clock_nsec": 500},
        10.0,
    )
    assert state.snapshot(now=10.0).time_offset.source == "PPS"
    state.on_disconnect("gone")
    assert state.snapshot(now=11.0).time_offset.source is None


def test_dop_only_sky_keeps_satellite_list():
    """gpsd 3.25 sends DOP-only SKY messages between full ones (no satellites key)."""
    state = GpsdState()
    state.on_connect()
    fold_message(
        state,
        {
            "class": "SKY",
            "nSat": 2,
            "uSat": 1,
            "hdop": 1.0,
            "satellites": [
                {
                    "PRN": 5,
                    "el": 10.0,
                    "az": 20.0,
                    "ss": 30.0,
                    "used": True,
                    "gnssid": 0,
                    "svid": 5,
                },
                {
                    "PRN": 7,
                    "el": 40.0,
                    "az": 50.0,
                    "ss": 0.0,
                    "used": False,
                    "gnssid": 0,
                    "svid": 7,
                },
            ],
        },
        now=100.0,
    )
    fold_message(state, {"class": "SKY", "hdop": 0.8, "pdop": 1.1, "uSat": 1}, now=101.0)
    snap = state.snapshot(now=101.0)
    assert snap.dop.hdop == 0.8  # DOPs updated from the partial message
    assert snap.dop.pdop == 1.1
    assert snap.satellites.seen == 2  # list and counts survive
    assert snap.satellites.used == 1
    assert [s.svid for s in snap.satellites.list] == [5, 7]
    assert state.collected_at == 101.0
