---
title: User guide
nav_order: 3
has_children: true
---

# User guide

StratumTap has two pages and one shared header. That is the whole application.

| Page | URL | What it is for |
|---|---|---|
| **Dashboard** | `/#/` | The "is it working, and how well?" page. Status pills, the server clock, the chrony numbers, position and satellites. |
| **Detail** | `/#/detail` | Everything else: map, time-accuracy gauge, sky plot, satellite table, accuracy estimates, history charts, NTP sources, recording and export, the live raw stream, and the verbatim tool output. |

Switch between them with the **Dashboard** / **Detail** links at the top left. The address bar
uses hash routes, so the two pages are bookmarkable and the browser's back button works.

- [Dashboard](dashboard.md) — every card on the front page explained
- [Detail view](detail-view.md) — the map, the gauge, the sky plot and the rest
- [Live raw stream](live-raw.md) — watching and capturing NMEA and gpsd JSON
- [Recording and export](recording-export.md) — JSON, CSV, GPX, GeoJSON and server history
- [Settings and header controls](settings.md) — refresh, units, theme, time correction

---

## The header

![StratumTap header: the app name and hostname stratum1, Dashboard and Detail links, a refresh-interval select showing 2 s, pause and refresh-now buttons, a green connected indicator reading updated 1.7 s ago, and a second row with Correct for network delay, Units and Pause when hidden](../assets/screenshots/dashboard-header.png)

The header is on both pages and never scrolls out of the way on desktop.

| Control | What it does |
|---|---|
| **StratumTap** *hostname* | The server's name. Hover it for the version and whether the data is synthetic. |
| **Dashboard** / **Detail** | Switch pages. |
| Interval select | How often the browser polls `/api/v1/status`. Choices come from the server (`1, 2, 5, 10, 30, 60 s` by default) plus **Off**. |
| ⏸ / ▶ | Pause and resume polling. The clock keeps ticking; the data freezes. |
| ↻ | Refresh now, and clear any error backoff. |
| Connection dot | `connecting…`, `connected`, `degraded` or `failing`, plus "updated N s ago · next in N s". |
| **Correct for network delay** | Show the server clock corrected for measured network delay, or exactly as received. |
| **Units** | metric, imperial or nautical. |
| **Pause when hidden** | Stop polling while the tab is in the background. |
| ☀ / ☾ / ◐ | Theme: light, dark or follow the system. |

On a narrow screen the second row collapses into a ⚙ popover with the same controls.

All of these are explained in detail on [Settings and header controls](settings.md).

---

## Reading the page when something is wrong

StratumTap never shows a broken page when a collector fails. Each domain — NTP and GPS — is
reported independently:

- A failing `chronyc` turns the NTP pill red and puts the error message in a banner on the
  **Time sync** card. Everything GPS keeps working.
- A gpsd problem does the same on the GPS side, and the dashboard's NTP half is unaffected.
- A value that simply is not available is shown as an em dash (—), not as zero.

The connection dot goes to *degraded* when a payload arrives with a domain marked
unavailable, and to *failing* after two consecutive failed requests.

See [Troubleshooting](../troubleshooting.md) for what to do about each case.
