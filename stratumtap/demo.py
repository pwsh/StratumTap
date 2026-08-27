"""Synthetic but plausible data for development and demos (``STRATUMTAP_DEMO=1``).

The demo source builds the very same models the real collectors do; the GPS
side even goes through :class:`~stratumtap.gpsd.GpsdState` folding so that the demo
exercises the production code path (and ``/raw/gpsd`` looks real).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from datetime import UTC, datetime

from .gpsd import GpsdState
from .models import NtpSnapshot, NtpSource, NtpSources, NtpSourceStat
from .nmea import nmea_sentence, parse_nmea_line

log = logging.getLogger("stratumtap.demo")

TICK_S = 1.0

#: (gnssid, svid, prn, base elevation, base azimuth, base SNR, used)
_SAT_TEMPLATE = [
    (0, 5, 5, 29.0, 64.0, 33.0, True),
    (0, 10, 10, 15.0, 260.0, 45.0, True),
    (0, 15, 15, 75.0, 104.0, 24.0, True),
    (0, 16, 16, 6.0, 297.0, 29.0, True),
    (0, 18, 18, 73.0, 328.0, 40.0, True),
    (0, 20, 20, 46.0, 56.0, 28.0, True),
    (0, 23, 23, 44.0, 273.0, 46.0, True),
    (0, 24, 24, 15.0, 141.0, 19.0, True),
    (0, 27, 27, 8.0, 324.0, 31.0, True),
    (0, 29, 29, 38.0, 191.0, 25.0, False),
    (6, 71, 71, 21.0, 46.0, 30.0, True),
    (1, 133, 46, 28.0, 229.0, 47.0, False),
]

_TRACKING_TEMPLATE = """Reference ID    : 50505300 (PPS)
Stratum         : 1
Ref time (UTC)  : {ref_time}
System time     : {sys_abs:.9f} seconds {sys_word} of NTP time
Last offset     : {last:+.9f} seconds
RMS offset      : {rms:.9f} seconds
Frequency       : {freq_abs:.3f} ppm {freq_word}
Residual freq   : {resid:+.3f} ppm
Skew            : {skew:.3f} ppm
Root delay      : {root_delay:.9f} seconds
Root dispersion : {root_disp:.9f} seconds
Update interval : {interval:.1f} seconds
Leap status     : Normal
"""

_SOURCES_TEMPLATE = """MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
#* PPS                          0   3   377     5   {pps:+.9f}[{pps:+.9f}] +/- {pps_err:.9f}
^- time.cloudflare.com          3   6   377    41   {net:+.6f}[{net:+.6f}] +/- {net_err:.6f}
"""

_SOURCESTATS_HEADER = (
    "Name/IP Address            NP  NR  Span  Frequency  Freq Skew  Offset  Std Dev"
)
_SOURCESTATS_TEMPLATE = (
    _SOURCESTATS_HEADER
    + """
