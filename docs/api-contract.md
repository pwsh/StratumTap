---
title: API contract (reference)
parent: API
nav_order: 1
---

# StratumTap API contract (v1)

Base path: `/api/v1`. All responses are `application/json` unless noted. Timestamps are
Unix seconds as floats (`t_*`) plus ISO‑8601 UTC strings where useful. Unavailable values
are `null`. Every domain object has `available: bool` and `error: string|null`.

Every JSON response contains a `server` object (added by the timing middleware / handlers):

```json
"server": {
  "t_recv": 1787753871.123456,   // server wall clock (Unix s) when the request was received
  "t_send": 1787753871.123980,   // server wall clock (Unix s) just before the response was sent
  "t0": 1787753871.100,          // echo of the client's ?t0= query param (float), or null
  "hostname": "stratum1",
  "version": "0.1.0",
  "demo": false,
  "uptime_s": 12345.6
}
```

Conventions:
- Units are in the field name: `_s` seconds, `_m` meters, `_mps` m/s, `_deg` degrees, `_ppm`.
- **`ntp.system_offset_s`**: seconds the *system clock is ahead of* NTP/true time.
  Positive = system clock is FAST, negative = SLOW. (`chronyc tracking` prints
  "0.000000372 seconds fast" → `+3.72e-7`.) This is the value the time-accuracy gauge shows.
- `ntp.frequency_ppm`: positive = system clock runs fast (gains time), matching the
  "fast"/"slow" word in `chronyc tracking`.
- `ntp.last_offset_s`: same sign sense as `system_offset_s` — positive = the system clock was
  measured *ahead* (fast) of the reference at the last update. chrony's `tracking` report
  already uses this sense for "Last offset" (the sample shows "fast" + "+0.000000994"), so it
  is passed through unchanged. `ntp.rms_offset_s` is unsigned.
- **Parser sign rule (chrony 4.3 `client.c`)**: in `chronyc -c tracking` CSV the *System time*
  field is chrony's `current_correction` (positive = system SLOW), so `system_offset_s = -csv[4]`.
  The *Frequency* CSV field is already positive = fast → passed through. In the human text,
  `system_offset_s` is `+value` when the word is "fast", `-value` when "slow".
- `gps.time_offset.offset_s`: `clock − real` from gpsd `PPS`/`TOFF` (we send
  `?WATCH={"enable":true,"json":true,"pps":true}`), i.e. how far the *system clock is ahead
  of* GPS time. For NMEA `TOFF` this is typically ~+0.1…+1 s (serial latency); for `PPS` it
  is tiny (µs). `cgps -s` does NOT use these: its "Time offset" line is simply
  `CLOCK_REALTIME now − TPV.time` (fix staleness), which we expose separately as
  `gps.fix.time_age_s` computed at response time (`t_send − fix.time_unix`).
- Satellite numbering: `cgps` prints `svid` first (the conventional PRN, e.g. SBAS **133**)
  and gpsd's internal `PRN` second (e.g. 46). The UI's primary satellite number is `svid`
  (fallback `prn` when `svid` is absent); `prn` is shown as a secondary column.
- `gps.satellites.collected_at` is `SKY.time` when the receiver sends one; the MTK-3301 on
  the target omits it, in which case the server's arrival time for that `SKY` is used.
