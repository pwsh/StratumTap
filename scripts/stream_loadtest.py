#!/usr/bin/env python3
"""Load-test the SSE stream and prove the local collectors are not starved.

Opens N well-behaved SSE readers plus K *stalled* clients (connect, never read —
the worst case for a broadcaster), while a poller times ``/api/v1/status`` and a
sampler watches ``/api/v1/health`` for event-loop lag and the data ages that show
whether chrony/gpsd collection kept its cadence.

Standard library only, so it runs from any machine:

    python3 scripts/stream_loadtest.py --base http://192.0.2.10:8080 \
        --readers 12 --stalled 2 --seconds 60
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import statistics
import threading
import time
from urllib.parse import urlparse


def _conn(base: str, timeout: float) -> http.client.HTTPConnection:
    u = urlparse(base)
    return http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)


def _get_json(base: str, path: str) -> tuple[float, dict]:
    c = _conn(base, 10)
    t0 = time.perf_counter()
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    dt = time.perf_counter() - t0
    c.close()
    return dt, json.loads(body)


class Reader(threading.Thread):
    """A normal SSE consumer that counts events and stats."""

    def __init__(self, base: str, events: str, stop: threading.Event) -> None:
        super().__init__(daemon=True)
        self.base, self.events, self.stop = base, events, stop
        self.count = 0
        self.last_stats: dict = {}
        self.error: str | None = None

    def run(self) -> None:
        try:
            c = _conn(self.base, 30)
            c.request("GET", f"/api/v1/stream?events={self.events}")
            r = c.getresponse()
            if r.status != 200:
                self.error = f"HTTP {r.status}"
                return
            event = None
            while not self.stop.is_set():
                line = r.readline()
                if not line:
                    self.error = "EOF"
                    return
                line = line.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    self.count += 1
                    if event == "stats":
                        with contextlib.suppress(ValueError):
                            self.last_stats = json.loads(line[5:].strip())
                elif line == "":
                    event = None
            c.close()
        except Exception as exc:  # noqa: BLE001 - report, don't crash the harness
            self.error = repr(exc)


class Stalled(threading.Thread):
    """Connects and never reads: the server's queue for it must fill and drop."""

    def __init__(self, base: str, stop: threading.Event) -> None:
        super().__init__(daemon=True)
        self.base, self.stop = base, stop
        self.status: int | None = None

    def run(self) -> None:
        try:
            c = _conn(self.base, 30)
            c.request("GET", "/api/v1/stream?events=nmea,gpsd,ntp,status&status_interval=1")
            r = c.getresponse()
            self.status = r.status
            self.stop.wait()  # hold the socket open without reading
            c.close()
        except Exception as exc:  # noqa: BLE001
            self.status = -1
            self.err = repr(exc)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--readers", type=int, default=12)
    ap.add_argument("--stalled", type=int, default=2)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--poll-hz", type=float, default=5)
    args = ap.parse_args()

    stop = threading.Event()
    readers = [Reader(args.base, "nmea,gpsd,ntp", stop) for _ in range(args.readers)]
    stalled = [Stalled(args.base, stop) for _ in range(args.stalled)]
    for t in [*readers, *stalled]:
        t.start()
    time.sleep(1.0)

    lat: list[float] = []
    ntp_age: list[float] = []
    gps_age: list[float] = []
    lag: list[float] = []
    clients: list[int] = []
    errors = 0
    t_end = time.time() + args.seconds
    next_health = 0.0
    while time.time() < t_end:
        try:
            dt, d = _get_json(args.base, "/api/v1/status")
            lat.append(dt * 1000)
            if d["ntp"].get("age_s") is not None:
                ntp_age.append(d["ntp"]["age_s"])
            if d["gps"].get("age_s") is not None:
                gps_age.append(d["gps"]["age_s"])
        except Exception:  # noqa: BLE001
            errors += 1
        if time.time() >= next_health:
            try:
                _, h = _get_json(args.base, "/api/v1/health")
                lag.append(float(h.get("loop_lag_ms") or 0.0))
                clients.append(int(h.get("stream_clients") or 0))
            except Exception:  # noqa: BLE001
                errors += 1
            next_health = time.time() + 2.0
        time.sleep(1.0 / args.poll_hz)
    stop.set()
    time.sleep(0.5)

    def pct(v: list[float], p: float) -> float:
        return sorted(v)[min(len(v) - 1, int(len(v) * p))] if v else float("nan")

    print(
        f"target {args.base}: {args.readers} readers + {args.stalled} stalled for {args.seconds:g}s"
    )
    print(
        f"status polls: n={len(lat)} p50={statistics.median(lat):.1f}ms "
        f"p95={pct(lat, 0.95):.1f}ms max={max(lat):.1f}ms errors={errors}"
    )
    print(
        f"ntp data age: max={max(ntp_age):.2f}s  gps data age: max={max(gps_age):.2f}s"
        "   (collectors starved if these grow)"
    )
    print(
        f"event-loop lag: max={max(lag):.1f}ms   "
        f"stream_clients seen: {min(clients)}..{max(clients)}"
    )
    total = sum(r.count for r in readers)
    bad = [r.error for r in readers if r.error]
    drops = [r.last_stats.get("dropped", 0) for r in readers if r.last_stats]
    each = total / args.seconds / max(1, args.readers)
    print(
        f"readers: events={total} ({each:.1f}/s each) errors={bad or 'none'} "
        f"dropped(reader-side stats)={sum(drops)}"
    )
    print(
        f"stalled clients HTTP status: {[s.status for s in stalled]} "
        "(200 = accepted; their queues must have dropped server-side)"
    )
    verdict = max(ntp_age) < 3 and max(gps_age) < 3 and pct(lat, 0.95) < 200 and not bad
    print("VERDICT:", "OK - no starvation" if verdict else "CHECK - see numbers above")


if __name__ == "__main__":
    main()
