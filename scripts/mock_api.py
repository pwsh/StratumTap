#!/usr/bin/env python3
"""Development mock of the StratumTap API (stdlib only).

Serves stratumtap/static/ at / (SPA fallback to index.html) and contract-shaped,
slowly varying fake data under /api/v1/. It exists so the frontend can be
developed and screenshotted without a real chrony/gpsd host; the real backend
lives in stratumtap/*.py.

    python3 scripts/mock_api.py [--port 8089] [--degraded]

--degraded makes the gps domain report available:false so the "no data" paths
can be exercised.

Streaming (docs/api-contract.md "Streaming (v0.2)") is mocked too: /api/v1/stream (SSE),
/api/v1/stream/nmea.txt and /api/v1/raw/nmea are fed by one producer thread
making ~8 NMEA sentences/s, a gpsd TPV/SKY/PPS + an ntp snapshot every second
and stats every 10 s. --bad-checksum-every N corrupts every Nth sentence so the
red "checksum failed" rows can be seen.

    curl -N 'http://127.0.0.1:8089/api/v1/stream?events=nmea,gpsd,ntp'

The SPA understands a test-only URL flag, ?rawstream=1, which makes the detail
view's "Live raw" panel connect on mount (for headless screenshots — an open
EventSource means --virtual-time-budget never expires, so drive Chrome over CDP
and wait in real time instead).
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import itertools
import json
import math
import mimetypes
import os
import posixpath
import queue
import random
import select
import socket
import threading
import time
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATIC = os.path.join(ROOT, "stratumtap", "static")
VERSION = "0.1.0-mock"
T_BOOT = time.time()
HOSTNAME = socket.gethostname() or "ntp-mock"

BASE_LAT = 51.4779  # Royal Observatory, Greenwich
BASE_LON = -0.0015

GNSS_NAMES = {
    "GP": ("GPS", 0),
    "SB": ("SBAS", 1),
    "GA": ("Galileo", 2),
    "BD": ("BeiDou", 3),
    "IM": ("IMES", 4),
    "QZ": ("QZSS", 5),
    "GL": ("GLONASS", 6),
    "IR": ("NavIC", 7),
}


# A fixed constellation: each entry gets its own slow orbital motion so the sky
# plot and SNR bars visibly move without ever teleporting.
def _make_sats():
    rnd = random.Random(20260826)
    sats = []
    spec = [("GP", 10), ("GL", 6), ("GA", 5), ("BD", 4), ("SB", 2), ("QZ", 1)]
    for gnss, n in spec:
        for i in range(n):
            svid = {"GP": 1, "GL": 65, "GA": 301, "BD": 401, "SB": 131, "QZ": 193}[gnss] + i * 2 + 1
            sats.append(
                {
                    "gnss": gnss,
                    "svid": svid,
                    "prn": svid if gnss != "SB" else 44 + i,
                    "sigid": 0 if gnss in ("GP", "GA") else None,
                    "el0": rnd.uniform(5, 85),
                    "az0": rnd.uniform(0, 360),
                    "el_rate": rnd.uniform(-0.05, 0.05),
                    "az_rate": rnd.uniform(-0.12, 0.12),
                    "snr0": rnd.uniform(18, 48),
                    "phase": rnd.uniform(0, 6.28),
                    "health": rnd.choice([1, 1, 1, 0, None]),
                }
            )
    return sats


SATS = _make_sats()


def iso(ts: float, digits: int = 3) -> str:
    dt = datetime.fromtimestamp(ts, tz=UTC)
    s = dt.strftime("%Y-%m-%dT%H:%M:%S")
    frac = f"{ts - math.floor(ts):.{digits}f}"[1:] if digits else ""
    return f"{s}{frac}Z"


def wobble(t: float, period: float, phase: float = 0.0) -> float:
    """Deterministic smooth -1..1 wobble (sum of two incommensurate sines)."""
    return 0.6 * math.sin(2 * math.pi * t / period + phase) + 0.4 * math.sin(
        2 * math.pi * t / (period * 0.37) + phase * 1.7
    )


def ntp_at(t: float) -> dict:
    """chrony tracking snapshot at time t. Offsets in the hundreds-of-ns range."""
    sys_off = 3.9e-7 * wobble(t, 47.0) + 6e-8 * wobble(t, 6.3, 1.1)
    last_off = sys_off * 1.3 + 2.1e-7 * wobble(t, 11.0, 2.0)
    rms = 6.9e-7 + 1.4e-7 * wobble(t, 210.0, 0.4)
    freq = 17.97 + 0.35 * wobble(t, 900.0)
    collected = t - (t % 1.0) - 0.14
    ref_unix = math.floor(t / 8.0) * 8.0
    ref_txt = datetime.fromtimestamp(ref_unix, tz=UTC).strftime("%a %b %d %H:%M:%S %Y")
    fast_slow = "fast" if sys_off >= 0 else "slow"
    raw = (
        f"Reference ID    : 50505300 (PPS)\n"
        f"Stratum         : 1\n"
        f"Ref time (UTC)  : {ref_txt}\n"
        f"System time     : {abs(sys_off):.9f} seconds {fast_slow} of NTP time\n"
        f"Last offset     : {last_off:+.9f} seconds\n"
        f"RMS offset      : {rms:.9f} seconds\n"
        f"Frequency       : {abs(freq):.3f} ppm {'fast' if freq >= 0 else 'slow'}\n"
        f"Residual freq   : {0.004:+.3f} ppm\n"
        f"Skew            : {0.098:.3f} ppm\n"
        f"Root delay      : 0.000000001 seconds\n"
        f"Root dispersion : 0.000010513 seconds\n"
        f"Update interval : 8.0 seconds\n"
        f"Leap status     : Normal\n"
    )
    return {
        "available": True,
        "error": None,
        "collected_at": collected,
        "age_s": round(t - collected, 3),
        "reference_id": "50505300",
        "reference_name": "PPS",
        "stratum": 1,
        "ref_time": iso(ref_unix, 6),
        "ref_time_unix": ref_unix,
        "system_offset_s": sys_off,
        "last_offset_s": last_off,
        "rms_offset_s": abs(rms),
        "frequency_ppm": freq,
        "residual_freq_ppm": 0.004 + 0.002 * wobble(t, 130.0),
        "skew_ppm": 0.098 + 0.01 * wobble(t, 75.0),
        "root_delay_s": 1e-9,
        "root_dispersion_s": 1.0513e-5 + 2e-6 * wobble(t, 55.0),
        "update_interval_s": 8.0,
        "leap_status": "Normal",
        "synchronized": True,
        "raw": raw,
    }


def sats_at(t: float):
    out = []
    used = 0
    for s in SATS:
        el = s["el0"] + s["el_rate"] * (t % 3600) * 0.02
        el = 5 + (el - 5) % 85
        az = (s["az0"] + s["az_rate"] * (t % 3600) * 0.05) % 360
        snr = s["snr0"] + 3.5 * wobble(t, 23.0, s["phase"])
        snr = max(0.0, min(54.0, snr))
        is_used = el > 12 and snr > 24 and s["gnss"] != "SB"
        if is_used:
            used += 1
        out.append(
            {
                "gnss": s["gnss"],
                "gnss_name": GNSS_NAMES[s["gnss"]][0],
                "gnssid": GNSS_NAMES[s["gnss"]][1],
                "svid": s["svid"],
                "prn": s["prn"],
                "sigid": s["sigid"],
                "el_deg": round(el, 1),
                "az_deg": round(az, 1),
                "snr_db": round(snr, 1),
                "used": is_used,
                "health": s["health"],
            }
        )
    out.sort(key=lambda x: (x["gnssid"], x["svid"]))
    return out, used


def gps_at(t: float, degraded: bool = False) -> dict:
    if degraded:
        return {
            "available": False,
            "error": "connection refused (127.0.0.1:2947)",
            "connected": False,
            "collected_at": None,
            "age_s": None,
            "gpsd_version": None,
            "device": None,
            "devices": [],
            "fix": None,
            "position": None,
            "motion": None,
            "accuracy": None,
            "dop": None,
            "ecef": None,
            "time_offset": None,
            "gst": None,
            "satellites": {"seen": 0, "used": 0, "collected_at": None, "list": []},
            "cgps_time_offset_text": None,
        }
    lst, used = sats_at(t)
    hdop = 0.97 + 0.25 * wobble(t, 90.0)
    eph = 4.0 + 1.4 * wobble(t, 70.0, 0.9)
    lat = BASE_LAT + 2.0e-6 * wobble(t, 160.0)
    lon = BASE_LON + 2.4e-6 * wobble(t, 190.0, 1.3)
    fix_unix = math.floor(t)
    collected = t - 0.02
    pps = 1.234e-6 + 6e-7 * wobble(t, 30.0)
    return {
        "available": True,
        "error": None,
        "connected": True,
        "collected_at": collected,
        "age_s": round(t - collected, 3),
        "gpsd_version": "3.22",
        "device": {
            "path": "/dev/ttyAMA0",
            "driver": "MTK-3301",
            "subtype": "MTK-3301",
            "activated": iso(T_BOOT, 3),
            "bps": 9600,
            "cycle_s": 1.0,
        },
        "devices": [
            {
                "path": "/dev/ttyAMA0",
                "driver": "MTK-3301",
                "subtype": "MTK-3301",
                "activated": iso(T_BOOT, 3),
                "bps": 9600,
                "cycle_s": 1.0,
            },
            {
                "path": "/dev/pps0",
                "driver": "PPS",
                "subtype": None,
                "activated": iso(T_BOOT, 3),
                "bps": None,
                "cycle_s": None,
            },
        ],
        "fix": {
            "mode": 3,
            "mode_text": "3D",
            "status": 2,
            "status_text": "DGPS",
            "fix_text": "3D DGPS FIX",
            "fix_age_s": round(t - T_BOOT, 1),
            "time": iso(fix_unix, 3),
            "time_unix": float(fix_unix),
            "time_age_s": round(t - fix_unix, 9),
            "ept_s": 0.005,
            "leapseconds": None,
        },
        "position": {
            "lat": lat,
            "lon": lon,
            "alt_hae_m": 198.4 + 0.6 * wobble(t, 120.0),
            "alt_msl_m": 231.9 + 0.6 * wobble(t, 120.0),
            "geoid_sep_m": -33.5,
            "grid_square": "EN41er01",
        },
        "motion": {
            "speed_mps": max(0.0, 0.022 + 0.02 * wobble(t, 40.0)),
            "track_deg": (63.5 + 12 * wobble(t, 140.0)) % 360,
            "mag_track_deg": (64.4 + 12 * wobble(t, 140.0)) % 360,
            "mag_var_deg": -0.9,
            "climb_mps": 0.02 * wobble(t, 33.0),
        },
        "accuracy": {
            "epx_m": 2.1 + 0.5 * wobble(t, 61.0),
            "epy_m": 3.0 + 0.5 * wobble(t, 67.0),
            "epv_m": 4.3 + 0.8 * wobble(t, 73.0),
            "eph_m": eph,
            "sep_m": eph * 1.45,
            "eps_mps": 6.08,
            "epd_deg": None,
            "epc_mps": None,
            "ept_s": 0.005,
        },
        "dop": {
            "xdop": 0.56,
            "ydop": 0.81,
            "vdop": 0.75 + 0.2 * wobble(t, 95.0),
            "hdop": hdop,
            "pdop": 1.23 + 0.3 * wobble(t, 88.0),
            "tdop": 0.69,
            "gdop": 1.65,
        },
        "ecef": {
            "x_m": None,
            "y_m": None,
            "z_m": None,
            "vx_mps": None,
            "vy_mps": None,
            "vz_mps": None,
            "p_acc_m": None,
            "v_acc_mps": None,
        },
        "time_offset": {
            "source": "PPS",
            "offset_s": pps,
            "real_s": float(fix_unix),
            "clock_s": fix_unix + pps,
            "precision": -20,
            "measured_at": float(fix_unix),
            "pps_offset_s": pps,
            "toff_offset_s": None,
        },
        "gst": {
            "time_unix": float(fix_unix),
            "rms_m": 5.9 + 0.8 * wobble(t, 64.0),
            "major_m": 2.8 + 0.5 * wobble(t, 58.0),
            "minor_m": 2.2 + 0.3 * wobble(t, 71.0, 0.7),
            "orient_deg": (19.1 + 25 * wobble(t, 150.0)) % 180,
            "lat_err_m": 2.8 + 0.4 * wobble(t, 62.0),
            "lon_err_m": 2.3 + 0.4 * wobble(t, 69.0),
            "alt_err_m": 4.7 + 0.7 * wobble(t, 77.0),
        },
        "satellites": {
            "seen": len(lst),
            "used": used,
            "collected_at": collected,
            "list": lst,
        },
        "cgps_time_offset_text": f"{t - fix_unix:.9f} s",
    }


HISTORY_COLUMNS = [
    "t",
    "ntp_system_offset_s",
    "ntp_last_offset_s",
    "ntp_rms_offset_s",
    "ntp_frequency_ppm",
    "ntp_stratum",
    "gps_mode",
    "gps_sats_used",
    "gps_sats_seen",
    "gps_hdop",
    "gps_eph_m",
    "gps_time_offset_s",
    "lat",
    "lon",
    "alt_hae_m",
]


def history_rows(now: float, seconds: float, maxpoints: int, degraded: bool):
    seconds = max(60.0, min(86400.0, float(seconds)))
    maxpoints = max(2, min(20000, int(maxpoints)))
    step = max(5.0, seconds / maxpoints)
    n = int(seconds / step)
    rows = []
    for i in range(n):
        t = now - seconds + i * step
        ntp = ntp_at(t)
        # Punch a couple of gaps so the charts' null handling is exercised.
        gap = (int(t) // 600) % 17 == 3
        if gap:
            rows.append([round(t, 3)] + [None] * (len(HISTORY_COLUMNS) - 1))
            continue
        if degraded:
            rows.append(
                [
                    round(t, 3),
                    ntp["system_offset_s"],
                    ntp["last_offset_s"],
                    ntp["rms_offset_s"],
                    ntp["frequency_ppm"],
                    1,
                ]
                + [None] * 9
            )
            continue
        lst, used = sats_at(t)
        rows.append(
            [
                round(t, 3),
                ntp["system_offset_s"],
                ntp["last_offset_s"],
                ntp["rms_offset_s"],
                ntp["frequency_ppm"],
                1,
                3,
                used,
                len(lst),
                round(0.97 + 0.25 * wobble(t, 90.0), 3),
                round(4.0 + 1.4 * wobble(t, 70.0, 0.9), 2),
                1.234e-6 + 6e-7 * wobble(t, 30.0),
                round(BASE_LAT + 2.0e-5 * wobble(t, 1600.0), 7),
                round(BASE_LON + 2.4e-5 * wobble(t, 1900.0, 1.3), 7),
                round(198.4 + 0.6 * wobble(t, 120.0), 2),
            ]
        )
    return step, rows


RAW_SOURCES = """\
MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
#* PPS                           0   3   377     5   +994ns[+1002ns] +/-  820ns
^- 162.159.200.1                 3   6   377    41  +1237us[+1240us] +/-   28ms
^+ 23.131.160.7                  2   6   377    19   -412us[ -409us] +/- 8942us
^? 192.168.1.9                   0   6     0     -     +0ns[   +0ns] +/-    0ns
"""

RAW_SOURCESTATS = """\
Name/IP Address            NP  NR  Span  Frequency  Freq Skew  Offset  Std Dev
===============================================================================
PPS                        32  15   248      0.001      0.050   110ns   400ns
162.159.200.1              18   9   1041    -0.014      0.221  +1109us  1521us
23.131.160.7               24  13   1372     0.008      0.104   -388us   902us
"""


def sources_at(t: float) -> dict:
    return {
        "available": True,
        "error": None,
        "collected_at": t - 1.2,
        "age_s": 1.2,
        "sources": [
            {
                "mode": "#",
                "mode_text": "refclock",
                "state": "*",
                "state_text": "current best",
                "name": "PPS",
                "stratum": 0,
                "poll": 3,
                "reach": 377,
                "last_rx_s": int(t) % 8,
                "last_sample_offset_s": 9.94e-7,
                "last_sample_adjusted_offset_s": 1.002e-6,
                "last_sample_error_s": 8.2e-7,
            },
            {
                "mode": "^",
                "mode_text": "server",
                "state": "-",
                "state_text": "not combined",
                "name": "162.159.200.1",
                "stratum": 3,
                "poll": 6,
                "reach": 377,
                "last_rx_s": 41,
                "last_sample_offset_s": 1.237e-3,
                "last_sample_adjusted_offset_s": 1.240e-3,
                "last_sample_error_s": 2.8e-2,
            },
            {
                "mode": "^",
                "mode_text": "server",
                "state": "+",
                "state_text": "combined",
                "name": "23.131.160.7",
                "stratum": 2,
                "poll": 6,
                "reach": 377,
                "last_rx_s": 19,
                "last_sample_offset_s": -4.12e-4,
                "last_sample_adjusted_offset_s": -4.09e-4,
                "last_sample_error_s": 8.942e-3,
            },
            {
                "mode": "^",
                "mode_text": "server",
                "state": "?",
                "state_text": "unusable",
                "name": "192.168.1.9",
                "stratum": 0,
                "poll": 6,
                "reach": 0,
                "last_rx_s": None,
                "last_sample_offset_s": 0.0,
                "last_sample_adjusted_offset_s": 0.0,
                "last_sample_error_s": 0.0,
            },
        ],
        "sourcestats": [
            {
                "name": "PPS",
                "np": 32,
                "nr": 15,
                "span_s": 248,
                "frequency_ppm": 0.001,
                "freq_skew_ppm": 0.05,
                "offset_s": 1.1e-7,
                "std_dev_s": 4.0e-7,
            },
            {
                "name": "162.159.200.1",
                "np": 18,
                "nr": 9,
                "span_s": 1041,
                "frequency_ppm": -0.014,
                "freq_skew_ppm": 0.221,
                "offset_s": 1.109e-3,
                "std_dev_s": 1.521e-3,
            },
            {
                "name": "23.131.160.7",
                "np": 24,
                "nr": 13,
                "span_s": 1372,
                "frequency_ppm": 0.008,
                "freq_skew_ppm": 0.104,
                "offset_s": -3.88e-4,
                "std_dev_s": 9.02e-4,
            },
        ],
        "raw_sources": RAW_SOURCES,
        "raw_sourcestats": RAW_SOURCESTATS,
    }


# ------------------------------------------------------------- streaming
#
# A tiny stand-in for the real broadcaster in stratumtap/gpsd.py: one producer
# thread makes ~8 NMEA sentences/s (valid checksums), a gpsd TPV/SKY/PPS and an
# ntp snapshot every second, and stats every 10 s. Subscribers get a bounded
# queue; when it fills the OLDEST event is dropped, exactly like the contract in
# docs/api-contract.md says. Nothing here ever blocks the producer on a slow client.

STREAM_QUEUE = 500
STREAM_MAX_CLIENTS = 16
NMEA_RING_SIZE = 1000
NMEA_PER_S = 8
KEEPALIVE_S = 15.0

# One NMEA sentence per producer tick, in this repeating order (8 per second).
NMEA_CYCLE = ["RMC", "GGA", "GSA", "GSV", "GSV", "VTG", "GGA", "GLL"]

NMEA_RING: deque = deque(maxlen=NMEA_RING_SIZE)
_ring_lock = threading.Lock()
_subs: list[Subscriber] = []
_subs_lock = threading.Lock()
_next_cid = itertools.count(1)
_producer_started = threading.Event()

# 0 disables; N corrupts every Nth sentence so the red "checksum failed" row
# styling can be exercised (--bad-checksum-every).
BAD_CHECKSUM_EVERY = 0


class Subscriber:
    def __init__(self, cid: int, events: set):
        self.id = cid
        self.events = events
        self.q: queue.Queue = queue.Queue(maxsize=STREAM_QUEUE)
        self.sent = 0
        self.dropped = 0

    def offer(self, kind: str, payload) -> None:
        """Never blocks: a full queue loses its oldest event, not the newest."""
        try:
            self.q.put_nowait((kind, payload))
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self.q.get_nowait()
            self.dropped += 1
            with contextlib.suppress(queue.Full):
                self.q.put_nowait((kind, payload))


def _publish(kind: str, payload) -> None:
    with _subs_lock:
        subs = list(_subs)
    for sub in subs:
        if kind in sub.events:
            sub.offer(kind, payload)


def _publish_stats() -> None:
    with _subs_lock:
        subs = list(_subs)
    n = len(subs)
    now = time.time()
    for sub in subs:
        sub.offer(
            "stats",
            {
                "t": now,
                "sent": sub.sent,
                "dropped": sub.dropped,
                "queue_len": sub.q.qsize(),
                "clients": n,
            },
        )


# ---- NMEA construction ---------------------------------------------------


def nmea_checksum(body: str) -> int:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return cs


def _sentence(body: str, corrupt: bool = False) -> str:
    cs = nmea_checksum(body)
    if corrupt:
        cs ^= 0x5A
    return f"${body}*{cs:02X}"


def _dm(value: float, lat: bool):
    hemi = ("N" if value >= 0 else "S") if lat else ("E" if value >= 0 else "W")
    a = abs(value)
    deg = int(a)
    minutes = (a - deg) * 60.0
    width = 2 if lat else 3
    return f"{deg:0{width}d}{minutes:07.4f}", hemi


def _hhmmss(t: float) -> str:
    d = datetime.fromtimestamp(t, tz=UTC)
    return d.strftime("%H%M%S") + f".{int((t % 1) * 100):02d}"


def _ddmmyy(t: float) -> str:
    return datetime.fromtimestamp(t, tz=UTC).strftime("%d%m%y")


def nmea_body(t: float, kind: str, page: int) -> str:
    """The sentence body (between '$' and '*') for `kind` at time t."""
    g = gps_at(t)
    pos = g["position"]
    dop = g["dop"]
    mot = g["motion"]
    lat, lat_h = _dm(pos["lat"], True)
    lon, lon_h = _dm(pos["lon"], False)
    hhmmss = _hhmmss(t)
    knots = mot["speed_mps"] * 1.943844
    cog = mot["track_deg"]
    sats = g["satellites"]["list"]
    used = [s for s in sats if s["used"]]

    if kind == "RMC":
        var = abs(mot["mag_var_deg"])
        var_h = "E" if mot["mag_var_deg"] >= 0 else "W"
        return (
            f"GPRMC,{hhmmss},A,{lat},{lat_h},{lon},{lon_h},"
            f"{knots:.2f},{cog:.2f},{_ddmmyy(t)},{var:.1f},{var_h},D"
        )
    if kind == "GGA":
        return (
            f"GPGGA,{hhmmss},{lat},{lat_h},{lon},{lon_h},2,{len(used):02d},"
            f"{dop['hdop']:.2f},{pos['alt_msl_m']:.1f},M,{pos['geoid_sep_m']:.1f},M,,0000"
        )
    if kind == "GSA":
        prns = [str(s["prn"]) for s in used[:12]]
        prns += [""] * (12 - len(prns))
        return f"GPGSA,A,3,{','.join(prns)},{dop['pdop']:.2f},{dop['hdop']:.2f},{dop['vdop']:.2f}"
    if kind == "GSV":
        total = max(1, (len(sats) + 3) // 4)
        idx = (page % total) + 1
        chunk = sats[(idx - 1) * 4 : idx * 4]
        fields = "".join(
            f",{s['prn']:02d},{int(s['el_deg']):02d},{int(s['az_deg']):03d},{int(s['snr_db']):02d}"
            for s in chunk
        )
        return f"GPGSV,{total},{idx},{len(sats):02d}{fields}"
    if kind == "VTG":
        return (
            f"GPVTG,{cog:.2f},T,{mot['mag_track_deg']:.2f},M,{knots:.2f},N,{knots * 1.852:.2f},K,D"
        )
    # GLL
    return f"GPGLL,{lat},{lat_h},{lon},{lon_h},{hhmmss},A,D"


def nmea_record(t: float, kind: str, page: int, corrupt: bool = False) -> dict:
    body = nmea_body(t, kind, page)
    return {
        "t": round(t, 4),
        "line": _sentence(body, corrupt),
        "type": kind,
        "talker": "GP",
        "checksum_ok": not corrupt,
    }


def gpsd_objects(t: float) -> list:
    """One TPV, one SKY and one PPS, shaped like real gpsd JSON plus `_t`."""
    g = gps_at(t)
    pos, dop, mot, fix = g["position"], g["dop"], g["motion"], g["fix"]
    sats = g["satellites"]["list"]
    return [
        {
            "class": "TPV",
            "_t": round(t, 4),
            "device": "/dev/ttyAMA0",
            "mode": 3,
            "status": 2,
            "time": fix["time"],
            "ept": fix["ept_s"],
            "lat": round(pos["lat"], 7),
            "lon": round(pos["lon"], 7),
            "altHAE": round(pos["alt_hae_m"], 3),
            "altMSL": round(pos["alt_msl_m"], 3),
            "geoidSep": pos["geoid_sep_m"],
            "eph": round(g["accuracy"]["eph_m"], 3),
            "speed": round(mot["speed_mps"], 3),
            "track": round(mot["track_deg"], 4),
            "climb": round(mot["climb_mps"], 3),
        },
        {
            "class": "SKY",
            "_t": round(t, 4),
            "device": "/dev/ttyAMA0",
            "xdop": dop["xdop"],
            "ydop": dop["ydop"],
            "vdop": dop["vdop"],
            "hdop": dop["hdop"],
            "pdop": dop["pdop"],
            "nSat": len(sats),
            "uSat": sum(1 for s in sats if s["used"]),
            "satellites": [
                {
                    "PRN": s["prn"],
                    "el": s["el_deg"],
                    "az": s["az_deg"],
                    "ss": s["snr_db"],
                    "used": s["used"],
                    "gnssid": s["gnssid"],
                    "svid": s["svid"],
                }
                for s in sats[:6]
            ],
        },
        {
            "class": "PPS",
            "_t": round(t, 4),
            "device": "/dev/pps0",
            "real_sec": int(t),
            "real_nsec": 0,
            "clock_sec": int(t),
            "clock_nsec": int((g["time_offset"]["pps_offset_s"] or 0) * 1e9),
            "precision": -20,
        },
    ]


def _prefill_ring(now: float) -> None:
    """Give /api/v1/raw/nmea something to answer with immediately after boot."""
    step = 1.0 / NMEA_PER_S
    start = now - NMEA_RING_SIZE * step
    with _ring_lock:
        NMEA_RING.clear()
        for i in range(NMEA_RING_SIZE):
            t = start + i * step
            bad = bool(BAD_CHECKSUM_EVERY) and i % BAD_CHECKSUM_EVERY == 0
            NMEA_RING.append(nmea_record(t, NMEA_CYCLE[i % len(NMEA_CYCLE)], i // 3, bad))


def _producer(stop: threading.Event) -> None:
    i = 0
    last_second = -1.0
    last_stats = time.time()
    while not stop.is_set():
        t = time.time()
        bad = bool(BAD_CHECKSUM_EVERY) and i % BAD_CHECKSUM_EVERY == 0
        rec = nmea_record(t, NMEA_CYCLE[i % len(NMEA_CYCLE)], i // 3, bad)
        with _ring_lock:
            NMEA_RING.append(rec)
        _publish("nmea", rec)
        i += 1
        sec = math.floor(t)
        if sec != last_second:
            last_second = sec
            for obj in gpsd_objects(t):
                _publish("gpsd", obj)
            _publish("ntp", ntp_at(t))
        if t - last_stats >= 10.0:
            last_stats = t
            _publish_stats()
        stop.wait(1.0 / NMEA_PER_S)


def start_producer() -> threading.Event:
    stop = threading.Event()
    _prefill_ring(time.time())
    th = threading.Thread(target=_producer, args=(stop,), name="mock-nmea", daemon=True)
    th.start()
    _producer_started.set()
    return stop


class Handler(BaseHTTPRequestHandler):
    server_version = "stratumtap-mock/" + VERSION
    protocol_version = "HTTP/1.1"
    degraded = False
    latency_ms = 0.0

    # ---- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):  # quieter console
        if os.environ.get("MOCK_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, code, body: bytes, ctype: str, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _server_obj(self, t_recv: float, t0):
        return {
            "t_recv": t_recv,
            "t_send": time.time(),
            "t0": t0,
            "hostname": HOSTNAME,
            "version": VERSION,
            "demo": True,
            "uptime_s": round(time.time() - T_BOOT, 1),
        }

    def _json(self, payload: dict, t_recv: float, t0, code=200):
        payload["server"] = self._server_obj(t_recv, t0)
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        self._send(code, body, "application/json")

    # ---- chunked streaming ---------------------------------------------
    def _stream_headers(self, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _chunk(self, data: bytes):
        self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        with contextlib.suppress(Exception):
            self.wfile.flush()

    def _chunk_end(self):
        self.wfile.write(b"0\r\n\r\n")
        with contextlib.suppress(Exception):
            self.wfile.flush()

    def _peer_gone(self) -> bool:
        """EOF on the request half of the socket: the client went away.

        A half-closed peer still accepts writes until its TCP stack answers with
        RST, so waiting for BrokenPipeError can keep a dead subscriber (and one
        of the 16 client slots) alive for a while. Peeking costs nothing.
        """
        try:
            ready, _, _ = select.select([self.connection], [], [], 0)
            if not ready:
                return False
            return self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
        except BlockingIOError:
            return False
        except OSError:
            return True

    def _sse(self, eid: int, event: str, payload):
        body = f"id: {eid}\nevent: {event}\ndata: {json.dumps(payload, allow_nan=False)}\n\n"
        self._chunk(body.encode("utf-8"))

    # ---- routing --------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        t_recv = time.time()
        if Handler.latency_ms:
            time.sleep(Handler.latency_ms / 1000.0)
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        q = parse_qs(parsed.query)
        try:
            t0 = float(q["t0"][0]) if "t0" in q else None
        except (TypeError, ValueError):
            t0 = None
        if path.startswith("/api/"):
            try:
                self.api(path, q, t_recv, t0)
            except Exception as exc:  # keep the mock alive
                self._json({"detail": f"mock error: {exc}"}, t_recv, t0, code=500)
            return
        self.static(path)

    def api(self, path, q, t_recv, t0):
        now = time.time()
        deg = Handler.degraded
        if path == "/api/v1/time":
            n = ntp_at(now)
            self._json(
                {
                    "ntp_synchronized": True,
                    "ntp_system_offset_s": n["system_offset_s"],
                    "ntp_stratum": 1,
                },
                t_recv,
                t0,
            )
        elif path == "/api/v1/status":
            self._json({"ntp": ntp_at(now), "gps": gps_at(now, deg)}, t_recv, t0)
        elif path == "/api/v1/ntp":
            self._json({"ntp": ntp_at(now)}, t_recv, t0)
        elif path == "/api/v1/ntp/sources":
            self._json({"ntp_sources": sources_at(now)}, t_recv, t0)
        elif path == "/api/v1/gps":
            self._json({"gps": gps_at(now, deg)}, t_recv, t0)
        elif path == "/api/v1/gps/satellites":
            self._json({"satellites": gps_at(now, deg)["satellites"]}, t_recv, t0)
        elif path == "/api/v1/config":
            self._json(
                {
                    "default_refresh_s": 2,
                    "refresh_choices_s": [1, 2, 5, 10, 30, 60],
                    "tile_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                    "tile_attribution": "&copy; OpenStreetMap contributors",
                    "hostname": HOSTNAME,
                    "demo": True,
                    "history_interval_s": 5,
                    "history_size": 17280,
                    "version": VERSION,
                },
                t_recv,
                t0,
            )
        elif path == "/api/v1/health":
            self._json(
                {"ok": not deg, "ntp_ok": True, "gpsd_connected": not deg, "gps_fix": not deg},
                t_recv,
                t0,
            )
        elif path == "/api/v1/history":
            self.history(q, t_recv, t0, now, deg)
        elif path == "/api/v1/stream":
            self.stream(q, t_recv, t0)
        elif path == "/api/v1/stream/nmea.txt":
            self.stream_nmea_txt()
        elif path == "/api/v1/raw/nmea":
            try:
                n = int(float(q.get("n", ["200"])[0]))
            except (TypeError, ValueError):
                n = 200
            n = max(1, min(NMEA_RING_SIZE, n))
            with _ring_lock:
                lines = list(NMEA_RING)[-n:]
            self._json(
                {"count": len(lines), "ring_size": NMEA_RING_SIZE, "lines": lines},
                t_recv,
                t0,
            )
        elif path.startswith("/api/v1/raw/chronyc/"):
            which = path.rsplit("/", 1)[-1]
            text = {
                "tracking": ntp_at(now)["raw"],
                "sources": RAW_SOURCES,
                "sourcestats": RAW_SOURCESTATS,
            }.get(which)
            if text is None:
                self._json({"detail": "Not found"}, t_recv, t0, code=404)
            else:
                self._send(200, text.encode(), "text/plain; charset=utf-8")
        elif path == "/api/v1/raw/gpsd":
            g = gps_at(now, deg)
            self._send(
                200,
                json.dumps(
                    {
                        "VERSION": {
                            "class": "VERSION",
                            "release": "3.22",
                            "rev": "3.22",
                            "proto_major": 3,
                        },
                        "DEVICES": {
                            "class": "DEVICES",
                            "devices": [g["device"]] if g["device"] else [],
                        },
                        "TPV": {
                            "class": "TPV",
                            "device": "/dev/ttyAMA0",
                            "mode": 3,
                            "time": g["fix"]["time"] if g["fix"] else None,
                            "lat": g["position"]["lat"] if g["position"] else None,
                            "lon": g["position"]["lon"] if g["position"] else None,
                        },
                        "SKY": {
                            "class": "SKY",
                            "device": "/dev/ttyAMA0",
                            "hdop": g["dop"]["hdop"] if g["dop"] else None,
                            "satellites": g["satellites"]["list"][:4],
                        },
                        "PPS": {
                            "class": "PPS",
                            "device": "/dev/pps0",
                            "real_sec": int(now),
                            "clock_sec": int(now),
                            "precision": -20,
                        },
                    },
                    indent=2,
                ).encode(),
                "application/json",
            )
        else:
            self._json({"detail": "Not found"}, t_recv, t0, code=404)

    # ---- streaming endpoints -------------------------------------------
    def stream(self, q, t_recv, t0):
        """GET /api/v1/stream — SSE. See docs/api-contract.md "Streaming (v0.2)"."""
        if self.command == "HEAD":
            self._stream_headers("text/event-stream")
            self._chunk_end()
            self.close_connection = True
            return
        allowed = {"nmea", "gpsd", "ntp", "status"}
        raw = q.get("events", ["nmea,gpsd"])[0]
        events = [e.strip() for e in raw.split(",") if e.strip() in allowed]
        if not events:
            events = ["nmea", "gpsd"]
        try:
            interval = float(q.get("status_interval", ["2"])[0])
        except (TypeError, ValueError):
            interval = 2.0
        interval = max(1.0, min(60.0, interval))

        with _subs_lock:
            if len(_subs) >= STREAM_MAX_CLIENTS:
                self._send(
                    503,
                    json.dumps({"detail": "too many stream clients"}).encode(),
                    "application/json",
                )
                return
            sub = Subscriber(next(_next_cid), set(events))
            _subs.append(sub)

        self.close_connection = True
        eid = 0
        try:
            self._stream_headers("text/event-stream")
            eid += 1
            self._sse(
                eid,
                "hello",
                {
                    "client_id": sub.id,
                    "events": events,
                    "server": self._server_obj(t_recv, t0),
                    "queue": STREAM_QUEUE,
                },
            )
            next_status = time.time() + interval if "status" in sub.events else None
            while True:
                if self._peer_gone():
                    break
                timeout = KEEPALIVE_S
                if next_status is not None:
                    timeout = max(0.05, min(timeout, next_status - time.time()))
                try:
                    kind, payload = sub.q.get(timeout=timeout)
                except queue.Empty:
                    if next_status is not None and time.time() >= next_status:
                        next_status += interval
                        now = time.time()
                        eid += 1
                        self._sse(
                            eid,
                            "status",
                            {
                                "ntp": ntp_at(now),
                                "gps": gps_at(now, Handler.degraded),
                                "server": self._server_obj(now, t0),
                            },
                        )
                        sub.sent += 1
                        continue
                    self._chunk(b": keepalive\n\n")
                    continue
                eid += 1
                self._sse(eid, kind, payload)
                sub.sent += 1
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # the browser navigated away mid-write; nothing to report
        finally:
            with _subs_lock:
                if sub in _subs:
                    _subs.remove(sub)
            with contextlib.suppress(Exception):
                self._chunk_end()

    def stream_nmea_txt(self):
        """GET /api/v1/stream/nmea.txt — raw sentences, one per line."""
        if self.command == "HEAD":
            self._stream_headers("text/plain; charset=utf-8")
            self._chunk_end()
            self.close_connection = True
            return
        with _subs_lock:
            if len(_subs) >= STREAM_MAX_CLIENTS:
                self._send(
                    503,
                    json.dumps({"detail": "too many stream clients"}).encode(),
                    "application/json",
                )
                return
            sub = Subscriber(next(_next_cid), {"nmea"})
            _subs.append(sub)
        self.close_connection = True
        try:
            self._stream_headers("text/plain; charset=utf-8")
            while True:
                if self._peer_gone():
                    break
                try:
                    _kind, payload = sub.q.get(timeout=KEEPALIVE_S)
                except queue.Empty:
                    continue
                self._chunk((payload["line"] + "\r\n").encode("utf-8"))
                sub.sent += 1
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _subs_lock:
                if sub in _subs:
                    _subs.remove(sub)
            with contextlib.suppress(Exception):
                self._chunk_end()

    def history(self, q, t_recv, t0, now, deg):
        seconds = float(q.get("seconds", ["3600"])[0])
        maxp = int(float(q.get("max", ["720"])[0]))
        fmt = q.get("format", ["json"])[0]
        step, rows = history_rows(now, seconds, maxp, deg)
        if fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["t_iso"] + HISTORY_COLUMNS)
            for r in rows:
                w.writerow([iso(r[0], 3)] + ["" if v is None else v for v in r])
            self._send(
                200,
                buf.getvalue().encode(),
                "text/csv; charset=utf-8",
                {"Content-Disposition": 'attachment; filename="history.csv"'},
            )
            return
        self._json(
            {
                "interval_s": step,
                "requested_seconds": seconds,
                "points": len(rows),
                "columns": HISTORY_COLUMNS,
                "rows": rows,
            },
            t_recv,
            t0,
        )

    # ---- static ---------------------------------------------------------
    def static(self, path):
        rel = posixpath.normpath(path).lstrip("/")
        target = os.path.join(STATIC, rel) if rel else os.path.join(STATIC, "index.html")
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        # SPA fallback: anything that is not a real file becomes index.html
        if not os.path.isfile(target) or not os.path.abspath(target).startswith(STATIC):
            target = os.path.join(STATIC, "index.html")
        try:
            with open(target, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, body, ctype)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8089)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--degraded", action="store_true", help="report gps available:false")
    ap.add_argument("--latency-ms", type=float, default=0.0, help="artificial response delay")
    ap.add_argument(
        "--bad-checksum-every",
        type=int,
        default=0,
        help="corrupt every Nth NMEA checksum (0 = never) to exercise the red rows",
    )
    args = ap.parse_args()
    Handler.degraded = args.degraded
    Handler.latency_ms = args.latency_ms
    global BAD_CHECKSUM_EVERY
    BAD_CHECKSUM_EVERY = max(0, args.bad_checksum_every)
    mimetypes.add_type("application/javascript", ".js")
    stop = start_producer()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print(f"mock api on http://{args.host}:{args.port}/  (static: {STATIC})")
    print(f"  SSE: http://{args.host}:{args.port}/api/v1/stream?events=nmea,gpsd,ntp")
    print(f"  UI:  http://{args.host}:{args.port}/?rawstream=1#/detail  (auto-connects the panel)")
    try:
        with contextlib.suppress(KeyboardInterrupt):
            srv.serve_forever()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
