---
title: API
nav_order: 5
has_children: true
---

# The JSON API
{: .no_toc }

Everything the UI shows is available under `/api/v1/`. It is a plain read-only JSON API — no
authentication, no write endpoints, no rate limiting.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

{: .note }
> This page is the friendly tour. The exact, field-by-field contract — every key, every unit,
> every sign convention — is the [API contract reference](api-contract.md).

---

## Base URL

```
http://<your-server>:8080/api/v1
```

Interactive documentation, generated from the code, is at `/docs`, and the OpenAPI schema at
`/openapi.json`. If you would rather click than read, start there.

All responses set `Cache-Control: no-store`. Nothing is ever cached, which is what you want
from a clock.

---

## The `server` block

Every JSON response carries a `server` object:

```json
"server": {
  "t_recv": 1787753871.123456,
  "t_send": 1787753871.123980,
  "t0": 1787753871.100,
  "hostname": "stratum1",
  "version": "0.1.0",
  "demo": false,
  "uptime_s": 12345.6
}
```

| Field | Meaning |
|---|---|
| `t_recv` | Server wall clock when the request was received, captured as early as possible. |
| `t_send` | Server wall clock just before the response was sent, captured as late as possible. |
| `t0` | Echo of your `?t0=` query parameter, or `null`. |
| `hostname` | The display name (`STRATUMTAP_HOSTNAME`, else the machine hostname). |
| `version` | StratumTap's version. |
| `demo` | `true` when the data is synthetic. |
| `uptime_s` | How long the service has been running. |

`t_recv` and `t_send` are what make the browser's clock synchronization possible: they let a
client subtract the server's own processing time from the round trip.
[How that works](technical/time-correction.md).

### The `t0` echo

Every endpoint accepts an optional `?t0=<unix seconds as a float>`, echoed back verbatim in
`server.t0`. It lets a client correlate a response with the exact moment it sent the request
without keeping state:

```sh
curl -s "http://stratum1.local:8080/api/v1/time?t0=$(date +%s.%N)" | jq .server
```

The server never interprets `t0` — it only hands it back.

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/time` | The cheapest possible timestamp exchange, for clock sync. |
| GET | `/api/v1/status` | Everything: `server`, `ntp`, `gps` (including satellites). |
| GET | `/api/v1/ntp` | chrony tracking state only. |
| GET | `/api/v1/ntp/sources` | Parsed `chronyc sources` and `sourcestats`. |
| GET | `/api/v1/gps` | Fix, position, motion, accuracy, DOPs, time offset, satellites. |
| GET | `/api/v1/gps/satellites` | Satellites only. |
| GET | `/api/v1/history` | The server-side history ring buffer. `?seconds=` `?max=` `?format=json\|csv`. |
| GET | `/api/v1/raw/chronyc/tracking` | Verbatim `chronyc tracking` text. |
| GET | `/api/v1/raw/chronyc/sources` | Verbatim `chronyc sources -v` text. |
| GET | `/api/v1/raw/chronyc/sourcestats` | Verbatim `chronyc sourcestats -v` text. |
| GET | `/api/v1/raw/gpsd` | The last raw gpsd message per class. |
| GET | `/api/v1/raw/nmea` | The newest NMEA sentences from the ring buffer. `?n=200`. |
| GET | `/api/v1/stream` | Server-Sent Events: `nmea`, `gpsd`, `ntp`, `status`. |
| GET | `/api/v1/stream/nmea.txt` | Chunked plain-text NMEA, for `curl -N`. |
| GET | `/api/v1/config` | Non-secret UI configuration. |
| GET | `/api/v1/health` | Liveness flags. `?strict=1` returns 503 when unhealthy. |

---

## Examples

### Is the clock good right now?

```sh
curl -s http://stratum1.local:8080/api/v1/time | jq .
```

```json
{
  "server": { "t_recv": 1787753871.123456, "t_send": 1787753871.123980, "t0": null,
              "hostname": "stratum1", "version": "0.1.0", "demo": false, "uptime_s": 12345.6 },
  "ntp_synchronized": true,
  "ntp_system_offset_s": 3.72e-7,
  "ntp_stratum": 1
}
```

That is the whole response — deliberately tiny, so it costs almost nothing to poll.

### The numbers you would put on a dashboard

```sh
curl -s http://stratum1.local:8080/api/v1/status | jq '{
  offset_us: (.ntp.system_offset_s * 1e6),
  stratum:   .ntp.stratum,
  synced:    .ntp.synchronized,
  reference: .ntp.reference_name,
  fix:       .gps.fix.fix_text,
  used:      .gps.satellites.used,
  seen:      .gps.satellites.seen,
  hdop:      .gps.dop.hdop
}'
```

```json
{
  "offset_us": 0.372,
  "stratum": 1,
  "synced": true,
  "reference": "PPS",
  "fix": "3D DGPS FIX",
  "used": 10,
  "seen": 12,
  "hdop": 0.97
}
```

### The last hour of history as CSV

```sh
curl -s 'http://stratum1.local:8080/api/v1/history?seconds=3600&format=csv' -o history.csv
head -2 history.csv
```

```
t_iso,t,ntp_system_offset_s,ntp_last_offset_s,ntp_rms_offset_s,ntp_frequency_ppm,ntp_stratum,gps_mode,gps_sats_used,gps_sats_seen,gps_hdop,gps_eph_m,gps_time_offset_s,lat,lon,alt_hae_m
2026-08-26T18:50:02.000Z,1787770202.0,3.72e-07,9.94e-07,6.9e-07,17.97,1,3,10,12,0.97,3.7,1.23e-06,51.477899,-0.0015,198.9
```

Add `&max=17280` to get every stored point instead of the default 720.

### The raw tool output

```sh
curl -s http://stratum1.local:8080/api/v1/raw/chronyc/tracking
curl -s http://stratum1.local:8080/api/v1/raw/gpsd | jq 'keys'
curl -s 'http://stratum1.local:8080/api/v1/raw/nmea?n=5' | jq -r '.lines[].line'
```

The `chronyc` endpoints return `text/plain`, always with HTTP 200. If the collector has
nothing to show they return a one-line explanation instead of tool output, for example
`chronyc tracking unavailable: 506 Cannot talk to daemon`.

### Live streams

```sh
# Server-Sent Events. Ctrl-C to stop.
curl -N 'http://stratum1.local:8080/api/v1/stream?events=nmea,ntp'

