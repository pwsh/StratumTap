"""``chronyc`` subprocess runner, output parsers and the polling collector.

Parsers are pure functions so they can be unit-tested against captured output.
The collector runs in a background task; request handlers only ever read the
snapshot it publishes into the :class:`~stratumtap.state.StateStore`.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

from .models import NtpSnapshot, NtpSource, NtpSources, NtpSourceStat

log = logging.getLogger("stratumtap.chrony")

MODE_TEXT = {
    "^": "server",
    "=": "peer",
    "#": "refclock",
}

STATE_TEXT = {
    "*": "current best",
    "+": "combined",
    "-": "not combined",
    "x": "falseticker",
    "~": "too variable",
    "?": "unusable",
}

LEAP_NOT_SYNC = "Not synchronised"

#: ``chronyc -c sources`` prints uint32 max for LastRx when never received.
LAST_RX_NEVER = 4294967295.0

# "506 Cannot talk to daemon", "501 Not authorised", ...
_CHRONY_ERR_RE = re.compile(r"^\s*(\d{3}\s+.+?)\s*$", re.MULTILINE)


_UNSEEN = "__unseen__"  # sentinel for "never polled" so start-up is not logged as a recovery


class ChronycError(RuntimeError):
    """``chronyc`` could not be run, timed out, or reported an error."""


# --------------------------------------------------------------------------
# subprocess
# --------------------------------------------------------------------------


async def run_chronyc(args: list[str], bin: str = "chronyc", timeout: float = 3.0) -> str:
    """Run ``chronyc`` with *args* and return stdout.

    Never uses a shell. Raises :class:`ChronycError` on a missing binary, a
    non-zero exit, a timeout, or a ``NNN ...`` error line on stdout.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ChronycError(f"{bin} not found") from exc
    except OSError as exc:
        raise ChronycError(f"cannot run {bin}: {exc.strerror or exc}") from exc

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        with contextlib.suppress(ProcessLookupError):  # pragma: no cover - race with exit
            proc.kill()
        # Reap so we do not leak a zombie / "child process still running" warning.
        with contextlib.suppress(TimeoutError):  # pragma: no cover
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        # communicate() was canceled mid-read, so close the pipes explicitly.
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            with contextlib.suppress(Exception):  # pragma: no cover - best effort
                transport.close()
        raise ChronycError(f"{bin} {' '.join(args)} timed out after {timeout:g}s") from exc

    text = out.decode("utf-8", "replace")
    if proc.returncode != 0:
        detail = _first_error_line(text) or _first_error_line(err.decode("utf-8", "replace"))
        if not detail:
            detail = f"exit status {proc.returncode}"
        raise ChronycError(detail)

    detail = _first_error_line(text)
    if detail is not None:
        raise ChronycError(detail)
    return text


def _first_error_line(text: str) -> str | None:
    """Return chrony's ``NNN message`` error line if *text* is one."""
    stripped = text.strip()
    if not stripped:
        return None
    first = stripped.splitlines()[0].strip()
    m = _CHRONY_ERR_RE.match(first)
    if m and first[:3].isdigit() and first[0] in "45":
        return m.group(1)
    return None


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _f(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _i(value: str | None, base: int = 10) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value, base)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return None


