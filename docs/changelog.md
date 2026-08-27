---
title: Changelog
nav_order: 9
---

# Changelog

All notable changes to StratumTap.

---

## Unreleased

- Verified on Debian 13 (Python 3.13, chrony 4.6.1, gpsd 3.25): all dependency pins are current and ship Python 3.13 aarch64 wheels; test suite passes on the Pi under 3.13.

- Installer rebuilds the venv when the system Python version changed (fixes a dead service after an OS upgrade, e.g. Debian 12 → 13).
- systemd unit: `Restart=always`, `StartLimitIntervalSec=0` so the service always comes back.
- gpsd 3.25 support: DOP-only `SKY` messages no longer blank the satellite list.

### Added
- **MQTT publisher with Home Assistant discovery.** Set `STRATUMTAP_MQTT_URL` and StratumTap
  publishes one retained device-discovery message — a device with about twenty entities
  (system/PPS offset, stratum, reference, sync, fix, satellites, HDOP/EPH, altitude, grid
  square) plus a `device_tracker` for the position — a flat JSON state topic, and an
  availability topic backed by a last will, so every entity goes unavailable within seconds of
  the service dying. Publishing is throttled: at least once a minute, never more than once
  every five seconds, and immediately in between when the offset, frequency, stratum, sync
  state, fix or satellite count actually moves. `/api/v1/health` gained an `mqtt` block.
  New settings: `STRATUMTAP_MQTT_URL`, `_TOPIC_PREFIX`, `_DISCOVERY_PREFIX`, `_DEVICE_ID`,
  `_CLIENT_ID`, `_INTERVAL_S`, `_MIN_INTERVAL_S`, `_DEADBAND_OFFSET_US`, `_DEADBAND_PPM`,
  `_EXPIRE_AFTER_S`, `_RETAIN_STATE`, `_TLS_INSECURE`, `_QOS`.
  [Home Assistant](integrations/home-assistant.md)
- **Raw data streaming over Server-Sent Events.** `/api/v1/stream` pushes raw NMEA sentences,
  every gpsd JSON object, chrony tracking updates and (on request) the full status payload.
  `/api/v1/stream/nmea.txt` gives the same NMEA as a chunked plain-text stream for
  `curl -N … > capture.nmea`, and `/api/v1/raw/nmea` serves a snapshot of the sentence ring
  buffer.
  [Streaming design](technical/streaming.md)
- **Live raw panel** on the detail view: an on-demand SSE console with per-sentence-type
  rates, checksum verification, a substring filter, pause and auto-scroll, `.nmea` and
  `.jsonl` capture download, and a snapshot fallback that needs no stream.
  [Live raw stream](user-guide/live-raw.md)
- **Non-starvation guarantees** for the stream: bounded per-subscriber queues with
  drop-oldest, a concurrent client cap, and no snapshot rebuild for NMEA lines — so a stalled
  browser can never slow down data collection. `loop_lag_ms` and `stream_clients` were added
  to `/api/v1/health` so this is observable, and `scripts/stream_loadtest.py` demonstrates it
  against a running instance.
- **`STRATUMTAP_HOSTNAME`** — override the display name shown in the header, the page title
  and `server.hostname`, instead of exposing the machine hostname.
  [Configuration](configuration.md#ui-defaults)
- New settings for the stream: `STRATUMTAP_STREAM_MAX_CLIENTS`, `STRATUMTAP_STREAM_QUEUE` and
  `STRATUMTAP_NMEA_RING`.
- This documentation site.

### Changed
- **Renamed to StratumTap** (previously `gps-ntp-visual`). Every environment variable prefix
  changed from `GPSNTP_` to `STRATUMTAP_`, the systemd unit is now `stratumtap.service`, and
  the install prefix is `/opt/stratumtap`.
- gpsd is now watched with `"nmea":true` so raw sentences arrive on the connection StratumTap
  already has open. It still never opens the serial port itself.

### Migration
`deploy/install.sh` migrates an existing `gps-ntp-visual` install automatically: it disables
and removes the old unit, converts `/etc/default/gps-ntp-visual` to
`/etc/default/stratumtap` (rewriting `GPSNTP_` to `STRATUMTAP_`), removes `/opt/gps-ntp-visual`
and the old system user, and installs the new unit. Just deploy again.

{: .note }
> Anything you configured by hand outside the environment file — a firewall rule naming the
> old port, a reverse-proxy block, a monitoring check — still refers to the old names. Check
> those after upgrading.

---

## 0.1.0

The initial release.

### Added
- **Dashboard** — server clock with browser time correction, sync and fix status pills, chrony
  tiles (offset, last offset, RMS, frequency, residual, skew, root delay and dispersion,
  update interval, reference time), GPS tiles (position, altitude, motion, accuracy, fix mode,
  GPS time, fix age, GPS→system offset) and a satellite signal chart.
  [User guide](user-guide/dashboard.md)
- **Detail view** — Leaflet map with 2D CEP and 3D SEP accuracy circles and the 1σ GST error
  ellipse, an auto-scaling time-offset gauge with hysteresis, a polar sky plot, a sortable
  satellite table, DOP and error-estimate panels, five history charts over 15 m to 24 h, the
  parsed `chronyc sources` and `sourcestats` tables, and the verbatim tool output.
  [User guide](user-guide/detail-view.md)
- **JSON API** under `/api/v1/` — `time`, `status`, `ntp`, `ntp/sources`, `gps`,
  `gps/satellites`, `history` (JSON or CSV), `raw/chronyc/*`, `raw/gpsd`, `config` and
  `health`, with interactive documentation at `/docs`.
  [API guide](api.md)
- **Browser time correction** — every response carries `server.t_recv` and `server.t_send`, so
  the browser runs NTP's four-timestamp exchange, keeps the eight lowest-delay samples and
  displays the offset and round-trip time alongside the clock.
  [How it works](technical/time-correction.md)
- **Recording and export** — an in-browser recorder with JSON, CSV, GPX and GeoJSON export,
  plus a server-side 24-hour history ring buffer that runs whether or not a browser is open.
  [User guide](user-guide/recording-export.md)
- **Demo mode** (`STRATUMTAP_DEMO=1`) — plausible synthetic data with no gpsd or chrony
  required.
- **Deployment** — `deploy/install.sh` (idempotent, installs to `/opt/stratumtap` with its own
  virtualenv, a dedicated system user and a hardened systemd unit), `deploy/deploy.sh` to push
  from a development machine, and `deploy/uninstall.sh`.
  [Getting started](getting-started.md)
- **Offline friendliness** — Leaflet vendored, `STRATUMTAP_TILE_URL` for a local tile server,
  and graceful degradation to a plain position readout when tiles fail.
