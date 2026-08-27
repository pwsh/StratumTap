---
title: Streaming design
parent: Technical
nav_order: 3
---

# Streaming design
{: .no_toc }

How `/api/v1/stream` pushes raw data to browsers without ever letting a slow browser slow down
the machine's own data collection.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

---

## What is on the stream

Four event families, selected with the `events` query parameter (default `nmea,gpsd`):

| Event | Payload |
|---|---|
| `hello` | `{"client_id": 3, "events": ["nmea","gpsd"], "server": {…}, "queue": 500}` — always the first event |
| `nmea` | `{"t": 1787758473.0123, "line": "$GPRMC,…*6A", "type": "RMC", "talker": "GP", "checksum_ok": true}` |
| `gpsd` | The raw gpsd JSON object exactly as received, plus `"_t"` (server receive time) |
| `ntp` | The full `ntp` snapshot after every **successful** `chronyc tracking` poll |
| `status` | The complete `/status` payload every `status_interval` seconds (1–60, default 2); only sent if you asked for `status` |
| `stats` | `{"t": …, "sent": 1234, "dropped": 0, "queue_len": 3, "clients": 2}` every 10 s |

Details worth knowing:

- Event ids start at `1` for `hello` and increment per event on that connection, `stats` and
  `status` included. Keepalives are SSE comments and carry no id.
- `hello.events` lists the accepted events in canonical order (`nmea`, `gpsd`, `ntp`,
  `status`), not the order you listed them.
- A **failing** `chronyc` produces no `ntp` event. It surfaces in the `status` event and in
  `/api/v1/ntp` as `available: false` — the stream is not the place to learn about an outage.
- An unknown or empty `events` list is a `400`; a `status_interval` outside 1–60 is a `422`.
- A `: keepalive` comment goes out after 15 s of silence, so proxies and browsers do not time
  the connection out.

There is also `/api/v1/stream/nmea.txt`: a chunked `text/plain` stream of raw sentences, one
per line, no timestamps and no SSE framing, for `curl -N … > capture.nmea`. An idle stream
simply stays silent while the connection is checked every 15 s.

---

## Why Server-Sent Events, not WebSocket

The data flows one way — server to browser — and it is text. That is exactly the shape SSE was
designed for, and it buys several things a WebSocket would cost extra effort to match:

- **It is plain HTTP.** Same port, same reverse proxy, same TLS, same auth, no protocol
  upgrade to configure. `curl -N` reads it directly, which makes debugging trivial.
- **Reconnection is built in.** `EventSource` reconnects on its own after a dropped
  connection.
- **Event ids and named events come free** in the wire format, so the client does not need a
  message-type envelope.
- **The browser API is tiny.** No framing, no ping/pong, no close codes.

The one thing SSE does not give you is a client→server channel — and StratumTap has nothing to
send upstream. It is a read-only viewer of a time server; letting a browser write to it would
be a feature request, not a missing capability.

{: .note }
> `EventSource` does **not** retry after a response the spec calls fatal — any non-200 status,
> including our `503 too many stream clients`. StratumTap therefore drives its own retry when
> the connection lands in `CLOSED`, with a limited error budget, and then offers a manual
> **Retry** button rather than hammering the server.

---

## gpsd relays the NMEA — StratumTap never opens the port

gpsd is watched with `{"enable":true,"json":true,"pps":true,"nmea":true}`. With `nmea` set,
gpsd interleaves the raw sentences (lines beginning `$` or `!`) with its JSON on the **same
socket** StratumTap already has open.

This matters more than it might look:

- **No contention for the device.** The serial port belongs to gpsd. StratumTap does not open
  it, cannot lock it, and cannot disturb chrony's PPS or a running `cgps`.
- **Nothing to configure.** No device path, no baud rate, no permissions — if gpsd can read
  the receiver, StratumTap can see the sentences.
- **It works over the network.** Point `STRATUMTAP_GPSD_HOST` at another machine's gpsd and
  the raw stream comes with it.
- **Stopping StratumTap is always safe.** It is a reader. There is no state in the time stack
  that depends on it.

---

## The non-starvation rules

The hard requirement: **a browser that stops reading must never slow down the gpsd reader or
the chrony poller.** A Raspberry Pi collecting timing data cannot be held hostage by a laptop
that went to sleep with a tab open.

Five rules, enforced in code and covered by tests:

**1. Producers never await consumers.** Publishing is a plain synchronous method that does one
`put_nowait` per subscriber. The gpsd reader and the chrony poller never `await` a client, so a
stalled socket cannot apply backpressure to data collection. The payload is serialized once
per publish, not once per subscriber.

**2. Bounded queues, drop the oldest.** Each subscriber has an `asyncio.Queue` of
`STRATUMTAP_STREAM_QUEUE` events (500 by default). When it is full, the **oldest** queued event
is discarded and the subscriber's `dropped` counter is incremented. For live raw data, the
newest sentence is always more useful than a stale one.

**3. A client cap.** `STRATUMTAP_STREAM_MAX_CLIENTS` (16) concurrent subscribers. Beyond that
`/api/v1/stream` answers `503 {"detail": "too many stream clients"}` — a clear refusal rather
than degrading everyone. Memory use is therefore bounded at
`max_clients × queue_size` events.

**4. NMEA never triggers a snapshot rebuild.** Raw sentences are appended to a ring buffer
(`STRATUMTAP_NMEA_RING`, 1 000 lines) and broadcast. Only JSON objects fold into the
`GpsSnapshot`. At 10–20 sentences a second, rebuilding the whole snapshot per line would be
pure waste.

**5. Disconnects are always cleaned up.** A client that goes away is detected on the next write
or via the request's disconnect flag, and the subscriber is removed in a `finally:` block, so a
crashed browser cannot leak a queue.

Two things make this observable rather than merely asserted: the per-subscriber `stats` event
(`sent`, `dropped`, `queue_len`, `clients`) that the Live raw panel displays as chips, and
`loop_lag_ms` in `/api/v1/health` — the worst event-loop scheduling overshoot in the last 60
seconds, measured by a 1 s ticker. If publishing were ever blocking, that number would climb.

{: .note }
> The NMEA ring survives a gpsd reconnect. The sentences immediately before a drop are usually
> the interesting ones, so `Snapshot last 200` still shows them after the link comes back.

---

## Load-test results

Measured against a running instance on a **Raspberry Pi 4** with 14 stream clients — 12
well-behaved readers plus **2 stalled** clients that connect and then never read a byte, the
worst case for a broadcaster — while `/api/v1/status` was polled continuously.

| Measurement | Result |
|---|---|
| Collector cadence | **1 s, intact** — chrony and gpsd data ages unchanged |
| `/api/v1/status` latency, p95 | **20 ms** |
| Event-loop lag (`loop_lag_ms`) | **4 ms** |
| CPU | **≤ 7 %** |

The point of the test is not the throughput number. It is that the two stalled clients had no
measurable effect on anything else: their queues filled, their `dropped` counters climbed, and
the collectors kept their cadence exactly as designed.

---

## Running the load test yourself

`scripts/stream_loadtest.py` is standard library only, so it runs from any machine with
Python 3 — run it from a *different* machine than the target, so you are not measuring your own
test harness competing for the Pi's CPU.

```sh
python3 scripts/stream_loadtest.py --base http://stratum1.local:8080 \
    --readers 12 --stalled 2 --seconds 60
```

| Option | Default | Meaning |
|---|---|---|
| `--base` | `http://127.0.0.1:8080` | Target base URL. |
| `--readers` | `12` | Well-behaved SSE readers subscribing to `nmea,gpsd,ntp`. |
| `--stalled` | `2` | Clients that connect and never read. |
| `--seconds` | `60` | Test duration. |
| `--poll-hz` | `5` | How often to poll `/api/v1/status` while the test runs. |

It reports `/api/v1/status` latency (mean, p95, max), the chrony and gpsd data **ages** — which
are the real proof that collection kept its cadence — the event-loop lag and client count from
`/api/v1/health`, per-reader event throughput, the HTTP status the stalled clients got, and a
one-line verdict.

{: .warning }
> `--readers` plus `--stalled` must stay under `STRATUMTAP_STREAM_MAX_CLIENTS` (16), or the
> extra clients are refused with 503 and the test measures the cap rather than the fan-out.
> If you want to test the cap, that is a separate, deliberate run.

What to look for:

- **Data ages** should stay around the collector intervals. If they grow with client count,
  something is starving.
- **`loop_lag_ms`** should stay in the single digits.
- **`dropped`** on the well-behaved readers should be 0. On the stalled ones it should climb
  steadily — that is the mechanism working.

---

## See also

- [Live raw stream](../user-guide/live-raw.md) — the browser side of all this
- [Configuration: streaming](../configuration.md#streaming) — the three tunables
- [The API contract](../api-contract.md) — exact event and frame formats