===============================================================================
PPS                        32  15   248     {f1:+.3f}      {s1:.3f}  {o1:+.3e}  {d1:.3e}
time.cloudflare.com        18   9   978     {f2:+.3f}      {s2:.3f}  {o2:+.3e}  {d2:.3e}
"""
)


class DemoSource:
    """Publishes slowly varying fake snapshots into a :class:`StateStore`."""

    def __init__(self, settings, store, seed: int | None = None, broadcaster=None) -> None:
        self._settings = settings
        self._store = store
        self._broadcaster = broadcaster
        self._rng = random.Random(seed if seed is not None else 20260826)
        self._task: asyncio.Task | None = None
        self._t0 = time.time()

        self._system_offset = 3.72e-7
        self._frequency = 17.97
        self._gps = GpsdState()
        self._gps.on_connect()
        self._gps.gpsd_version = "3.22"
        self._gps.fold(
            {"class": "VERSION", "release": "3.22", "rev": "3.22", "proto_major": 3},
            now=self._t0,
        )
        self._gps.fold(
            {
                "class": "DEVICES",
                "devices": [
                    {
                        "class": "DEVICE",
                        "path": "/dev/ttyAMA0",
                        "driver": "MTK-3301",
                        "subtype": "AXN_2.51_3339_17112000-0004",
                        "activated": _iso(self._t0, digits=3),
                        "flags": 1,
                        "native": 0,
                        "bps": 9600,
                        "parity": "N",
                        "stopbits": 1,
                        "cycle": 1.0,
                        "mincycle": 0.1,
                    },
                    {
                        "class": "DEVICE",
                        "path": "/dev/pps0",
                        "driver": "PPS",
                        "activated": _iso(self._t0, digits=3),
                    },
                ],
            },
            now=self._t0,
        )

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start ticking; publishes one snapshot immediately."""
        if self._task is not None:
            return
        self.tick()
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        """Stop the demo task."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover - defensive
            log.debug("demo task raised on shutdown", exc_info=True)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - must never die
                log.exception("demo tick failed")

    # -- generation --------------------------------------------------------

    def tick(self, now: float | None = None) -> None:
        """Generate and publish one demo sample (snapshots *and* stream events)."""
        if now is None:
            now = time.time()
        ntp = self._ntp(now)
        self._store.set_ntp(ntp)
        self._store.set_ntp_sources(self._sources(now))
        messages = self._gps_messages(now)
        for msg in messages:
            self._gps.fold(msg, now=now)
        self._store.set_gps(self._gps.snapshot(now))
        self._store.set_raw_gpsd(dict(self._gps.raw))

        # Raw NMEA goes into the same ring the live client fills, so /raw/nmea and
        # the SSE stream behave identically in demo mode. Real gpsd interleaves the
        # sentences with the JSON in no fixed order; the frontend must not care.
        for line in self.nmea_lines(now):
            parsed = parse_nmea_line(line)
            entry = {
                "t": now,
                "line": parsed["line"],
                "type": parsed["type"],
                "talker": parsed["talker"],
                "checksum_ok": parsed["checksum_ok"],
            }
            self._store.add_nmea(entry)
            self._publish("nmea", entry)
        for msg in messages:
            self._publish("gpsd", {**msg, "_t": now})
        self._publish("ntp", ntp.model_dump(mode="json"))

    def _publish(self, event: str, payload) -> None:
        if self._broadcaster is not None:
            self._broadcaster.publish(event, payload)

    # -- NMEA --------------------------------------------------------------

    def nmea_lines(self, now: float) -> list[str]:
        """One cycle of synthetic sentences (RMC, GGA, GSA, 3x GSV, VTG)."""
        tpv = self._gps.tpv
        sky = self._gps.sky
        if not tpv or not sky:
            return []
        sats = [s for s in sky.get("satellites") or [] if isinstance(s, dict)]
        used = [s for s in sats if s.get("used")]

        stamp = datetime.fromtimestamp(now, tz=UTC)
        hhmmss = stamp.strftime("%H%M%S.%f")[:-4]
        ddmmyy = stamp.strftime("%d%m%y")
        lat_dm, ns = _degrees_min(tpv.get("lat"), "NS")
        lon_dm, ew = _degrees_min(tpv.get("lon"), "EW", degree_digits=3)

        speed_mps = float(tpv.get("speed") or 0.0)
        knots = speed_mps * 1.943844
        kmh = speed_mps * 3.6
        track = float(tpv.get("track") or 0.0)
        magtrack = float(tpv.get("magtrack") or track)
        magvar = float(tpv.get("magvar") or 0.0)
        var_dir = "E" if magvar >= 0 else "W"
        mode = int(tpv.get("mode") or 1)
        alt = tpv.get("altMSL")
        geoid = tpv.get("geoidSep")

        lines = [
            nmea_sentence(
                f"GPRMC,{hhmmss},A,{lat_dm},{ns},{lon_dm},{ew},"
                f"{knots:.2f},{track:.2f},{ddmmyy},{abs(magvar):.1f},{var_dir},D"
            ),
            nmea_sentence(
                f"GPGGA,{hhmmss},{lat_dm},{ns},{lon_dm},{ew},2,{len(used):02d},"
                f"{_f(sky.get('hdop'), 2)},{_f(alt, 1)},M,{_f(geoid, 1)},M,,"
            ),
            nmea_sentence(
                "GPGSA,A,{mode},{prns},{pdop},{hdop},{vdop}".format(
                    mode=max(1, min(3, mode)),
                    prns=",".join(_prn_slots(used)),
                    pdop=_f(sky.get("pdop"), 2),
                    hdop=_f(sky.get("hdop"), 2),
                    vdop=_f(sky.get("vdop"), 2),
                )
            ),
        ]
        lines.extend(_gsv(sats))
        lines.append(
            nmea_sentence(f"GPVTG,{track:.2f},T,{magtrack:.2f},M,{knots:.2f},N,{kmh:.2f},K,D")
        )
        return lines

    # -- chrony ------------------------------------------------------------

    def _ntp(self, now: float) -> NtpSnapshot:
        rng = self._rng
        # Random walk inside +/- 1 us with the occasional microsecond spike.
        step = rng.gauss(0.0, 8e-8) - self._system_offset * 0.05
        self._system_offset = _clamp(self._system_offset + step, -1e-6, 1e-6)
        if rng.random() < 0.01:
            self._system_offset += rng.choice((-1.0, 1.0)) * rng.uniform(1e-6, 4e-6)
        self._frequency = _clamp(self._frequency + rng.gauss(0.0, 0.002), 17.96, 17.98)

        system_offset = self._system_offset
        last_offset = system_offset + rng.gauss(0.0, 3e-7)
        rms_offset = abs(system_offset) * 0.8 + 4e-7
        residual = rng.gauss(0.004, 0.002)
        skew = abs(rng.gauss(0.098, 0.01))
        root_delay = 1e-9
        root_dispersion = abs(rng.gauss(1.0513e-5, 1e-6))
        interval = 8.0
        ref_unix = math.floor(now) - rng.randint(0, 8)

        raw = _TRACKING_TEMPLATE.format(
            ref_time=datetime.fromtimestamp(ref_unix, tz=UTC).strftime("%a %b %d %H:%M:%S %Y"),
            sys_abs=abs(system_offset),
            sys_word="fast" if system_offset >= 0 else "slow",
            last=last_offset,
            rms=rms_offset,
            freq_abs=abs(self._frequency),
            freq_word="fast" if self._frequency >= 0 else "slow",
            resid=residual,
            skew=skew,
            root_delay=root_delay,
            root_disp=root_dispersion,
            interval=interval,
        )

        return NtpSnapshot(
            available=True,
            error=None,
            collected_at=now,
            reference_id="50505300",
            reference_name="PPS",
            stratum=1,
            ref_time=_iso(float(ref_unix)),
            ref_time_unix=float(ref_unix),
            system_offset_s=system_offset,
            last_offset_s=last_offset,
            rms_offset_s=rms_offset,
            frequency_ppm=self._frequency,
            residual_freq_ppm=residual,
            skew_ppm=skew,
            root_delay_s=root_delay,
            root_dispersion_s=root_dispersion,
            update_interval_s=interval,
            leap_status="Normal",
            synchronized=True,
            raw=raw,
        )

    def _sources(self, now: float) -> NtpSources:
        rng = self._rng
        pps = self._system_offset + rng.gauss(0.0, 2e-7)
        pps_err = abs(rng.gauss(8.2e-7, 1e-7))
        net = rng.gauss(0.0, 1.5e-3)
        net_err = abs(rng.gauss(4.0e-3, 5e-4))
        stats = (
            rng.gauss(0.001, 0.0005),
            abs(rng.gauss(0.05, 0.005)),
            rng.gauss(1.1e-7, 5e-8),
            abs(rng.gauss(4.0e-7, 5e-8)),
            rng.gauss(-0.02, 0.005),
            abs(rng.gauss(0.4, 0.05)),
            rng.gauss(-2.0e-4, 5e-5),
            abs(rng.gauss(1.5e-3, 2e-4)),
        )
        return NtpSources(
            available=True,
            error=None,
            collected_at=now,
            sources=[
                NtpSource(
                    mode="#",
                    mode_text="refclock",
                    state="*",
                    state_text="current best",
                    name="PPS",
                    stratum=0,
                    poll=3,
                    poll_interval_s=8,
                    reach=255,
                    reach_octal="377",
                    last_rx_s=float(rng.randint(0, 8)),
                    last_sample_offset_s=pps,
                    last_sample_adjusted_offset_s=pps,
                    last_sample_error_s=pps_err,
                ),
                NtpSource(
                    mode="^",
                    mode_text="server",
                    state="-",
                    state_text="not combined",
                    name="time.cloudflare.com",
                    stratum=3,
                    poll=6,
                    poll_interval_s=64,
                    reach=255,
                    reach_octal="377",
                    last_rx_s=float(rng.randint(0, 64)),
                    last_sample_offset_s=net,
                    last_sample_adjusted_offset_s=net,
                    last_sample_error_s=net_err,
                ),
            ],
            sourcestats=[
                NtpSourceStat(
                    name="PPS",
                    np=32,
                    nr=15,
                    span_s=248.0,
                    frequency_ppm=stats[0],
                    freq_skew_ppm=stats[1],
                    offset_s=stats[2],
                    std_dev_s=stats[3],
                ),
                NtpSourceStat(
                    name="time.cloudflare.com",
                    np=18,
                    nr=9,
                    span_s=978.0,
                    frequency_ppm=stats[4],
                    freq_skew_ppm=stats[5],
                    offset_s=stats[6],
                    std_dev_s=stats[7],
                ),
            ],
            raw_sources=_SOURCES_TEMPLATE.format(
                pps=pps, pps_err=pps_err, net=net, net_err=net_err
            ),
            raw_sourcestats=_SOURCESTATS_TEMPLATE.format(
                f1=stats[0],
                s1=stats[1],
                o1=stats[2],
                d1=stats[3],
                f2=stats[4],
                s2=stats[5],
                o2=stats[6],
                d2=stats[7],
            ),
        )

    # -- gpsd --------------------------------------------------------------

    def _mode(self, now: float) -> int:
        """3D, dropping to 2D for 10 s every ~3 minutes."""
        phase = (now - self._t0) % 180.0
        return 2 if phase < 10.0 else 3

    def _gps_messages(self, now: float) -> list[dict]:
        rng = self._rng
        elapsed = now - self._t0
        mode = self._mode(now)

        lat = self._settings.demo_lat + rng.gauss(0.0, 1e-6)
        lon = self._settings.demo_lon + rng.gauss(0.0, 1e-6)
        eph = _clamp(4.0 + rng.gauss(0.0, 0.8), 2.0, 6.0)
        epx = eph * 0.55
        epy = eph * 0.75
        epv = eph * 1.1
        ept = abs(rng.gauss(0.005, 0.001))

        tpv = {
            "class": "TPV",
            "device": "/dev/ttyAMA0",
            "mode": mode,
            "status": 2,
            "time": _iso(now, digits=3),
            "ept": round(ept, 6),
            "lat": round(lat, 9),
            "lon": round(lon, 9),
            "altHAE": round(198.4 + rng.gauss(0.0, 0.3), 3),
            "altMSL": round(231.9 + rng.gauss(0.0, 0.3), 3),
            "geoidSep": -33.5,
            "magvar": -0.9,
            "speed": round(abs(rng.gauss(0.022, 0.01)), 3),
            "climb": round(rng.gauss(0.0, 0.01), 3),
            "track": round((63.5 + rng.gauss(0.0, 3.0)) % 360.0, 2),
            "magtrack": round((64.4 + rng.gauss(0.0, 3.0)) % 360.0, 2),
            "eps": round(abs(rng.gauss(6.08, 0.5)), 2),
            "epc": round(abs(rng.gauss(12.0, 1.0)), 2),
            "epx": round(epx, 3),
            "epy": round(epy, 3),
            "epv": round(epv, 3),
            "eph": round(eph, 3),
            "sep": round(eph * 1.45, 3),
            "leapseconds": 18,
        }
        if mode < 3:
            for key in ("altHAE", "altMSL", "epv", "climb"):
                tpv.pop(key, None)

        sats = []
        used_count = 0
        for gnssid, svid, prn, el, az, ss, used in _SAT_TEMPLATE:
            drift = elapsed / 60.0
            el_now = _clamp(el + 2.0 * math.sin(drift + svid), 0.0, 90.0)
            az_now = (az + 3.0 * drift) % 360.0
            entry = {
                "gnssid": gnssid,
                "svid": svid,
                "PRN": prn,
                "el": round(el_now, 1),
                "az": round(az_now, 1),
                "ss": round(_clamp(ss + rng.gauss(0.0, 1.2), 0.0, 55.0), 1),
                "used": bool(used),
                "health": 1,
            }
            if gnssid == 0:
                entry["sigid"] = 0
            sats.append(entry)
            if used:
                used_count += 1

        major = _clamp(2.8 + rng.gauss(0.0, 0.2), 1.5, 5.0)
        minor = _clamp(2.2 + rng.gauss(0.0, 0.15), 1.0, 4.0)
        gst = {
            "class": "GST",
            "device": "/dev/ttyAMA0",
            "time": _iso(now, digits=3),
            "rms": round(_clamp(5.8 + rng.gauss(0.0, 0.3), 3.0, 9.0), 1),
            "major": round(major, 1),
            "minor": round(minor, 1),
            "orient": round((19.0 + rng.gauss(0.0, 1.5)) % 360.0, 1),
            "lat": round(major, 1),
            "lon": round(minor + 0.1, 1),
            "alt": round(major * 1.6, 1),
        }

        hdop = round(_clamp(0.97 + rng.gauss(0.0, 0.05), 0.5, 3.0), 2)
        sky = {
            "class": "SKY",
            "device": "/dev/ttyAMA0",
            "time": _iso(now, digits=3),
            "xdop": round(hdop * 0.58, 2),
            "ydop": round(hdop * 0.84, 2),
            "vdop": round(hdop * 0.77, 2),
            "hdop": hdop,
            "pdop": round(hdop * 1.27, 2),
            "tdop": round(hdop * 0.71, 2),
            "gdop": round(hdop * 1.70, 2),
            "nSat": len(sats),
            "uSat": used_count,
            "satellites": sats,
        }

        real = math.floor(now)
        pps_offset = rng.gauss(0.0, 8e-7)
        pps_offset = _clamp(pps_offset, -2e-6, 2e-6)
        pps = {
            "class": "PPS",
            "device": "/dev/ttyAMA0",
            "real_sec": real,
            "real_nsec": 0,
            "clock_sec": real,
            "clock_nsec": int(round(pps_offset * 1e9)) % 1_000_000_000,
            "precision": -20,
            "qErr": 0,
        }
        # Keep the exact offset regardless of the nsec wraparound above.
        if pps_offset < 0:
            pps["real_sec"] = real + 1
            pps["clock_nsec"] = int(round((1.0 + pps_offset) * 1e9))
        else:
            pps["clock_nsec"] = int(round(pps_offset * 1e9))

        toff_offset = _clamp(rng.gauss(0.4, 0.02), 0.30, 0.55)
        toff = {
            "class": "TOFF",
            "device": "/dev/ttyAMA0",
            "real_sec": real,
            "real_nsec": 0,
            "clock_sec": real,
            "clock_nsec": int(round(toff_offset * 1e9)),
        }
        pps_ttyama = dict(pps, device="/dev/ttyAMA0")
        return [tpv, gst, sky, pps, pps_ttyama, toff]


def _f(value, digits: int) -> str:
    """Format an optional number for an NMEA field (missing -> empty field)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return f"{float(value):.{digits}f}"