- Response envelopes name their payload after the domain: `/api/v1/status` →
  `{server, ntp, gps}`, `/api/v1/ntp` → `{server, ntp}`, `/api/v1/ntp/sources` →
  `{server, ntp_sources}`, `/api/v1/gps` → `{server, gps}`, `/api/v1/gps/satellites` →
  `{server, satellites}`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/time` | Minimal, cheapest possible timestamp exchange for clock sync. |
| GET | `/api/v1/status` | Everything: `server`, `ntp`, `gps` (includes satellites). |
| GET | `/api/v1/ntp` | `server` + `ntp`. |
| GET | `/api/v1/ntp/sources` | `server` + `ntp_sources` (parsed `chronyc -c sources` / `sourcestats`). |
| GET | `/api/v1/gps` | `server` + `gps` (fix, position, motion, accuracy, dop, time_offset, satellites). |
| GET | `/api/v1/gps/satellites` | `server` + `satellites` only (the same object as `gps.satellites`). |
| GET | `/api/v1/history?seconds=3600&max=720&format=json|csv` | Server-side history ring buffer (downsampled to ≤ `max` points). `format=csv` → `text/csv` attachment. |
| GET | `/api/v1/raw/chronyc/tracking` | `text/plain` — verbatim `chronyc tracking` (human) output. |
| GET | `/api/v1/raw/chronyc/sources` | `text/plain` — verbatim `chronyc sources -v`. |
| GET | `/api/v1/raw/chronyc/sourcestats` | `text/plain` — verbatim `chronyc sourcestats -v`. |
| GET | `/api/v1/raw/gpsd` | JSON: `{ "VERSION": {...}, "DEVICES": {...}, "TPV": {...}, "SKY": {...}, "PPS": {...}, "TOFF": {...} }` last message per class (missing classes omitted; `WATCH`, `GST`, `DEVICE` and any other class gpsd sends are included the same way). |
| GET | `/api/v1/config` | Non-secret UI config: `default_refresh_s`, `refresh_choices_s`, `tile_url`, `tile_attribution`, `hostname`, `demo`, `history_interval_s`, `history_size`, `version`. |
| GET | `/api/v1/health` | `{ "ok": bool, "ntp_ok": bool, "gpsd_connected": bool, "gps_fix": bool, "server": {...} }`. HTTP 200 always (UI reads flags); `?strict=1` → 503 when not `ok`. `ntp_ok` = `ntp.available && ntp.synchronized`; `gpsd_connected` = `gps.connected`; `gps_fix` = `gps.fix.mode >= 2`; `ok` = all three. |
| GET | `/docs`, `/openapi.json` | FastAPI auto docs. |
| GET | `/` (+ any non-API path) | The SPA (`index.html`). |

All endpoints accept an optional `?t0=<float unix seconds>` query parameter which is
echoed back in `server.t0` (lets the client correlate without closures if it wants to).

Responses set `Cache-Control: no-store`.

### `GET /api/v1/time`

```json
{ "server": { ...as above... }, "ntp_synchronized": true, "ntp_system_offset_s": 3.72e-7, "ntp_stratum": 1 }
```

### `ntp` object (NtpSnapshot)

```json
{
  "available": true,
  "error": null,
  "collected_at": 1787753870.98,        // when chronyc was last run successfully
  "age_s": 0.14,                          // t_send − collected_at (computed at response time)
  "reference_id": "50505300",             // hex string exactly as chrony prints it
  "reference_name": "PPS",                // the name in parentheses (refclock name, hostname or IP)
  "stratum": 1,
  "ref_time": "2026-08-26T14:17:12.000000Z",
  "ref_time_unix": 1787753832.0,
  "system_offset_s": 3.72e-7,             // + = system FAST (see conventions)
  "last_offset_s": 9.94e-7,
  "rms_offset_s": 6.9e-7,
  "frequency_ppm": 17.97,                 // + = fast
  "residual_freq_ppm": 0.004,
  "skew_ppm": 0.098,
  "root_delay_s": 1e-9,
  "root_dispersion_s": 1.0513e-5,
  "update_interval_s": 8.0,
  "leap_status": "Normal",                // "Normal" | "Insert second" | "Delete second" | "Not synchronised"
  "synchronized": true,                   // stratum < 16 && leap_status != "Not synchronised"
  "raw": "Reference ID    : 50505300 (PPS)\nStratum         : 1\n..."   // verbatim human output
}
```

### `ntp_sources` object (`/api/v1/ntp/sources`)

```json
{
  "available": true, "error": null, "collected_at": 1787753870.0, "age_s": 1.2,
  "sources": [
    { "mode": "#", "mode_text": "refclock", "state": "*", "state_text": "current best",
      "name": "PPS", "stratum": 0, "poll": 3, "reach": 255, "reach_octal": "377", "last_rx_s": 5,
      "poll_interval_s": 8, "last_sample_offset_s": 9.94e-7, "last_sample_adjusted_offset_s": 1.0e-6, "last_sample_error_s": 8.2e-7 }
  ],
  "sourcestats": [
    { "name": "PPS", "np": 32, "nr": 15, "span_s": 248, "frequency_ppm": 0.001,
      "freq_skew_ppm": 0.05, "offset_s": 1.1e-7, "std_dev_s": 4.0e-7 }
  ],
  "raw_sources": "...chronyc sources -v...", "raw_sourcestats": "..."
}
```

Mode chars: `^` server, `=` peer, `#` refclock. State chars: `*` current best, `+` combined,
`-` not combined, `x` falseticker, `~` too variable, `?` unusable. In `chronyc -c sources`
chrony prints `LastRx` as `4294967295` (uint32 max) when a source has never been received →
`last_rx_s: null`; `reach` is printed in OCTAL even in CSV mode (`377`) → parse with base 8 into the integer `reach` (0–255) and keep the text in `reach_octal`; `poll` is log2 seconds (`poll_interval_s = 2**poll` is also provided).
`name` is the address as printed in CSV mode (no reverse DNS); `raw_sources` (the `-v` text)
has resolved hostnames.

