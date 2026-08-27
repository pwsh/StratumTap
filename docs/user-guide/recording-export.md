---
title: Recording and export
parent: User guide
nav_order: 4
---

# Recording and export
{: .no_toc }

Two independent things carry data out of StratumTap: a **browser recorder** you arm by hand,
and a **server-side history buffer** that is always running.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

---

## The browser recorder

![Recording and export card: Start recording and Clear buttons with a Cap field set to 50000, tiles reading Samples 0, Duration —, Approx size 0 B and State stopped, JSON, CSV, GPX and GeoJSON export buttons, and a Server history CSV (24 h) link](../assets/screenshots/detail-recording.png)

On the [detail view](detail-view.md). Press **Start recording** and every poll from then on is
appended to an array in your browser's memory.

| Control | What it does |
|---|---|
| **Start / Stop recording** | Arms and disarms. Stopping keeps what you have collected. |
| **Clear** | Discards everything collected so far. |
| **Cap** | Maximum samples kept (default 50 000, clamped to 100 … 1 000 000). When the cap is reached the oldest sample is dropped and the tiles report how many. |
| **Samples** | How many are held. |
| **Duration** | Wall-clock span from the first sample to the last. |
| **Approx size** | Rough memory footprint, so you can see it growing. |
| **State** | `recording` or `stopped`. |

**What a sample contains:** the browser's receive time plus the *entire* `/api/v1/status`
payload for that poll — the whole `ntp` object, the whole `gps` object including every
satellite in view, and the `server` block. Nothing is thrown away at capture time; the export
formats decide what to keep.

At the default 2 s refresh, one hour of recording is about 1 800 samples.

{: .warning }
> The recorder lives entirely in the browser tab. Reloading the page, navigating away from
> StratumTap, or closing the tab loses the recording. Export before you leave. For unattended
> collection use the [server history](#server-side-history) instead.

{: .note }
> Recording only appends while the browser is polling. If you pause polling, or **Pause when
> hidden** is on and the tab is in the background, no samples are collected during that time.

---

## Export formats

Four buttons, enabled once there is at least one sample. Each downloads a file named after the
server's hostname.

| Format | Contains | Good for |
|---|---|---|
| **JSON** | Every sample verbatim, pretty-printed: `[{ "t_received": …, "status": { … } }, …]` | Keeping everything, including per-satellite detail. Feed it to `jq` or a notebook. |
| **CSV** | One row per sample, ~50 flattened columns: all the chrony fields, fix, position, motion, accuracy, DOPs, time offset, satellite counts. | Spreadsheets and plotting. The satellite *list* is not included — only the used/seen counts. |
| **GPX** | A single track of the positions, with elevation and the GPS fix time. | Mapping tools, GPS software, a survey of where the antenna thinks it is. |
| **GeoJSON** | A `LineString` of the track plus one `Point` feature per sample, each carrying fix mode, EPH, SEP, HDOP, satellite counts, speed, track and the NTP system offset. | Web maps, GIS, QGIS, anything that reads GeoJSON. |

The CSV's first two columns are `t_iso` and `t_unix` (the browser's receive time). GPX and
GeoJSON skip samples with no position fix — a recording taken before the receiver got a fix
exports as an empty track rather than a track through the middle of the Atlantic.

---

## Server-side history

The service samples its own state every 5 seconds into a ring buffer that holds 24 hours by
default. This runs whether or not any browser is open, which makes it the right tool for
"what happened overnight?".

It feeds the [history charts](detail-view.md#history) and is available as JSON or CSV.

**In the UI:** the **Server history CSV (24 h) ↓** link at the bottom of the Recording card.

**With curl:**

```sh
# the last hour as CSV
curl -s 'http://stratum1.local:8080/api/v1/history?seconds=3600&format=csv' -o history.csv

# the full 24 hours, undownsampled (17280 points at 5 s)
curl -s 'http://stratum1.local:8080/api/v1/history?seconds=86400&max=17280&format=csv' \
  -o history-24h.csv

# as JSON, downsampled to 200 points
curl -s 'http://stratum1.local:8080/api/v1/history?seconds=86400&max=200' | jq .
```

| Parameter | Default | Meaning |
|---|---|---|
| `seconds` | `3600` | How far back to go. |
| `max` | `720` | Maximum points returned. More points than this are downsampled. |
| `format` | `json` | `json` or `csv`. |

### Columns

Both formats carry the same series. CSV adds an ISO-8601 `t_iso` column at the front.

| Column | |
|---|---|
| `t` | Unix seconds (server clock) |
| `ntp_system_offset_s` | System-clock offset, + = fast |
| `ntp_last_offset_s` | Last measured offset |
| `ntp_rms_offset_s` | RMS offset |
| `ntp_frequency_ppm` | Frequency correction |
| `ntp_stratum` | Stratum |
| `gps_mode` | 0 unknown, 1 no fix, 2 2D, 3 3D |
| `gps_sats_used`, `gps_sats_seen` | Satellite counts |
| `gps_hdop` | HDOP |
| `gps_eph_m` | Horizontal error estimate |
| `gps_time_offset_s` | GPS→system offset (PPS/TOFF) |
| `lat`, `lon`, `alt_hae_m` | Position |

Empty cells (JSON `null`) mean the value was unavailable at that sample — a gap, never a zero.

{: .note }
> The ring buffer is in memory. Restarting the service starts it over. If you need history
> that survives restarts, poll `/api/v1/status` into your own time-series database, or use the
> [Home Assistant integration](../integrations/home-assistant.md).

### Quick analysis examples

```sh
# worst absolute system offset in the last 24 h
curl -s 'http://stratum1.local:8080/api/v1/history?seconds=86400&max=17280' \
  | jq -r '.columns as $c | .rows[] | .[$c|index("ntp_system_offset_s")]' \
  | awk 'NF && ($1<0?-$1:$1) > m { m = ($1<0?-$1:$1) } END { print m, "s" }'

# satellites used, once a minute, as a quick plot in the terminal
curl -s 'http://stratum1.local:8080/api/v1/history?seconds=3600&max=60&format=csv' \
  | cut -d, -f1,9
```

Column numbers in CSV depend on the header row — check it with `head -1 history.csv` rather
than hard-coding positions.

---

## Capturing raw NMEA instead

If what you want is the sentences rather than the parsed values, see
[Live raw stream](live-raw.md#saving-a-capture) — the `.nmea` and `.jsonl` buttons in the
browser, or `curl -N .../api/v1/stream/nmea.txt` from a shell.
