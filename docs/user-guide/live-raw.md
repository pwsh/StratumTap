---
title: Live raw stream
parent: User guide
nav_order: 3
---

# Live raw stream
{: .no_toc }

The **Live raw (streamed)** panel at the bottom of the [detail view](detail-view.md) is a
console fed by Server-Sent Events: raw NMEA sentences, every gpsd JSON object and each chrony
update, as they happen.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

![Live raw panel: Disconnect, Pause and Clear buttons, NMEA, gpsd JSON and ntp checkboxes, a filter box, Auto-scroll, a live status chip with sent, dropped, queue and clients counters, per-sentence rate chips, and a scrolling log of timestamped NMEA sentences and gpsd JSON objects with Save .nmea and Save .jsonl buttons](../assets/screenshots/detail-live-raw.png)

---

## Connect and disconnect

The panel mounts **disconnected**. Nothing touches `/api/v1/stream` until you press
**Connect** — no browser sitting on the detail view is silently holding a stream open.

- **Connect / Disconnect** opens and closes the stream. Your choice is remembered, so the
  panel reconnects the next time you visit the detail view.
- **Pause** keeps the stream open and keeps buffering, but stops redrawing. Useful when you
  want to read a line that keeps scrolling away.
- **Clear** empties the log and the counters.

The stream also closes when you navigate away from the detail view, and — if **Pause when
hidden** is on — when the tab goes into the background.

The status chip on the left reads `disconnected`, `connecting`, `live`, `reconnecting` or
`refused`.

---

## Choosing what to watch

| Checkbox | What it adds |
|---|---|
| **NMEA** | Raw NMEA sentences exactly as the receiver sent them, relayed by gpsd. |
| **gpsd JSON** | Every gpsd JSON object: `TPV`, `SKY`, `GST`, `PPS`, `TOFF`, `DEVICE`, and so on. |
| **ntp** | One line after each successful `chronyc tracking` poll, summarizing offset, stratum and last offset. |

Changing the selection reconnects with the new subscription.

**Filter** does a plain substring match on the line, case-insensitively — type `GGA` for just
that sentence type, `$GP` for GPS talker sentences, `TPV` for position reports. It filters
what is displayed, not what is received, so clearing it brings the buffered lines back.

**Auto-scroll** keeps the newest line in view. Turn it off to read back through the buffer.

---

## Reading the log

Each row is: server receive time (UTC), a badge (`NMEA`, `JSON` or `NTP`), and the payload.

- NMEA rows show the sentence verbatim, including its `*XX` checksum.
- gpsd rows are clipped to keep the row a sensible width; hover for the full JSON.
- A sentence whose **checksum does not match** is highlighted, and hovering says
  `checksum mismatch`.

The panel keeps 2 000 entries in memory for export and renders at most 500 rows in the page,
so a long session cannot grow without bound. Events are batched into a single animation frame
rather than touching the page per event, which is what keeps a 10–20 event/s stream from
making the tab sluggish.

---

## The chips

**Rate chips** (one per sentence type) show `count · rate/s` over the last 10 seconds, with
the sentences a GPS/NTP operator looks for first — `RMC`, `GGA`, `GSA`, `GSV`, `VTG`, `GLL`,
`ZDA`, `GST`, `TXT` — ordered ahead of the rest. There is a **TOTAL** chip and a **CHECKSUM
ERR** chip; the latter should stay at zero. A non-zero checksum error count points at the
serial link between the receiver and the Pi, not at StratumTap.

**Stat chips** come from the server, once every 10 seconds:

| Chip | Meaning |
|---|---|
| **SENT** | Events the server has queued for you since you connected. |
| **DROPPED** | How many of those you lost because your queue was full. `SENT − DROPPED` is what you actually got. |
| **QUEUE** | Your current server-side backlog, out of the queue size (500 by default). |
| **CLIENTS** | How many stream subscribers the server currently has. |

A rising **DROPPED** means your browser or network could not keep up. That is by design: the
server discards your oldest queued events rather than slowing down data collection. See
[Streaming design](../technical/streaming.md).

---

## Saving a capture

- **Save .nmea** writes the buffered NMEA sentences, one per line, no timestamps — the format
  every NMEA tool expects.
- **Save .jsonl** writes one JSON object per line for *everything* in the buffer (NMEA, gpsd
  and ntp entries alike), with timestamps, for scripted analysis.

The counter beside them says how many NMEA sentences and how many total events are held.

You can also capture straight from the server without opening a browser at all:

```sh
curl -N http://stratum1.local:8080/api/v1/stream/nmea.txt > capture.nmea
```

That is a plain chunked `text/plain` stream of sentences — no SSE framing, no timestamps.
Stop it with Ctrl-C.

---

## Snapshot without connecting

**Snapshot last 200 (poll)** fetches `/api/v1/raw/nmea?n=200` — the newest sentences from the
server's ring buffer — as a single request. No stream is opened.

Use it when:

- you only want a quick look and do not want to hold a stream slot open;
- the stream was refused because the client cap is reached;
- you want the last sentences *before* something went wrong. The ring buffer survives a gpsd
  reconnect, and the sentences just before a drop are usually the interesting ones.

The ring holds 1 000 sentences by default (`STRATUMTAP_NMEA_RING`).

---

## When the server refuses

The status chip reads **refused** and a **Retry** button appears.

The usual cause is the concurrent-subscriber cap: `/api/v1/stream` answers
`503 {"detail": "too many stream clients"}` beyond `STRATUMTAP_STREAM_MAX_CLIENTS`
(16 by default). Close a stream in another tab or on another machine, then press Retry.
Browsers do not retry a stream that failed with a non-200 status, so StratumTap drives its own
retry with a limited error budget and then hands you the button.

Other reasons: a browser with no `EventSource` support, or a reverse proxy that buffers the
response — see [Configuration](../configuration.md#reverse-proxy) for the `proxy_buffering off`
requirement.

---

## What it is not

{: .note }
> **StratumTap never opens the serial port.** gpsd owns the receiver; StratumTap asks gpsd to
> relay the raw sentences on the same JSON socket it already uses
> (`?WATCH={"enable":true,"json":true,"pps":true,"nmea":true}`). Nothing here competes with
> gpsd, chrony or `cgps` for the device, and stopping StratumTap cannot disturb the time
> stack.

There is no way to *send* anything to the receiver from this panel. StratumTap only reads.