### `gps` object (GpsSnapshot)

```json
{
  "available": true,                      // connected to gpsd AND at least one TPV received
  "error": null,                          // e.g. "connection refused (127.0.0.1:2947)"
  "connected": true,                      // TCP session to gpsd is up
  "collected_at": 1787753871.10,          // time of the most recent TPV/SKY
  "age_s": 0.02,
  "gpsd_version": "3.25",
  "device": { "path": "/dev/ttyAMA0", "driver": "MTK-3301", "subtype": "AXN_2.51_3339_17112000-0004", "activated": "2026-08-26T15:23:41.000Z", "bps": 9600, "cycle_s": 1.0 },   // the device that produced the last TPV
  "devices": [ { ...same shape... }, { "path": "/dev/pps0", "driver": "PPS", "subtype": null, "activated": "...", "bps": null, "cycle_s": null } ],  // every device gpsd reports (DEVICES + DEVICE add/remove notices)
  "fix": {
    "mode": 3, "mode_text": "3D",                       // 0 unknown, 1 no fix, 2 2D, 3 3D
    "status": 2, "status_text": "DGPS",                 // gpsd status enum: 2 DGPS,3 RTK fixed,4 RTK float,5 DR,6 GNSS+DR,7 time-only(surveyed),8 simulated,9 P(Y); gpsd never emits 0/1 → null
    "fix_text": "3D DGPS FIX",                          // cgps-style label ("NO FIX", "2D FIX", "3D FIX", "3D DGPS FIX", ...)
    "fix_age_s": 6.0,                                   // seconds since the fix mode last changed (cgps "(6 secs)")
    "time": "2026-08-26T14:17:51.000Z", "time_unix": 1787753871.0,
    "time_age_s": 1.001503501,                          // t_send − time_unix (this is cgps's "Time offset" line)
    "ept_s": 0.005,                                     // time error estimate
    "leapseconds": 18
  },
  "position": {
    "lat": 51.47789900, "lon": -0.00150100,               // decimal degrees, S/W negative
    "alt_hae_m": 198.4, "alt_msl_m": 231.9, "geoid_sep_m": -33.5,
    "grid_square": "IO91xl94"                           // 8-char Maidenhead
  },
  "motion": { "speed_mps": 0.022, "track_deg": 63.5, "mag_track_deg": 64.4, "mag_var_deg": -0.9, "climb_mps": 0.0 },
  "accuracy": {
    "epx_m": 2.1, "epy_m": 3.0, "epv_m": 4.3,           // per-axis 1σ-ish estimates
    "eph_m": 4.0,                                       // 2D (CEP) — the map accuracy circle radius
    "sep_m": 5.8,                                       // 3D (SEP)
    "eps_mps": 6.08, "epd_deg": null, "epc_mps": null, "ept_s": 0.005
  },
  "dop": { "xdop": 0.56, "ydop": 0.81, "vdop": 0.75, "hdop": 0.97, "pdop": 1.23, "tdop": 0.69, "gdop": 1.65 },
  "ecef": { "x_m": null, "y_m": null, "z_m": null, "vx_mps": null, "vy_mps": null, "vz_mps": null, "p_acc_m": null, "v_acc_mps": null },
  "time_offset": {
    "source": "PPS",                                    // "PPS" | "TOFF" | null (null when neither message has been seen)
    "offset_s": 0.000001234,                            // clock − real (system clock ahead of GPS time)
    "real_s": 1787753871.0, "clock_s": 1787753871.000001234,
    "precision": -20, "measured_at": 1787753871.0,      // measured_at = server time the message arrived
    "pps_offset_s": 0.000001234, "toff_offset_s": 0.4123 // last value from each message class (null if unseen)
  },
  "gst": {                                              // NMEA GST pseudorange-noise statistics (gpsd class GST); null if the receiver never sends it
    "time_unix": 1787757821.0, "rms_m": 5.9,
    "major_m": 2.8, "minor_m": 2.2, "orient_deg": 19.1,  // 1σ error ellipse: semi-major/semi-minor axes and orientation of the major axis (deg from true north)
    "lat_err_m": 2.8, "lon_err_m": 2.3, "alt_err_m": 4.7
  },
  "satellites": {
    "seen": 12, "used": 9, "collected_at": 1787753871.0,
    "list": [
      { "gnss": "GP", "gnss_name": "GPS", "gnssid": 0, "svid": 5, "prn": 5, "sigid": 0,
        "el_deg": 29.0, "az_deg": 64.0, "snr_db": 33.0, "used": true, "health": 1 },
      { "gnss": "SB", "gnss_name": "SBAS", "gnssid": 1, "svid": 133, "prn": 46, "sigid": null,
        "el_deg": 28.0, "az_deg": 229.0, "snr_db": 47.0, "used": false, "health": null }
    ]
  },
  "cgps_time_offset_text": "1.001503501 s"                 // formatted fix.time_age_s, 9 decimals, as cgps prints it
}
```