# just raw NMEA, straight into a file
curl -N http://stratum1.local:8080/api/v1/stream/nmea.txt > capture.nmea

# the full status payload every 2 s
curl -N 'http://stratum1.local:8080/api/v1/stream?events=status&status_interval=2'
```

`-N` (`--no-buffer`) matters — without it curl buffers and you see nothing.

The SSE frames look like this:

```
id: 1
event: hello
data: {"client_id":3,"events":["nmea","ntp"],"server":{...},"queue":500}

id: 2
event: nmea
data: {"t":1787758473.0123,"line":"$GPRMC,...*6A","type":"RMC","talker":"GP","checksum_ok":true}
```

Every 10 seconds a `stats` event reports how many events you have been sent and how many were
dropped. Design details: [Streaming](technical/streaming.md).

---

## Monitoring with `/health`

```sh
curl -s http://stratum1.local:8080/api/v1/health | jq .
```

```json
{
  "ok": true,
  "ntp_ok": true,
  "gpsd_connected": true,
  "gps_fix": true,
  "loop_lag_ms": 2.1,
  "stream_clients": 1,
  "mqtt": {"enabled": false, "connected": false, "publishes": 0, "errors": 0, "last_publish_at": null, "last_reason": null, "last_error": null},
  "server": { "...": "..." }
}
```

| Flag | True when |
|---|---|
| `ntp_ok` | chrony data is available **and** chrony reports itself synchronized. |
| `gpsd_connected` | The TCP session to gpsd is up. |
| `gps_fix` | The fix mode is 2D or better. |
| `ok` | All three of the above. |
| `loop_lag_ms` | *(not a flag)* Worst event-loop scheduling overshoot in the last 60 s. Single-digit on a healthy Pi. |
| `stream_clients` | *(not a flag)* Current SSE subscribers. |

The plain endpoint always returns HTTP 200 so the UI can read the individual flags. For
monitoring, add `?strict=1` — then it returns **503** whenever `ok` is false:

```sh
curl -fsS 'http://stratum1.local:8080/api/v1/health?strict=1' >/dev/null \
  || echo "StratumTap unhealthy"
```

That is the form to point Uptime Kuma, Nagios, a Docker `HEALTHCHECK` or a systemd timer at.

{: .note }
> `?strict=1` goes red when there is **no GPS fix**, which on a timing installation is a real
> alarm. If you only care that the web service is alive, use the plain endpoint and check the
> HTTP status.

---

## Error handling

The one thing to internalize: **a broken collector never produces a 5xx**.

- An unreachable gpsd or a failing `chronyc` shows up as `"available": false` with a
  human-readable `"error"` string inside the relevant domain object. The HTTP status stays
  200. This is what lets the UI show a degraded panel instead of a broken page — and it means
  your monitoring script must look at `available`, not just at the status code.
- An unknown `/api/v1/*` path returns 404 with `{"detail": "Not found"}`.
- `/api/v1/health?strict=1` is the *only* endpoint that returns 503 for a data problem.
- `/api/v1/stream` returns 503 `{"detail": "too many stream clients"}` when the subscriber cap
  is reached, and 400 for an unknown `events` value.

Values that are simply unavailable are `null`, never `0` and never omitted.

---

## Interactive docs

`/docs` serves FastAPI's Swagger UI, generated from the same Pydantic models that produce the
responses — so it cannot drift from reality. `/openapi.json` is the schema, if you want to
generate a client.

{: .note }
> `/docs` loads its assets from a CDN, so on a fully isolated network the page will be blank.
> `/openapi.json` still works, and so does this documentation.

---

## Versioning and stability

The path carries the version: `/api/v1/`. Within v1, fields are added but not removed or
redefined, and units and sign conventions do not change. A field you do not recognize is safe
to ignore.

The models in `stratumtap/models.py` are the single source of truth; the
[API contract reference](api-contract.md) documents them field by field.