def _iso(unix: float | None) -> str | None:
    if unix is None:
        return None
    dt = datetime.fromtimestamp(unix, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _synchronized(stratum: int | None, leap: str | None) -> bool:
    return stratum is not None and stratum < 16 and leap != LEAP_NOT_SYNC


def _csv_rows(text: str) -> list[list[str]]:
    """Split chronyc CSV output into rows, tolerating blank/short lines."""
    reader = csv.reader(io.StringIO(text))
    return [row for row in reader if row and any(cell.strip() for cell in row)]


# --------------------------------------------------------------------------
# tracking
# --------------------------------------------------------------------------

_TRACKING_CSV_FIELDS = 14


def parse_tracking_csv(text: str) -> dict[str, Any]:
    """Parse ``chronyc -c tracking`` into API field names.

    chrony 4.3 emits, in order::

        ref_id_hex, name, stratum, ref_time, current_correction, last_offset,
        rms_offset, freq_ppm, resid_freq_ppm, skew_ppm, root_delay,
        root_dispersion, update_interval, leap_status

    ``current_correction`` is positive when the system clock is SLOW, so the
    API's ``system_offset_s`` (positive = FAST) is its negation.
    """
    rows = _csv_rows(text)
    if not rows:
        raise ValueError("empty tracking CSV")
    row = rows[0]
    if len(row) < _TRACKING_CSV_FIELDS:
        raise ValueError(f"tracking CSV has {len(row)} fields, expected {_TRACKING_CSV_FIELDS}")

    stratum = _i(row[2])
    ref_time_unix = _f(row[3])
    correction = _f(row[4])
    leap = row[13].strip() or None

    return {
        "reference_id": (row[0].strip() or None),
        "reference_name": (row[1].strip() or None),
        "stratum": stratum,
        "ref_time": _iso(ref_time_unix),
        "ref_time_unix": ref_time_unix,
        "system_offset_s": (None if correction is None else -correction),
        "last_offset_s": _f(row[5]),
        "rms_offset_s": _f(row[6]),
        "frequency_ppm": _f(row[7]),
        "residual_freq_ppm": _f(row[8]),
        "skew_ppm": _f(row[9]),
        "root_delay_s": _f(row[10]),
        "root_dispersion_s": _f(row[11]),
        "update_interval_s": _f(row[12]),
        "leap_status": leap,
        "synchronized": _synchronized(stratum, leap),
    }


_REF_ID_RE = re.compile(r"^\s*([0-9A-Fa-f]+)\s*(?:\((.*)\))?\s*$")
_SIGNED_RE = re.compile(r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")


def _text_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


def _signed_seconds(value: str) -> float | None:
    """``0.000000372 seconds fast of NTP time`` -> +3.72e-7 (``slow`` -> negative)."""
    m = _SIGNED_RE.search(value)
    if not m:
        return None
    num = float(m.group(1))
    low = value.lower()
    if "slow" in low:
        return -abs(num)
    if "fast" in low:
        return abs(num)
    return num


def parse_tracking_text(text: str) -> dict[str, Any]:
    """Parse the human-readable ``chronyc tracking`` report.

    Missing or garbled lines simply yield ``None`` for that field.
    """
    fields = _text_lines(text)
    if not fields:
        raise ValueError("no 'key : value' lines in tracking output")

    out: dict[str, Any] = {
        "reference_id": None,
        "reference_name": None,
        "stratum": None,
        "ref_time": None,
        "ref_time_unix": None,
        "system_offset_s": None,
        "last_offset_s": None,
        "rms_offset_s": None,
        "frequency_ppm": None,
        "residual_freq_ppm": None,
        "skew_ppm": None,
        "root_delay_s": None,
        "root_dispersion_s": None,
        "update_interval_s": None,
        "leap_status": None,
        "synchronized": False,
    }

    ref = fields.get("Reference ID")
    if ref:
        m = _REF_ID_RE.match(ref)
        if m:
            out["reference_id"] = m.group(1)
            name = (m.group(2) or "").strip()
            out["reference_name"] = name or None
        else:
            out["reference_id"] = ref.split()[0]

    out["stratum"] = _i(fields.get("Stratum"))

    # "Ref time (UTC)" is the usual key; be tolerant of other suffixes.
    ref_time_key = next((k for k in fields if k.startswith("Ref time")), None)
    if ref_time_key:
        raw = fields[ref_time_key]
        try:
            dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
        except ValueError:
            dt = None
        if dt is not None:
            out["ref_time_unix"] = dt.timestamp()
            out["ref_time"] = _iso(out["ref_time_unix"])

    if "System time" in fields:
        out["system_offset_s"] = _signed_seconds(fields["System time"])
    if "Last offset" in fields:
        # chrony already prints this with the API's sign sense.
        m = _SIGNED_RE.search(fields["Last offset"])
        out["last_offset_s"] = float(m.group(1)) if m else None
    if "RMS offset" in fields:
        m = _SIGNED_RE.search(fields["RMS offset"])
        out["rms_offset_s"] = abs(float(m.group(1))) if m else None
    if "Frequency" in fields:
        raw = fields["Frequency"]
        m = _SIGNED_RE.search(raw)
        if m:
            num = float(m.group(1))
            low = raw.lower()
            if "slow" in low:
                num = -abs(num)
            elif "fast" in low:
                num = abs(num)
            out["frequency_ppm"] = num
    for key, name in (
        ("Residual freq", "residual_freq_ppm"),
        ("Skew", "skew_ppm"),
        ("Root delay", "root_delay_s"),
        ("Root dispersion", "root_dispersion_s"),
        ("Update interval", "update_interval_s"),
    ):
        if key in fields:
            m = _SIGNED_RE.search(fields[key])
            out[name] = float(m.group(1)) if m else None

    leap = fields.get("Leap status")
    out["leap_status"] = leap.strip() if leap else None
    out["synchronized"] = _synchronized(out["stratum"], out["leap_status"])
    return out


# --------------------------------------------------------------------------
# sources / sourcestats
# --------------------------------------------------------------------------

_SOURCES_CSV_FIELDS = 10
_SOURCESTATS_CSV_FIELDS = 8


def parse_sources_csv(text: str) -> list[dict[str, Any]]:
    """Parse ``chronyc -c sources``.

    Fields: mode, state, name, stratum, poll (log2 s), reach (octal), last_rx,
    adjusted offset, measured offset, error.
    """
    out: list[dict[str, Any]] = []
    for row in _csv_rows(text):
        if len(row) < _SOURCES_CSV_FIELDS:
            continue
        mode = row[0].strip() or None
        state = row[1].strip() or None
        poll = _i(row[4])
        last_rx = _f(row[6])
        if last_rx is not None and last_rx >= LAST_RX_NEVER:
            # chrony prints uint32 max for a source it has never received from.
            last_rx = None
        out.append(
            {
                "mode": mode,
                "mode_text": MODE_TEXT.get(mode or ""),
                "state": state,
                "state_text": STATE_TEXT.get(state or ""),
                "name": row[2].strip() or None,
                "stratum": _i(row[3]),
                "poll": poll,
                "poll_interval_s": (None if poll is None else 2**poll),
                # chrony prints Reach in octal even in CSV mode (client.c "%3o").
                "reach": _i(row[5], base=8),
                "reach_octal": (row[5].strip() or None),
                "last_rx_s": last_rx,
                "last_sample_adjusted_offset_s": _f(row[7]),
                "last_sample_offset_s": _f(row[8]),
                "last_sample_error_s": _f(row[9]),
            }
        )
    return out


def parse_sourcestats_csv(text: str) -> list[dict[str, Any]]:
    """Parse ``chronyc -c sourcestats``."""
    out: list[dict[str, Any]] = []
    for row in _csv_rows(text):
        if len(row) < _SOURCESTATS_CSV_FIELDS:
            continue
        out.append(
            {
                "name": row[0].strip() or None,
                "np": _i(row[1]),
                "nr": _i(row[2]),
                "span_s": _f(row[3]),
                "frequency_ppm": _f(row[4]),
                "freq_skew_ppm": _f(row[5]),
                "offset_s": _f(row[6]),
                "std_dev_s": _f(row[7]),
            }
        )
    return out


# --------------------------------------------------------------------------
# collector
# --------------------------------------------------------------------------


class ChronyCollector:
    """Polls ``chronyc`` and publishes snapshots into a :class:`StateStore`."""

    def __init__(self, settings, store, broadcaster=None) -> None:
        self._settings = settings
        self._store = store
        self._broadcaster = broadcaster
        self._tasks: list[asyncio.Task] = []
        self._tracking_error: str | None = _UNSEEN
        self._sources_error: str | None = _UNSEEN

    async def start(self) -> None:
        """Start the tracking and sources polling tasks."""
        if self._tasks:
            return
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._loop(self.poll_tracking, self._settings.chrony_poll_s)),
            loop.create_task(self._loop(self.poll_sources, self._settings.sources_poll_s)),
        ]

    async def stop(self) -> None:
        """Cancel the polling tasks and wait for them to finish."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - defensive
                log.debug("chrony task raised on shutdown", exc_info=True)
        self._tasks = []

    async def _loop(self, poll, interval: float) -> None:
        backoff = 0.0
        while True:
            try:
                await poll()
                backoff = 0.0
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive, must never die
                log.exception("unhandled error in chrony poll loop")
                backoff = min(30.0, interval if backoff <= 0 else backoff * 2)
            await asyncio.sleep(max(0.1, interval + backoff))

    def _note(self, attr: str, error: str | None) -> None:
        """Log at WARNING only when the availability state changes."""
        previous = getattr(self, attr)
        if previous == error:
            return
        setattr(self, attr, error)
        name = attr.replace("_error", "").lstrip("_")
        if error is None:
            if previous == _UNSEEN:
                log.info("chronyc %s available", name)
            else:
                log.warning("chronyc %s recovered", name)
        else:
            log.warning("chronyc %s unavailable: %s", name, error)

    async def poll_tracking(self) -> NtpSnapshot:
        """Run one tracking poll (CSV + human text) and publish the snapshot."""
        bin_ = self._settings.chronyc_bin
        csv_text: str | None = None
        csv_err: str | None = None
        try:
            csv_text = await run_chronyc(["-c", "tracking"], bin=bin_)
        except ChronycError as exc:
            csv_err = str(exc)

        raw_text: str | None = None
        text_err: str | None = None
        try:
            raw_text = await run_chronyc(["tracking"], bin=bin_)
        except ChronycError as exc:
            text_err = str(exc)

        fields: dict[str, Any] | None = None
        error: str | None = None
        if csv_text is not None:
            try:
                fields = parse_tracking_csv(csv_text)
            except ValueError as exc:
                error = f"cannot parse chronyc -c tracking: {exc}"
        else:
            error = csv_err

        if fields is None and raw_text is not None:
            try:
                fields = parse_tracking_text(raw_text)
                error = None
            except ValueError as exc:
                error = error or f"cannot parse chronyc tracking: {exc}"

        if fields is None:
            snapshot = NtpSnapshot(available=False, error=error or text_err or "chronyc failed")
        else:
            snapshot = NtpSnapshot(
                available=True,
                error=None,
                collected_at=_now(),
                raw=raw_text,
                **fields,
            )
        self._note("_tracking_error", snapshot.error)
        self._store.set_ntp(snapshot)
        if self._broadcaster is not None and snapshot.available:
            # Synchronous fan-out: the poll loop never waits for a stream client.
            self._broadcaster.publish("ntp", snapshot.model_dump(mode="json"))
        return snapshot

    async def poll_sources(self) -> NtpSources:
        """Run one sources/sourcestats poll and publish the snapshot."""
        bin_ = self._settings.chronyc_bin
        errors: list[str] = []

        async def _try(args: list[str]) -> str | None:
            try:
                return await run_chronyc(args, bin=bin_)
            except ChronycError as exc:
                errors.append(str(exc))
                return None

        sources_csv = await _try(["-c", "sources"])
        raw_sources = await _try(["sources", "-v"])
        stats_csv = await _try(["-c", "sourcestats"])
        raw_stats = await _try(["sourcestats", "-v"])

        if sources_csv is None and stats_csv is None:
            snapshot = NtpSources(
                available=False,
                error=errors[0] if errors else "chronyc failed",
            )
        else:
            snapshot = NtpSources(
                available=True,
                error=None,
                collected_at=_now(),
                sources=[NtpSource(**row) for row in parse_sources_csv(sources_csv or "")],
                sourcestats=[
                    NtpSourceStat(**row) for row in parse_sourcestats_csv(stats_csv or "")
                ],
                raw_sources=raw_sources,
                raw_sourcestats=raw_stats,
            )
        self._note("_sources_error", snapshot.error)
        self._store.set_ntp_sources(snapshot)
        return snapshot


def _now() -> float:
    return time.time()