def _degrees_min(value, hemispheres: str, degree_digits: int = 2) -> tuple[str, str]:
    """Decimal degrees -> ``("5128.6741", "N")`` (NMEA ddmm.mmmm)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "", hemispheres[0]
    hemi = hemispheres[0] if value >= 0 else hemispheres[1]
    magnitude = abs(float(value))
    degrees = int(magnitude)
    minutes = (magnitude - degrees) * 60.0
    return f"{degrees:0{degree_digits}d}{minutes:07.4f}", hemi


def _prn_slots(used: list[dict]) -> list[str]:
    """The 12 PRN fields of a GSA sentence (blank when fewer are used)."""
    prns = [f"{int(s.get('PRN') or s.get('svid') or 0):02d}" for s in used][:12]
    return prns + [""] * (12 - len(prns))


def _gsv(sats: list[dict]) -> list[str]:
    """Split the satellite list into GSV sentences of at most four each."""
    chunks = [sats[i : i + 4] for i in range(0, len(sats), 4)] or [[]]
    total = len(chunks)
    out = []
    for index, chunk in enumerate(chunks, start=1):
        body = f"GPGSV,{total},{index},{len(sats):02d}"
        for sat in chunk:
            body += (
                f",{int(sat.get('PRN') or 0):02d},{int(sat.get('el') or 0):02d},"
                f"{int(sat.get('az') or 0):03d},{int(sat.get('ss') or 0):02d}"
            )
        out.append(nmea_sentence(body))
    return out


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def _iso(unix: float, digits: int = 6) -> str:
    text = datetime.fromtimestamp(unix, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")
    if digits < 6:
        text = text[: len(text) - (6 - digits)]
    return text + "Z"
