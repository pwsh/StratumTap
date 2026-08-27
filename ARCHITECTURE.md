# StratumTap — Architecture

A lightweight web front end for a GPS-disciplined NTP server (chrony + gpsd) running on
Debian 12 or 13. It replaces watching `cgps -s` and `chronyc tracking` in a terminal with a
responsive web UI and a JSON API.

## Goals

1. **Lightweight.** Single Python process, a handful of dependencies, no frontend build
   step, no database. Runs comfortably on a Raspberry Pi.
2. **Same data as the terminal tools, obtained the right way.** We do not scrape curses
   output. GPS data comes straight from gpsd's JSON socket (the same feed `cgps` uses).
   NTP data comes from `chronyc -c tracking` (CSV) plus the human-readable text for display.
3. **Two views + an API.** A compact dashboard, a detail view (map, satellites, time
   gauge, history, recording/export), and a versioned JSON API under `/api/v1/`.
4. **Time you can trust in the browser.** The API stamps every response with server
   receive/send times so the browser can run an NTP-style 4-timestamp exchange and
   optionally correct for network delay when displaying "server time now".

## Functional grouping

The raw tool output is regrouped into these domains (this grouping drives both the API
shape and the UI layout):

| Domain | Source | Contents |
|---|---|---|
| **Time / Sync** | chrony tracking | reference (ID/name/stratum), system-clock offset (the gauge), last/RMS offset, frequency/residual/skew, root delay/dispersion, update interval, leap status |
| **Time / GPS clock** | gpsd TPV, PPS/TOFF | GPS time, time error (ept), GPS→system time offset (PPS/TOFF), leap seconds |
| **Fix** | gpsd TPV | mode (2D/3D), status (DGPS/RTK/…), fix age |
| **Position** | gpsd TPV | lat/lon, altitude (HAE, MSL), geoid separation, Maidenhead grid |
| **Motion** | gpsd TPV | speed, track (true/magnetic, variation), climb |
| **Accuracy** | gpsd TPV + SKY | error estimates (EPX/EPY/EPV/EPH=CEP/SEP/EPS/EPD/EPT) and DOPs (X/Y/V/H/P/T/G) |
| **Satellites** | gpsd SKY | seen/used counts, per-satellite constellation, PRN, elevation, azimuth, SNR, used, health |
| **NTP sources** | chronyc sources/sourcestats | peers/refclocks table (detail view) |
| **Server** | app | timestamps for clock sync, hostname, version, demo flag, uptime |

## Components

```
browser  ──HTTP/JSON──▶  stratumtap (FastAPI + uvicorn)
                            ├── ChronyCollector   ── subprocess: chronyc -c tracking / tracking / sources / sourcestats
                            ├── GpsdClient        ── asyncio TCP 127.0.0.1:2947, ?WATCH={"enable":true,"json":true}
                            ├── DemoSource        ── synthetic data when STRATUMTAP_DEMO=1
                            ├── StateStore        ── latest snapshots + fixed-size history ring buffer
                            └── static/           ── the SPA (vanilla ES modules, Leaflet vendored)
```

### Backend (`stratumtap/` Python package)

| Module | Responsibility |
|---|---|
| `config.py` | `Settings` (pydantic-settings, env prefix `STRATUMTAP_`). See README for the full list. |
| `models.py` | Pydantic response models — the single source of truth for the API contract in `docs/api-contract.md`. |
| `chrony.py` | `ChronyCollector`: background task polling `chronyc` every `chrony_poll_s`; parsers `parse_tracking_csv`, `parse_tracking_text`, `parse_sources_csv`, `parse_sourcestats_csv`. Never blocks a request on a subprocess — requests read the cached snapshot. |
| `gpsd.py` | `GpsdClient`: persistent asyncio connection with exponential-backoff reconnect; sends `?WATCH={"enable":true,"json":true,"pps":true}`; folds `TPV`, `SKY`, `GST`, `PPS`, `TOFF`, `DEVICES`/`DEVICE`, `VERSION` into a `GpsSnapshot`; keeps the last raw message per class for `/raw`. A 30 s silence triggers a `?DEVICES;` liveness probe (a receiver-less gpsd is legitimately quiet); only a second silent period forces a reconnect. While connected but without data the snapshot's `error` explains why ("no GPS device" / "waiting for the first position report"). |
| `demo.py` | `DemoSource`: plausible, slowly varying fake data for development and demos. Only active when explicitly enabled. |
| `geo.py` | Maidenhead locator, unit helpers, fix-text helpers. |
| `state.py` | `StateStore`: latest `NtpSnapshot`/`GpsSnapshot`, a history ring buffer sampled every `history_interval_s`, downsampling for `/history`. |
| `stream.py` | `Broadcaster`: synchronous fan-out of `nmea`/`gpsd`/`ntp` events into per-subscriber bounded queues (drop-oldest, counters), client cap, SSE framing with keepalives and periodic `stats`. Producers never await consumers. |
| `nmea.py` | NMEA sentence splitting/checksum helper used for the raw stream and demo data. |
| `mqtt.py` | Optional `MqttPublisher` (enabled by `STRATUMTAP_MQTT_URL`): Home Assistant device-based discovery, retained state on `<prefix>/<id>/state`, availability/LWT, 60 s floor + change-triggered publishes with deadbands; its own reconnecting task, reads snapshots only. |
| `api.py` | FastAPI router for `/api/v1/*` (including `/stream`, `/stream/nmea.txt`, `/raw/nmea`). |
| `app.py` | `create_app()` — wires settings, collectors (lifespan), static files, timing middleware. |
| `__main__.py` | `python -m stratumtap` → uvicorn. |