`gnss` two-letter labels follow cgps: `GP` GPS, `SB` SBAS, `GA` Galileo, `BD` BeiDou,
`IM` IMES, `QZ` QZSS, `GL` GLONASS, `IR` IRNSS/NavIC, `??` unknown (gnssid 0..7). Satellites
are sorted by gnssid then svid (then prn). `gps_time_offset_s` in history = `time_offset.offset_s`.

Live capture from the target (2026-08-26, `tests/fixtures/live_*`): gpsd 3.22 emits `PPS`
messages to JSON watchers even with `"pps":false`; `TOFF` was not observed; the MTK-3301
receiver sends `GST`, `altHAE`+`altMSL`+`alt`, `magtrack`, `geoidSep`, `eph`, `sep`, `epc`,
`status:2`, and no `leapseconds`/`ecef*`. Two `PPS` messages per second arrive (from
`/dev/pps0` and the KPPS on `/dev/ttyAMA0`, identical values) — either is fine.

### `history` (`/api/v1/history`)

```json
{
  "server": {...},
  "interval_s": 5, "requested_seconds": 3600, "points": 720,
  "columns": ["t","ntp_system_offset_s","ntp_last_offset_s","ntp_rms_offset_s","ntp_frequency_ppm","ntp_stratum",
              "gps_mode","gps_sats_used","gps_sats_seen","gps_hdop","gps_eph_m","gps_time_offset_s","lat","lon","alt_hae_m"],
  "rows": [[1787750271.0, 3.7e-7, 9.9e-7, 6.9e-7, 17.97, 1, 3, 9, 12, 0.97, 4.0, 1.0015, 51.4779, -0.0015, 198.4], ...]
}
```
Rows are ascending in time, `null` where a value was unavailable. `format=csv` returns the
same as CSV with a header row and `t_iso` as an extra first column.

## Error handling

- Unknown `/api/v1/*` path → 404 JSON `{ "detail": "Not found" }`.
- Collector failures never produce 5xx; they produce `available:false, error:"..."` so the UI
  can show a degraded state. `/health?strict=1` is the only endpoint that returns 503.
- The `/api/v1/raw/chronyc/*` endpoints always answer 200 `text/plain`. When the collector has
  nothing to show they return a one-line explanation instead of the tool output, e.g.
  `chronyc tracking unavailable: 506 Cannot talk to daemon`.
- If `chronyc -c tracking` fails but the human `chronyc tracking` output parses, the text is
  used and `available` stays `true` (the CSV is authoritative whenever both succeed).

## Streaming (v0.2)

Raw data is pushed over **Server-Sent Events**. gpsd is watched with
`{"enable":true,"json":true,"pps":true,"nmea":true}`, so raw NMEA sentences arrive
interleaved with the JSON on the same socket (lines starting with `$` or `!`). The app never
opens the serial device itself.

### Non-starvation guarantees (design rules, enforced in code and tests)

1. The gpsd reader task and the chrony poller **never await a subscriber**. Publishing is a
   synchronous `put_nowait` into each subscriber's bounded queue; when a queue is full the
   **oldest** event is dropped and the subscriber's `dropped` counter incremented.
2. Per-subscriber queue size: `STRATUMTAP_STREAM_QUEUE` (default 500 events). Concurrent
   subscribers capped at `STRATUMTAP_STREAM_MAX_CLIENTS` (default 16); beyond that `/stream`
   answers `503 {"detail":"too many stream clients"}`.
3. NMEA lines do **not** trigger a snapshot rebuild — only JSON objects do. NMEA is appended
   to a ring buffer (`STRATUMTAP_NMEA_RING`, default 1000 lines) and broadcast.
4. A `: keepalive` SSE comment is sent every 15 s of silence. Client disconnects are detected
   on the next write (or via `request.is_disconnected()`) and the subscriber is always removed
   in a `finally:`.
