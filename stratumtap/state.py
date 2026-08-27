"""In-memory state: the latest snapshots plus a fixed-size history ring buffer."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime

from .models import GpsSnapshot, NtpSnapshot, NtpSources

log = logging.getLogger("stratumtap.state")

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

HistoryRow = tuple

#: The NMEA rate is estimated over this many seconds of arrivals.
NMEA_RATE_WINDOW_S = 5.0


class StateStore:
    """Holds the newest snapshots and samples them into a history ring buffer."""

    def __init__(self, settings) -> None:
        self._settings = settings
        self.started_at = time.time()
        self.ntp = NtpSnapshot(available=False, error="not collected yet")
        self.ntp_sources = NtpSources(available=False, error="not collected yet")
        self.gps = GpsSnapshot(available=False, connected=False, error="not connected")
        self.raw_gpsd: dict[str, dict] = {}
        #: Raw NMEA entries ``{t, line, type, talker, checksum_ok}``, oldest first.
        self.nmea_ring: deque[dict] = deque(
            maxlen=max(1, int(getattr(settings, "nmea_ring", 1000)))
        )
        self._nmea_times: deque[float] = deque()
        self.history: deque[HistoryRow] = deque(maxlen=max(1, int(settings.history_size)))
        self._task: asyncio.Task | None = None

    # -- uptime ------------------------------------------------------------

    def uptime_s(self, now: float | None = None) -> float:
        """Seconds since the store (i.e. the process) was created."""
        return max(0.0, (now if now is not None else time.time()) - self.started_at)

    # -- setters -----------------------------------------------------------

    def set_ntp(self, snapshot: NtpSnapshot) -> None:
        """Publish a new chrony tracking snapshot."""
        self.ntp = snapshot

    def set_ntp_sources(self, snapshot: NtpSources) -> None:
        """Publish a new chrony sources/sourcestats snapshot."""
        self.ntp_sources = snapshot

    def set_gps(self, snapshot: GpsSnapshot) -> None:
        """Publish a new gpsd snapshot."""
        self.gps = snapshot

    def set_raw_gpsd(self, raw: dict[str, dict]) -> None:
        """Publish the last raw message per gpsd class."""
        self.raw_gpsd = raw

    # -- raw NMEA ----------------------------------------------------------

    def add_nmea(self, entry: dict) -> None:
        """Append one parsed NMEA entry to the ring and update the rate estimate.

        The ring deliberately survives a gpsd reconnect: the last lines before the
        drop are exactly what someone debugging a receiver wants to see.
        """
        self.nmea_ring.append(entry)
        t = entry.get("t")
        self._nmea_times.append(float(t) if isinstance(t, (int, float)) else time.time())
        self._prune_nmea()

    def _prune_nmea(self, now: float | None = None) -> None:
        cutoff = (now if now is not None else time.time()) - NMEA_RATE_WINDOW_S
        times = self._nmea_times
        while times and times[0] < cutoff:
            times.popleft()

    def nmea_rate(self, now: float | None = None) -> float:
        """Sentences per second, counted over the last :data:`NMEA_RATE_WINDOW_S`."""
        self._prune_nmea(now)
        return len(self._nmea_times) / NMEA_RATE_WINDOW_S

    def nmea_lines(self, n: int) -> list[dict]:
        """The newest *n* ring entries, oldest first."""
        n = max(0, int(n))
        if n == 0:
            return []
        ring = self.nmea_ring
        if n >= len(ring):
            return list(ring)
        return list(ring)[-n:]

    # -- history -----------------------------------------------------------

    def sample(self, now: float | None = None) -> HistoryRow:
        """Append one history row for the current snapshots and return it."""
        if now is None:
            now = time.time()
        ntp = self.ntp
        gps = self.gps
        row: HistoryRow = (
            round(now, 3),
            ntp.system_offset_s if ntp.available else None,
            ntp.last_offset_s if ntp.available else None,
            ntp.rms_offset_s if ntp.available else None,
            ntp.frequency_ppm if ntp.available else None,
            ntp.stratum if ntp.available else None,
            gps.fix.mode if gps.available else None,
            gps.satellites.used if gps.available else None,
            gps.satellites.seen if gps.available else None,
            gps.dop.hdop if gps.available else None,
            gps.accuracy.eph_m if gps.available else None,
            gps.time_offset.offset_s if gps.available else None,
            gps.position.lat if gps.available else None,
            gps.position.lon if gps.available else None,
            gps.position.alt_hae_m if gps.available else None,
        )
        self.history.append(row)
        return row

    def history_rows(self, seconds: float, max_points: int) -> list[list]:
        """Rows newer than *seconds* ago, downsampled to at most *max_points*."""
        cutoff = time.time() - max(0.0, float(seconds))
        rows = [row for row in self.history if row[0] >= cutoff]
        max_points = max(1, int(max_points))
        if len(rows) > max_points:
            # Sample from the newest end so the most recent row always survives.
            step = math.ceil(len(rows) / max_points)
            rows = rows[::-1][::step][::-1]
        return [list(row) for row in rows]

    def history_csv(self, seconds: float, max_points: int) -> Iterator[str]:
        """Yield the same rows as CSV text, with ``t_iso`` as an extra first column."""
        yield ",".join(["t_iso", *HISTORY_COLUMNS]) + "\n"
        for row in self.history_rows(seconds, max_points):
            iso = datetime.fromtimestamp(row[0], tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            cells = [iso + "Z"]
            for value in row:
                cells.append("" if value is None else _fmt(value))
            yield ",".join(cells) + "\n"

    # -- sampler task ------------------------------------------------------

    async def start_sampler(self) -> None:
        """Start the background task that appends a history row periodically."""
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._sampler())

    async def stop_sampler(self) -> None:
        """Stop the history sampler task."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover - defensive
            log.debug("history sampler raised on shutdown", exc_info=True)

    async def _sampler(self) -> None:
        interval = max(0.1, float(self._settings.history_interval_s))
        while True:
            await asyncio.sleep(interval)
            try:
                self.sample()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - must never die
                log.exception("history sampler failed")


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        return repr(value)
    text = str(value)
    if any(c in text for c in ',"\n'):
        return '"' + text.replace('"', '""') + '"'
    return text
