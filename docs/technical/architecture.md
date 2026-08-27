---
title: Architecture
parent: Technical
nav_order: 4
---

# Architecture
{: .no_toc }

A reader's summary. The full document lives in `ARCHITECTURE.md` at the repository root.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

---

## Goals

1. **Lightweight.** One Python process, four direct dependencies, no database, no frontend
   build step. Comfortable on a Raspberry Pi.
2. **The same data as the terminal tools, obtained the right way.** No scraping of curses
   output. GPS comes from gpsd's JSON socket — the same feed `cgps` uses. NTP comes from
   `chronyc -c tracking` (CSV) plus the human text for display.
3. **Two views and an API.** A compact dashboard, a detail view, and a versioned JSON API
   under `/api/v1/`.
4. **Time you can trust in the browser.** Every response is stamped with server receive and
   send times so the browser can run an NTP-style four-timestamp exchange.

---

## Components

```
browser  ──HTTP/JSON──▶  stratumtap (FastAPI + uvicorn)
                            ├── ChronyCollector   ── subprocess: chronyc -c tracking / tracking
                            │                                    / sources / sourcestats
                            ├── GpsdClient        ── asyncio TCP 127.0.0.1:2947
                            │                        ?WATCH={"enable":true,"json":true,
                            │                                "pps":true,"nmea":true}
                            ├── DemoSource        ── synthetic data when STRATUMTAP_DEMO=1
                            ├── Broadcaster       ── SSE fan-out, bounded per-client queues
                            ├── StateStore        ── latest snapshots + 24 h history ring
                            └── static/           ── the SPA (vanilla ES modules, Leaflet vendored)
```

| Module | Responsibility |
|---|---|
| `config.py` | `Settings` — pydantic-settings, env prefix `STRATUMTAP_`. |
| `models.py` | Pydantic response models. The single source of truth for the API contract. |
| `chrony.py` | Polling collector plus pure parsers for the CSV and text forms. |
| `gpsd.py` | Persistent asyncio client with exponential-backoff reconnect; folds gpsd messages into one snapshot. |
| `demo.py` | Plausible synthetic data. Only active when explicitly enabled. |
| `geo.py` | Maidenhead locator, unit helpers, fix-text helpers. |
| `state.py` | Latest snapshots, the history ring buffer, downsampling, the NMEA ring. |
| `stream.py` | The SSE broadcaster: bounded queues, drop-oldest, client cap, keepalives. |
| `nmea.py` | Sentence splitting and checksum verification. |
| `api.py` | The `/api/v1` router. |
| `app.py` | `create_app()` — settings, collector lifespan, static files, timing middleware. |
| `loopmon.py` | Event-loop lag measurement, exposed as `loop_lag_ms` in `/health`. |

---

## Data flow

**In.** Two background tasks run for the life of the process. The chrony collector polls
`chronyc` every second (sources every ten). The gpsd client holds one TCP connection open and
reads messages as they arrive. Each publishes into `StateStore` — the latest `NtpSnapshot`,
the latest `GpsSnapshot`, a history sample every 5 s, and a ring of recent NMEA lines — and
into the `Broadcaster` for any SSE subscribers.

**Out.** Request handlers **only read memory**. No handler runs a subprocess, opens a socket,
or awaits a collector. That is what makes `/api/v1/status` cheap enough to poll once a second
from several browsers at once, and what keeps p95 latency in the tens of milliseconds on a Pi
under load.

**Stamping.** ASGI middleware records `t_recv` as early as it can; each handler stamps
`t_send` as the last thing it does, and derives every `age_s` field from it. So an "age" is
always relative to the instant the response left, not to some earlier moment inside the
handler.

### Design rules

- All I/O with gpsd and chrony happens in background tasks; handlers only read memory.
- **Consumers can never starve producers** — see [Streaming design](streaming.md).
- Units in the API are SI and unambiguous, with the unit in the field name.
- Data that is unavailable is `null`; each domain object carries `available` and `error`.
- Collector failures never produce a 5xx. They surface as `available: false` so the UI can
  degrade one card instead of breaking the page.