5. `/api/v1/health` reports `loop_lag_ms` (max event-loop scheduling overshoot over the last
   60 s, measured by a 1 s ticker) and `stream_clients`; a healthy Pi shows single-digit ms.

### `GET /api/v1/stream?events=nmea,gpsd,ntp,status&status_interval=2`

`Content-Type: text/event-stream`, `Cache-Control: no-store`, `X-Accel-Buffering: no`.
`events` is a comma list (default `nmea,gpsd`). Each event has an incrementing `id:`.

| event | data (JSON) |
|---|---|
| `hello` | `{ "client_id": 3, "events": ["nmea","gpsd"], "server": {...ServerInfo...}, "queue": 500 }` — first event |
| `nmea` | `{ "t": 1787758473.0123, "line": "$GPRMC,...*6A", "type": "RMC", "talker": "GP", "checksum_ok": true }` |
| `gpsd` | the raw gpsd JSON object exactly as received, plus `"_t": <server receive time>` |
| `ntp` | the `ntp` object (NtpSnapshot) after every successful `chronyc tracking` poll |
| `status` | the full `/status` payload every `status_interval` seconds (1–60, default 2); only when `status` is requested |
| `stats` | `{ "t": ..., "sent": 1234, "dropped": 0, "queue_len": 3, "clients": 2 }` every 10 s |

### `GET /api/v1/stream/nmea.txt`

`text/plain` chunked stream of raw NMEA lines (one per line, no timestamps) — for
`curl -N http://host:8080/api/v1/stream/nmea.txt > capture.nmea`. Same queue/cap rules.

### `GET /api/v1/raw/nmea?n=200`

`{ "server": {...}, "count": 200, "ring_size": 1000, "lines": [ { "t": ..., "line": "...", "type": "GGA", "talker": "GP", "checksum_ok": true }, ... ] }`
newest last. `n` 1–ring size.

### `health` additions

`{ ..., "loop_lag_ms": 2.1, "stream_clients": 1 }`

`{ ..., "mqtt": { "enabled": bool, "connected": bool, "publishes": int, "errors": int,
"last_publish_at": float|null, "last_reason": str|null, "last_error": str|null } }` — the
optional MQTT publisher. `enabled` is `false` (and every other field zero/null) unless
`STRATUMTAP_MQTT_URL` is set; `enabled: true` with `connected: false` plus a `last_error` is
the misconfigured-broker case. `last_reason` is `"interval"` or `"change:<field>"`.

### Implementation notes (v0.2 backend)

Points the contract above left open, as actually implemented:

- **`/raw/nmea`** also returns `rate_per_s` — sentences per second, counted over the last
  5 s (so it decays to `0` when the receiver goes quiet). `n` is clamped to the ring size
  rather than rejected (`n < 1` is a 422). `lines` is oldest first, i.e. newest last.
- **`stats` counters** are per subscriber: `sent` is the number of events *queued* for that
  client since it connected (a later-dropped event is still counted in `sent`), `dropped`
  is how many of those it lost to a full queue. `sent - dropped` is what it actually got.
  `queue_len` is the current backlog, `clients` the current `stream_clients`.
- **`hello.events`** lists the accepted events in canonical order (`nmea`, `gpsd`, `ntp`,
  `status`), not in the order the query listed them.
- **Event ids** start at `1` for `hello` and increment per SSE event on that connection
  (`stats` and `status` included). Keepalives are comments and carry no id.
- **`events` validation**: an unknown or empty `events` list is `400
  {"detail":"unknown stream event(s): …"}` / `{"detail":"no stream events requested"}`.
  `status_interval` outside 1–60 is a 422 (FastAPI query validation).
- **`ntp` events** are published after a *successful* tracking poll only; a failing
  `chronyc` shows up in the `status` event (and in `/api/v1/ntp`), not as an `ntp` event.
- **`/stream/nmea.txt`** writes the raw sentence and a `\n`, nothing else — no timestamps,
  no SSE framing, no keepalive comments (an idle stream simply stays silent while the
  connection is checked every 15 s).
- **Demo mode** (`STRATUMTAP_DEMO=1`) synthesises `$GPRMC`, `$GPGGA`, `$GPGSA`, 3×`$GPGSV` and
  `$GPVTG` with valid checksums every second and publishes `nmea`/`gpsd`/`ntp` exactly like
  the live sources, so the whole streaming surface works without a receiver.
- **The NMEA ring survives a gpsd reconnect** — the last sentences before a drop are
  usually the interesting ones.