Design rules:
- All I/O with gpsd/chrony happens in background tasks; request handlers only read memory.
- **Consumers can never starve producers.** The gpsd reader and chrony poller publish stream
  events with `put_nowait` into bounded per-client queues (oldest dropped when full) and never
  await a client; raw NMEA lines are ring-buffered and broadcast without rebuilding the
  snapshot. `/health` exposes `loop_lag_ms` so this can be verified on the target.
- Every API response carries `server.t_recv` / `server.t_send` (Unix seconds, float) captured
  by ASGI middleware as early/late as possible.
- Units in the API are SI and unambiguous (`_s`, `_m`, `_mps`, `_deg`, `_ppm`). Sign
  conventions are documented per field in `docs/api-contract.md`; the frontend does unit conversion.
- Data that is unavailable is `null`; each domain object has `available` and `error`.
- Python 3.11 compatible (Debian 12 system Python); tested on 3.13 (Debian 13). No 3.12+ syntax.

### Frontend (`stratumtap/static/`)

No build step. Vanilla ES modules, CSS custom properties, dark/light theme (auto + toggle),
mobile-first responsive grid. Leaflet is vendored (`static/vendor/leaflet/`) so the app
works on an isolated network (map tiles still need internet unless `STRATUMTAP_TILE_URL`
points at a local tile server; the map degrades to a plain position readout when tiles
fail to load).

| Route | View |
|---|---|
| `#/` | **Dashboard** — hero clock (server time, corrected), status pills, NTP tiles, GPS tiles, satellite bar. |
| `#/detail` | **Detail** — map + accuracy circle/GST ellipse, sky plot, satellite table, DOP/error panels, auto-scaling time-offset gauge, offset/frequency history, NTP sources, raw output, recording & export, and the **Live raw** panel (`js/stream.js` + `js/components/rawlog.js`): an `EventSource` on `/api/v1/stream`, opened only on demand and closed on unmount/hidden tab, rAF-batched rendering capped at 500 DOM rows, per-sentence-type rates, checksum checks, `.nmea`/`.jsonl` capture download. |

Shared header: refresh control (interval select + pause + "updated N s ago"), time
correction toggle, units toggle, theme toggle, connection indicator.

Clock-sync algorithm (`js/clock.js`): each `/api/v1/time` (or any API) exchange yields
`t0` (browser send), `t1=server.t_recv`, `t2=server.t_send`, `t3` (browser receive).
`delay = (t3−t0) − (t2−t1)`, `offset = ((t1−t0) + (t2−t3))/2` (server − browser). The
estimator keeps the last 8 samples and uses the one with the lowest delay (NTP clock
filter). With correction ON the displayed server time is `Date.now() + offset`; with
correction OFF it is `t2 + (now − t3)` (the timestamp as received, ticking forward).
Both the offset and the delay are shown so the user can judge the correction.

Recording (`js/recorder.js`): while recording, every poll appends the full status
snapshot to an in-memory array (cap configurable, default 50 000). Export as JSON, CSV
(flattened), GPX (track), GeoJSON (LineString + points). Server-side history
(`/api/v1/history`) covers the last 24 h regardless of whether a browser was open.

## Deployment

`deploy/install.sh` (run on the target as root) installs into `/opt/stratumtap` with
its own venv, a dedicated `stratumtap` system user, env file `/etc/default/stratumtap`,
and a hardened systemd unit. `deploy/deploy.sh` rsyncs the repo to the target and runs
the installer. Default port 8080.

## Testing

`pytest`: parsers against the real sample output captured from the target host,
gpsd message folding, Maidenhead, the time endpoint, and the full API in demo mode.
`ruff` for lint/format. `node --check` on frontend modules.
