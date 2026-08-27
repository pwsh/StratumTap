"""gpsd JSON client: message folding plus a reconnecting asyncio TCP session.

The folding is a pure-ish core (:class:`GpsdState` / :func:`fold_message`) so it
can be unit-tested without a socket; :class:`GpsdClient` only does I/O.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .geo import maidenhead
from .models import (
    GpsAccuracy,
    GpsDevice,
    GpsDop,
    GpsEcef,
    GpsFix,
    GpsGst,
    GpsMotion,
    GpsPosition,
    GpsSnapshot,
    GpsTimeOffset,
    Satellite,
    Satellites,
)
from .nmea import parse_nmea_line

log = logging.getLogger("stratumtap.gpsd")

WATCH_COMMAND = '?WATCH={"enable":true,"json":true,"pps":true,"nmea":true};\n'
DEVICES_COMMAND = "?DEVICES;\n"

READ_LIMIT = 64 * 1024
IDLE_TIMEOUT_S = 30.0
CONNECT_TIMEOUT_S = 5.0
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 30.0
TIME_OFFSET_PREFER_PPS_S = 5.0

MODE_TEXT = {0: "unknown", 1: "no fix", 2: "2D", 3: "3D"}

# gpsd ``status`` enum (gpsd never emits 0/1).
STATUS_TEXT = {
    2: "DGPS",
    3: "RTK",
    4: "RTK float",
    5: "DR",
    6: "GNSSDR",
    7: "FIXED",
    8: "SIM",
    9: "P(Y)",
}

# cgps's fix-label modifier for each status.
_STATUS_MOD = {
    2: "DGPS ",
    3: "RTK ",
    4: "RTK ",
    5: "DR ",
    6: "GNSSDR ",
    7: "FIXED ",
    8: "SIM ",
    9: "P(Y) ",
}

GNSS_LABELS = {
    0: ("GP", "GPS"),
    1: ("SB", "SBAS"),
    2: ("GA", "Galileo"),
    3: ("BD", "BeiDou"),
    4: ("IM", "IMES"),
    5: ("QZ", "QZSS"),
    6: ("GL", "GLONASS"),
    7: ("IR", "NavIC"),
}
GNSS_UNKNOWN = ("??", "unknown")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def mode_text(mode: int | None) -> str | None:
    """``0..3`` -> ``unknown``/``no fix``/``2D``/``3D``."""
    if mode is None:
        return None
    return MODE_TEXT.get(mode, "unknown")


def status_text(status: int | None) -> str | None:
    """gpsd ``status`` enum -> a short label (``None`` when absent/unknown)."""
    if status is None:
        return None
    return STATUS_TEXT.get(status)


def fix_text(mode: int | None, status: int | None) -> str | None:
    """cgps-style fix label, e.g. ``3D DGPS FIX`` or ``FIXED SURVEYED``."""
    if mode is None:
        return None
    mod = _STATUS_MOD.get(status, "") if status is not None else ""
    if mode == 3 and status == 7:
        return f"{mod}SURVEYED"
    if mode == 2:
        return f"2D {mod}FIX"
    if mode == 3:
        return f"3D {mod}FIX"
    return f"NO {mod}FIX"


def gnss_labels(gnssid: int | None) -> tuple[str, str]:
    """``gnssid`` -> (two-letter cgps label, full constellation name)."""
    if gnssid is None:
        return GNSS_UNKNOWN
    return GNSS_LABELS.get(gnssid, GNSS_UNKNOWN)


def parse_gps_time(value: Any) -> float | None:
    """Convert gpsd's ISO-8601 ``time`` string to Unix seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _num(msg: dict[str, Any], key: str) -> float | None:
    value = msg.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int(msg: dict[str, Any], key: str) -> int | None:
    value = msg.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _pair_offset(msg: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """Return ``(offset_s, real_s, clock_s)`` from a ``PPS``/``TOFF`` message."""
    real_s = msg.get("real_sec")
    real_ns = msg.get("real_nsec", 0)
    clock_s = msg.get("clock_sec")
    clock_ns = msg.get("clock_nsec", 0)
    if real_s is None or clock_s is None:
        return None, None, None
    real = float(real_s) + float(real_ns or 0) / 1e9
    clock = float(clock_s) + float(clock_ns or 0) / 1e9
    # Subtract the seconds first: at Unix-epoch magnitudes a float64 only
    # resolves ~240 ns, which would destroy a microsecond-scale PPS offset.
    offset = (float(clock_s) - float(real_s)) + (float(clock_ns or 0) - float(real_ns or 0)) / 1e9
    return offset, real, clock


# --------------------------------------------------------------------------
# folding state
# --------------------------------------------------------------------------


class GpsdState:
    """Accumulates gpsd messages into the data behind a :class:`GpsSnapshot`."""

    def __init__(self) -> None:
        self.connected: bool = False
        self.error: str | None = "not connected"
        self.gpsd_version: str | None = None

        self.devices: dict[str, dict[str, Any]] = {}

        self.tpv: dict[str, Any] = {}
        self.sky: dict[str, Any] = {}
        self.sky_at: float | None = None
        self.gst: dict[str, Any] = {}
        self.have_tpv: bool = False

        self.mode: int | None = None
        self.mode_changed_at: float | None = None

        self.collected_at: float | None = None

        self.pps: dict[str, Any] | None = None
        self.pps_at: float | None = None
        self.toff: dict[str, Any] | None = None
        self.toff_at: float | None = None

        self.raw: dict[str, dict[str, Any]] = {}

    # -- lifecycle ---------------------------------------------------------

    def on_connect(self) -> None:
        """Mark the session up and drop stale fix data (device info survives)."""
        self.connected = True
        self.error = None
        self.tpv = {}
        self.sky = {}
        self.sky_at = None
        self.gst = {}
        self.have_tpv = False
        self.mode = None
        self.mode_changed_at = None
        self.collected_at = None

    def on_disconnect(self, error: str | None) -> None:
        """Mark the session down with a human-readable *error*."""
        self.connected = False
        self.error = error
        self.have_tpv = False
        self.tpv = {}
        self.sky = {}
        self.sky_at = None
        self.gst = {}
        self.mode = None
        self.mode_changed_at = None
        # PPS/TOFF are only meaningful while the session is live; do not keep
        # reporting a stale offset after the connection drops.
        self.pps = None
        self.pps_at = None
        self.toff = None
        self.toff_at = None

    # -- folding -----------------------------------------------------------

    def fold(self, msg: dict[str, Any], now: float | None = None) -> None:
        """Fold one decoded gpsd message into the state."""
        if not isinstance(msg, dict):
            return
        cls = msg.get("class")
        if not isinstance(cls, str):
            return
        if now is None:
            now = time.time()
        self.raw[cls] = msg

        if cls == "VERSION":
            self.gpsd_version = msg.get("release") or msg.get("rev")
        elif cls == "DEVICES":
            devices = msg.get("devices")
            if isinstance(devices, list):
                self.devices = {}
                for dev in devices:
                    if isinstance(dev, dict) and dev.get("path"):
                        self.devices[str(dev["path"])] = dev
        elif cls == "DEVICE":
            self._fold_device(msg)
        elif cls == "WATCH":
            pass
        elif cls == "TPV":
            self._fold_tpv(msg, now)
        elif cls == "SKY":
            self._fold_sky(msg, now)
        elif cls == "GST":
            self.gst = msg
        elif cls == "PPS":
            self.pps = msg
            self.pps_at = now
        elif cls == "TOFF":
            self.toff = msg
            self.toff_at = now
        elif cls == "ERROR":
            log.warning("gpsd ERROR: %s", msg.get("message"))

    def _fold_sky(self, msg: dict[str, Any], now: float) -> None:
        """Merge a ``SKY`` message.

        gpsd 3.25+ emits a DOP-only ``SKY`` (from ``$GPGSA``, no ``satellites``
        key) between the full ones (from ``$GPGSV``). Replacing wholesale would
        blank the satellite list several times a second, so keep the previous
        list and counts when the new message carries none.
        """
        if "satellites" not in msg and self.sky.get("satellites") is not None:
            merged = dict(self.sky)
            merged.update(msg)
            merged["satellites"] = self.sky["satellites"]
            for key in ("nSat", "uSat"):
                if key not in msg and key in self.sky:
                    merged[key] = self.sky[key]
            msg = merged
        self.sky = msg
        self.sky_at = now
        self.collected_at = now

    def _fold_device(self, msg: dict[str, Any]) -> None:
        path = msg.get("path")
        if not path:
            return
        path = str(path)
        activated = msg.get("activated")
        removed = activated in (0, 0.0, "0")
        if removed:
            self.devices.pop(path, None)
            return
        self.devices[path] = msg

    def _fold_tpv(self, msg: dict[str, Any], now: float) -> None:
        self.tpv = msg
        self.have_tpv = True
        self.collected_at = now
        mode = _int(msg, "mode")
        if mode != self.mode or self.mode_changed_at is None:
            self.mode = mode
            self.mode_changed_at = now

    # -- snapshot ----------------------------------------------------------

    def device_list(self) -> list[GpsDevice]:
        """Every device gpsd has told us about, in the order it reported them."""
        return [_device_model(dev) for dev in self.devices.values()]

    def active_device(self) -> dict[str, Any]:
        """The device named by the last TPV, else the first non-PPS device."""
        tpv_device = self.tpv.get("device")
        if tpv_device is not None:
            dev = self.devices.get(str(tpv_device))
            if dev is not None:
                return dev
        for dev in self.devices.values():
            if str(dev.get("driver") or "").upper() != "PPS":
                return dev
        return next(iter(self.devices.values()), {})

    def device(self) -> GpsDevice:
        """The active device as a :class:`GpsDevice`."""
        return _device_model(self.active_device())

    def gst_model(self) -> GpsGst | None:
        """The last ``GST`` message, or ``None`` if the receiver never sent one."""
        if not self.gst:
            return None
        return GpsGst(
            time_unix=parse_gps_time(self.gst.get("time")),
            rms_m=_num(self.gst, "rms"),
            major_m=_num(self.gst, "major"),
            minor_m=_num(self.gst, "minor"),
            orient_deg=_num(self.gst, "orient"),
            lat_err_m=_num(self.gst, "lat"),
            lon_err_m=_num(self.gst, "lon"),
            alt_err_m=_num(self.gst, "alt"),
        )

    def time_offset(self, now: float) -> GpsTimeOffset:
        """Pick PPS or TOFF as the current GPS-vs-system offset source."""
        pps_offset, pps_real, pps_clock = _pair_offset(self.pps or {})
        toff_offset, toff_real, toff_clock = _pair_offset(self.toff or {})

        pps_recent = self.pps_at is not None and (now - self.pps_at) <= TIME_OFFSET_PREFER_PPS_S
        toff_recent = self.toff_at is not None and (now - self.toff_at) <= TIME_OFFSET_PREFER_PPS_S

        source: str | None
        if self.pps_at is None and self.toff_at is None:
            source = None
        elif pps_recent and toff_recent:
            source = "PPS"
        elif self.pps_at is None:
            source = "TOFF"
        elif self.toff_at is None:
            source = "PPS"
        else:
            source = "PPS" if self.pps_at >= self.toff_at else "TOFF"

        if source == "PPS":
            chosen, offset, real, clock, at = (
                self.pps or {},
                pps_offset,
                pps_real,
                pps_clock,
                self.pps_at,
            )
        elif source == "TOFF":
            chosen, offset, real, clock, at = (
                self.toff or {},
                toff_offset,
                toff_real,
                toff_clock,
                self.toff_at,
            )
        else:
            chosen, offset, real, clock, at = {}, None, None, None, None

        return GpsTimeOffset(
            source=source,
            offset_s=offset,
            real_s=real,
            clock_s=clock,
            precision=_int(chosen, "precision"),
            measured_at=at,
            pps_offset_s=pps_offset,
            toff_offset_s=toff_offset,
        )

    def satellites(self) -> Satellites:
        """The ``SKY`` satellite list, sorted the way cgps orders it."""
        sky = self.sky
        raw = sky.get("satellites")
        entries: list[Satellite] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                gnssid = _int(item, "gnssid")
                short, full = gnss_labels(gnssid)
                used = item.get("used")
                entries.append(
                    Satellite(
                        gnss=short,
                        gnss_name=full,
                        gnssid=gnssid,
                        svid=_int(item, "svid"),
                        prn=_int(item, "PRN"),
                        sigid=_int(item, "sigid"),
                        el_deg=_num(item, "el"),
                        az_deg=_num(item, "az"),
                        snr_db=_num(item, "ss"),
                        used=bool(used) if isinstance(used, bool) else None,
                        health=_int(item, "health"),
                    )
                )
        entries.sort(key=_sat_sort_key)

        seen = _int(sky, "nSat")
        if seen is None and isinstance(raw, list):
            seen = len(entries)
        used_count = _int(sky, "uSat")
        if used_count is None and isinstance(raw, list):
            used_count = sum(1 for s in entries if s.used)

        collected = parse_gps_time(sky.get("time")) if sky else None
        if collected is None and sky:
            # The MTK-3301 omits SKY.time; fall back to when the message arrived.
            collected = self.sky_at
        return Satellites(seen=seen, used=used_count, collected_at=collected, list=entries)

    def snapshot(self, now: float | None = None) -> GpsSnapshot:
        """Build the API :class:`GpsSnapshot` for the current state."""
        if now is None:
            now = time.time()
        tpv = self.tpv
        sky = self.sky

        mode = _int(tpv, "mode")
        status = _int(tpv, "status")
        fix_age = None
        if self.mode_changed_at is not None:
            fix_age = max(0.0, now - self.mode_changed_at)
        time_unix = parse_gps_time(tpv.get("time"))

        alt_hae = _num(tpv, "altHAE")
        if alt_hae is None:
            alt_hae = _num(tpv, "alt")
        lat = _num(tpv, "lat")
        lon = _num(tpv, "lon")
        grid = maidenhead(lat, lon) if lat is not None and lon is not None else None

        fix = GpsFix(
            mode=mode,
            mode_text=mode_text(mode),
            status=status,
            status_text=status_text(status),
            fix_text=fix_text(mode, status),
            fix_age_s=fix_age,
            time=tpv.get("time") if isinstance(tpv.get("time"), str) else None,
            time_unix=time_unix,
            time_age_s=None,  # filled in by the API layer at response time
            ept_s=_num(tpv, "ept"),
            leapseconds=_int(tpv, "leapseconds"),
        )

        error = self.error
        if self.connected and error is None:
            # Not failures, but the UI should say why there is no fix data.
            if not self.devices:
                error = "connected to gpsd, but it reports no GPS device"
            elif not self.have_tpv:
                error = "connected to gpsd, waiting for the first position report"

        return GpsSnapshot(
            available=self.connected and self.have_tpv,
            error=error,
            connected=self.connected,
            collected_at=self.collected_at,
            age_s=None,
            gpsd_version=self.gpsd_version,
            device=self.device(),
            devices=self.device_list(),
            fix=fix,
            position=GpsPosition(
                lat=lat,
                lon=lon,
                alt_hae_m=alt_hae,
                alt_msl_m=_num(tpv, "altMSL"),
                geoid_sep_m=_num(tpv, "geoidSep"),
                grid_square=grid,
            ),
            motion=GpsMotion(
                speed_mps=_num(tpv, "speed"),
                track_deg=_num(tpv, "track"),
                mag_track_deg=_num(tpv, "magtrack"),
                mag_var_deg=_num(tpv, "magvar"),
                climb_mps=_num(tpv, "climb"),
            ),
            accuracy=GpsAccuracy(
                epx_m=_num(tpv, "epx"),
                epy_m=_num(tpv, "epy"),
                epv_m=_num(tpv, "epv"),
                eph_m=_num(tpv, "eph"),
                sep_m=_num(tpv, "sep"),
                eps_mps=_num(tpv, "eps"),
                epd_deg=_num(tpv, "epd"),
                epc_mps=_num(tpv, "epc"),
                ept_s=_num(tpv, "ept"),
            ),
            dop=GpsDop(
                xdop=_num(sky, "xdop"),
                ydop=_num(sky, "ydop"),
                vdop=_num(sky, "vdop"),
                hdop=_num(sky, "hdop"),
                pdop=_num(sky, "pdop"),
                tdop=_num(sky, "tdop"),
                gdop=_num(sky, "gdop"),
            ),
            ecef=GpsEcef(
                x_m=_num(tpv, "ecefx"),
                y_m=_num(tpv, "ecefy"),
                z_m=_num(tpv, "ecefz"),
                vx_mps=_num(tpv, "ecefvx"),
                vy_mps=_num(tpv, "ecefvy"),
                vz_mps=_num(tpv, "ecefvz"),
                p_acc_m=_num(tpv, "ecefpAcc"),
                v_acc_mps=_num(tpv, "ecefvAcc"),
            ),
            time_offset=self.time_offset(now),
            gst=self.gst_model(),
            satellites=self.satellites(),
            cgps_time_offset_text=None,  # filled in by the API layer at response time
        )


def _device_model(dev: dict[str, Any]) -> GpsDevice:
    """Convert a gpsd ``DEVICE`` dict into the API model."""
    activated = dev.get("activated")
    return GpsDevice(
        path=dev.get("path"),
        driver=dev.get("driver"),
        subtype=dev.get("subtype"),
        activated=activated if isinstance(activated, str) else None,
        bps=_int(dev, "bps"),
        cycle_s=_num(dev, "cycle"),
    )


def _sat_sort_key(sat: Satellite) -> tuple[int, int, int]:
    gnssid = sat.gnssid if sat.gnssid is not None else 99
    primary = sat.svid if sat.svid is not None else (sat.prn if sat.prn is not None else 9999)
    prn = sat.prn if sat.prn is not None else 9999
    return (gnssid, primary, prn)


def fold_message(state: GpsdState, msg: dict[str, Any], now: float | None = None) -> GpsdState:
    """Fold *msg* into *state* and return it (convenience for tests)."""
    state.fold(msg, now)
    return state


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------


class GpsdClient:
    """Persistent, reconnecting gpsd JSON session."""

    def __init__(self, settings, store, broadcaster=None) -> None:
        self._settings = settings
        self._store = store
        self._broadcaster = broadcaster
        self.state = GpsdState()
        self._task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._last_error: str | None = None

    @property
    def _where(self) -> str:
        return f"{self._settings.gpsd_host}:{self._settings.gpsd_port}"

    async def start(self) -> None:
        """Start the background session task."""
        if self._task is None:
            self._publish()
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        """Cancel the session task and close the socket."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - defensive
                log.debug("gpsd task raised on shutdown", exc_info=True)
        await self._close_writer()

    async def _close_writer(self) -> None:
        writer, self._writer = self._writer, None
        if writer is None:
            return
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        except (OSError, TimeoutError):  # pragma: no cover - best effort
            pass

    def _publish(self, now: float | None = None) -> None:
        self._store.set_gps(self.state.snapshot(now))
        self._store.set_raw_gpsd(dict(self.state.raw))

    def _note(self, error: str | None) -> None:
        if error == self._last_error:
            return
        self._last_error = error
        if error is None:
            log.info("gpsd connected (%s)", self._where)
        else:
            log.warning("gpsd unavailable: %s", error)

    async def _run(self) -> None:
        backoff = BACKOFF_MIN_S
        while True:
            try:
                await self._session()
                backoff = BACKOFF_MIN_S
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.on_disconnect(self._describe(exc))
                self._note(self.state.error)
                self._publish()
            else:
                self.state.on_disconnect(f"connection closed by gpsd ({self._where})")
                self._note(self.state.error)
                self._publish()
            await self._close_writer()
            await asyncio.sleep(backoff)
            backoff = min(BACKOFF_MAX_S, backoff * 2)

    def _describe(self, exc: BaseException) -> str:
        if isinstance(exc, ConnectionRefusedError):
            return f"connection refused ({self._where})"
        if isinstance(exc, TimeoutError):
            return f"timeout ({self._where})"
        if isinstance(exc, ConnectionResetError):
            return f"connection reset ({self._where})"
        if isinstance(exc, OSError):
            return f"{exc.strerror or exc} ({self._where})"
        return f"{type(exc).__name__}: {exc}"

    async def _session(self) -> None:
        """One connect/watch/read cycle. Returns normally on EOF."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self._settings.gpsd_host, self._settings.gpsd_port, limit=READ_LIMIT
            ),
            timeout=CONNECT_TIMEOUT_S,
        )
        self._writer = writer
        self.state.on_connect()
        self._note(None)

        # gpsd greets us with an unsolicited VERSION line.
        line = await asyncio.wait_for(reader.readline(), timeout=CONNECT_TIMEOUT_S)
        if line:
            self._handle_line(line)

        writer.write(WATCH_COMMAND.encode("ascii"))
        await asyncio.wait_for(writer.drain(), timeout=CONNECT_TIMEOUT_S)
        self._publish()

        idle_strikes = 0
        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=IDLE_TIMEOUT_S)
            except TimeoutError:
                # A receiver with no device attached is legitimately silent. Probe
                # gpsd once before concluding the session is dead; a healthy daemon
                # answers ?DEVICES; immediately.
                idle_strikes += 1
                if idle_strikes > 1:
                    raise
                writer.write(DEVICES_COMMAND.encode("ascii"))
                await asyncio.wait_for(writer.drain(), timeout=CONNECT_TIMEOUT_S)
                continue
            idle_strikes = 0
            if not line:
                return  # EOF -> reconnect
            # _handle_line() decides whether the line warrants a snapshot rebuild;
            # raw NMEA never does (streaming contract rule 3).
            self._handle_line(line)

    def _handle_line(self, line: bytes) -> None:
        """Dispatch one line from gpsd: raw NMEA to the ring, JSON to the folder.

        With ``"nmea":true`` in the watch, raw sentences and JSON objects are
        interleaved on the same socket in no guaranteed order (a sentence may
        arrive before *or* after the JSON describing the same cycle) — neither
        path may assume anything about the other.
        """
        text = line.decode("utf-8", "replace").strip()
        if not text:
            return
        if text[0] in "$!":
            self._handle_nmea(text)
            return  # rule 3: raw NMEA never rebuilds the snapshot
        try:
            msg = json.loads(text)
        except ValueError:
            log.debug("gpsd: undecodable line %r", text[:120])
            return
        try:
            self.state.fold(msg)
        except Exception:  # pragma: no cover - folding must never kill the task
            log.exception("gpsd: error folding %s message", msg.get("class"))
        if self._broadcaster is not None and isinstance(msg, dict):
            self._broadcaster.publish("gpsd", {**msg, "_t": time.time()})
        self._publish()

    def _handle_nmea(self, text: str, now: float | None = None) -> None:
        """Append one raw sentence to the ring buffer and broadcast it."""
        if now is None:
            now = time.time()
        parsed = parse_nmea_line(text)
        entry = {
            "t": now,
            "line": parsed["line"],
            "type": parsed["type"],
            "talker": parsed["talker"],
            "checksum_ok": parsed["checksum_ok"],
        }
        self._store.add_nmea(entry)
        if self._broadcaster is not None:
            self._broadcaster.publish("nmea", entry)
