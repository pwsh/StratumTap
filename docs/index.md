---
title: Home
nav_order: 1
description: A lightweight web front end and JSON API for a GPS-disciplined chrony NTP server.
---

# StratumTap

If you run a Raspberry Pi (or any Debian box) with `chrony` disciplined by a GPS receiver
through `gpsd` and PPS, StratumTap replaces squinting at `cgps -s` and `chronyc tracking` in
two SSH windows. It is a single Python process — no database, no frontend build step — that
reads gpsd's JSON socket directly and runs `chronyc` for NTP state, and serves a responsive
dashboard, a detail view, and a versioned JSON API on port 8080.

![StratumTap dashboard: status pills reading NTP synchronized stratum 1, a large UTC server clock, the chrony time-sync tiles, GPS position and satellite bars](assets/screenshots/dashboard.png)

[Get started](getting-started.md){: .btn .btn-primary }
[Browse the user guide](user-guide/index.md){: .btn }
[API guide](api.md){: .btn }

---

## What you get

**Dashboard.** A big server clock, sync and fix status pills, and the numbers that matter at a
glance: stratum, system-clock offset, frequency, root dispersion, fix mode, position,
satellites used and seen, DOPs and accuracy estimates.
[Read more](user-guide/dashboard.md)

**Detail view.** A Leaflet map with accuracy circles and the GPS error ellipse, a polar sky
plot of every satellite in view, a sortable per-satellite table, an auto-scaling time-offset
gauge, offset and frequency history charts, the `chronyc sources` and `sourcestats` tables,
and the raw tool output verbatim.
[Read more](user-guide/detail-view.md)

**Live raw stream.** A console fed by Server-Sent Events showing raw NMEA sentences, every
gpsd JSON object and each chrony update as they happen, with per-sentence-type rates,
checksum checking and `.nmea` / `.jsonl` capture download. gpsd relays the sentences, so
StratumTap never opens the serial port itself.
[Read more](user-guide/live-raw.md)

**JSON API.** Everything the UI shows is available under `/api/v1/`, with interactive docs at
`/docs` and a `/health` endpoint for monitoring.
[Read more](api.md)

**Recording and export.** Arm the browser recorder and every poll is appended to an in-memory
track you can export as JSON, CSV, GPX or GeoJSON. Server-side history covers the last 24
hours whether or not a browser was open.
[Read more](user-guide/recording-export.md)

**Demo mode.** `STRATUMTAP_DEMO=1` serves plausible synthetic data, so you can try the whole
thing — including the raw stream — on a laptop with no GPS receiver at all.

**Browser time correction.** Every API response carries the server's receive and send
timestamps, so the browser runs the same four-timestamp exchange NTP uses and shows a server
time corrected for network delay rather than one that is however stale your connection happens
to be.
[How it works](technical/time-correction.md)

---

## Who it's for

- **Anyone with a GPS-disciplined NTP server** who wants a status page instead of an SSH
  session — a wall display in the shack, a browser tab on the workstation, a phone check from
  the sofa.
- **Time nerds** who want to see the system-clock offset, the PPS offset and the fix age side
  by side and understand why all three are different numbers.
  [Read the measurements article](technical/measurements.md)
- **Home lab and monitoring folks** who want the numbers in Home Assistant, Prometheus or a
  script.
  [Home Assistant guide](integrations/home-assistant.md)
- **GPS tinkerers** who want to watch raw NMEA scroll past and capture it for analysis without
  stealing the serial port from gpsd.

---

## Requirements at a glance

| | |
|---|---|
| Operating system | Debian 12 (bookworm) or Debian 13 (trixie), or similar, with systemd |
| Python | 3.11 or newer (the system Python on Debian 12 and 13 is fine) |
| Time stack | `chrony` running, and `chronyc tracking` working for an unprivileged user |
| GPS | `gpsd` running with a real device, ideally with PPS |
| Network | Outbound HTTPS only if you want OpenStreetMap map tiles |
| Hardware | Comfortable on a Raspberry Pi; four Python dependencies, no database |

{: .note }
> The installer will **not** install or reconfigure chrony or gpsd. The time stack on an NTP
> server is not something a web UI's installer should touch — it only warns if they are
> missing.

{: .warning }
> StratumTap has no authentication. Anyone who can reach the port can see your server's
> position and time state. Keep it on a trusted network, or put it behind a reverse proxy with
> TLS and authentication. See [Configuration](configuration.md#reverse-proxy).

---

## Quick links

- [Getting started](getting-started.md) — try it in demo mode, then install it for real
- [Configuration reference](configuration.md) — every `STRATUMTAP_*` variable
- [Troubleshooting](troubleshooting.md) — symptom, cause, fix
- [How the time correction works](technical/time-correction.md)
- [What every number means](technical/measurements.md)
- [Receiver and chrony compatibility](technical/receivers.md)
- [Changelog](changelog.md)