- Python 3.11 compatible (Debian 12's system Python) and tested on 3.13 (Debian 13). No 3.12+ syntax.

---

## Why the serial port is never opened

StratumTap talks to **gpsd**, not to the receiver.

gpsd owns the device. It is already parsing the NMEA, already handing PPS to the kernel and to
chrony, and already relaying everything to `cgps`, `gpspipe` and anyone else who asks. Asking
it for the raw sentences too (`"nmea":true` on the watch) costs one flag.

The alternative — opening `/dev/ttyAMA0` directly — would mean competing with gpsd for the
device, needing device permissions, needing baud-rate configuration, and introducing a way for
a *status page* to disturb a *time server*. That trade is not worth making for any feature.

The consequence is a strong safety property: **StratumTap is a reader, and stopping it can
never affect timekeeping.**

---

## Frontend

No build step. Vanilla ES modules, CSS custom properties, a dark/light theme that can follow
the system, and a mobile-first responsive grid. Leaflet is vendored into
`static/vendor/leaflet/` so the application works on an isolated network — only the map
*tiles* need the internet.

| Route | View |
|---|---|
| `#/` | Dashboard — hero clock, status pills, chrony tiles, GPS tiles, satellite bars |
| `#/detail` | Detail — map, gauge, sky plot, satellite table, accuracy, history, sources, recording, live raw, raw output |

A shared header carries the refresh control, the time-correction toggle, units, theme and the
connection indicator. Each view's `mount()` returns an `unmount()` that tears down every
timer, animation frame, subscription and event listener it created, so switching views repeatedly
leaks nothing.

Performance choices worth noting: the hero clock repaints on `requestAnimationFrame` but only
rebuilds its `HH:MM:SS` string when the second actually changes; the history charts are
hand-drawn on a canvas with no chart library; and the live raw console batches events into one
animation frame rather than touching the DOM per event.

---

## Deployment layout

| Path | What |
|---|---|
| `/opt/stratumtap/app` | Source tree, owned by root |
| `/opt/stratumtap/venv` | Private virtualenv with exactly pinned dependencies |
| `/etc/default/stratumtap` | Configuration, created once and never overwritten |
| `/etc/systemd/system/stratumtap.service` | The unit |

`deploy/install.sh` runs on the target and is idempotent. `deploy/deploy.sh` rsyncs the repo
from a development machine and runs the installer over ssh. Default port 8080.

Dependencies are pinned exactly — direct *and* transitive — so the venv on the target is
reproducible. `uvicorn` is installed **without** the `[standard]` extra on purpose: `uvloop`
and `httptools` publish no `armv7l` wheels and would force a source build on a 32-bit
Raspberry Pi.

---

## Hardening summary

The unit runs as an unprivileged `stratumtap` system user with no home directory and no shell,
under `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `PrivateDevices`, `ProtectClock`,
`ProtectKernelTunables`, `ProtectKernelModules`, `ProtectControlGroups`, `NoNewPrivileges`,
`RestrictNamespaces`, `RestrictRealtime`, `RestrictSUIDSGID`, `LockPersonality`,
`MemoryDenyWriteExecute`, `SystemCallArchitectures=native`, `RemoveIPC` and an empty
`CapabilityBoundingSet`.

The service only ever reads: it runs `chronyc` and opens a TCP connection to gpsd, and it
writes no files anywhere.

{: .warning }
> Keep `AF_INET`, `AF_INET6` and `AF_UNIX` in `RestrictAddressFamilies=`, and do not add
> `IPAddressDeny=`. Either change cuts off both collectors.

There is **no authentication**. Anyone who can reach the port sees the server's position and
time state. See [Configuration](../configuration.md#reverse-proxy).

---

## Testing

`pytest` covers the parsers against real captured output from a live target, gpsd message
folding, the Maidenhead locator, the time endpoint, the full API in demo mode, and the stream
broadcaster's non-starvation behavior (including an explicit "a stalled subscriber does not
starve the others" test). `ruff` handles lint and formatting; `node --check` validates the
frontend modules.
