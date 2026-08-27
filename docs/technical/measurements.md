---
title: What every number means
parent: Technical
nav_order: 2
---

# What every number means
{: .no_toc }

Where each value comes from, what it is measuring, and which way its sign points.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

---

## Where the data comes from

Two sources, and neither of them is a screen scrape.

**chrony** is read with `chronyc -c tracking` (CSV mode) once a second, plus the
human-readable `chronyc tracking` for the verbatim panel. The CSV is authoritative whenever
both succeed; if the CSV fails but the text parses, the text is used and the panel stays
available. `chronyc sources` and `sourcestats` are polled every 10 s.

**gpsd** is read over its JSON socket on `127.0.0.1:2947` — the same feed `cgps` uses — with a
persistent connection and exponential-backoff reconnect. StratumTap sends
`?WATCH={"enable":true,"json":true,"pps":true,"nmea":true}` and folds the resulting `TPV`,
`SKY`, `GST`, `PPS`, `TOFF`, `DEVICES`/`DEVICE` and `VERSION` messages into one snapshot.

Both run in background tasks. API request handlers only ever read the resulting in-memory
snapshot, so a request never waits on a subprocess or a socket.

{: .note }
> Everything in the API is SI, with the unit in the field name: `_s` seconds, `_m` meters,
> `_mps` m/s, `_deg` degrees, `_ppm` parts per million. Unit conversion for display happens in
> the browser. A value that is unavailable is `null` — never `0`, never omitted.

---

## chrony tracking fields

### Reference ID and name

`Reference ID : 50505300 (PPS)`. The hex value is a raw 32-bit identifier; the name in
parentheses is what you actually want — a refclock name like `PPS`, or a hostname/IP for a
network server. StratumTap shows the name on the pill and keeps the hex in the tooltip.

### Stratum

Distance from a stratum-0 reference. A GPS/PPS-disciplined server is **stratum 1**. Stratum 16
means unsynchronized.

### System time (the big number)

{: .note }
> **`ntp.system_offset_s` — positive means the system clock is FAST (ahead of true time),
> negative means SLOW.** The UI always prints the word next to the number, so you never have
> to remember.

This is chrony's best estimate of the current error of the system clock, and it is the value
the [gauge](../user-guide/detail-view.md#time-accuracy-the-gauge) shows. On a PPS-disciplined
Pi it lives in the tens or hundreds of nanoseconds.

#### The CSV-versus-text sign flip

Worth knowing if you compare StratumTap's number with a script of your own.

In `chronyc -c tracking` CSV output, the *System time* field is chrony's internal
`current_correction` — **the correction that needs to be applied**, so positive means the
system clock is *slow*. In the human-readable report chrony flips it for you and prints
"0.000000372 seconds fast".

The two formats therefore disagree in sign. StratumTap normalizes to the human sense
(positive = fast):

- from the CSV: `system_offset_s = −csv_field`
- from the text: `+value` when the word is "fast", `−value` when "slow"

The *Frequency* field needs no flip — it is already positive-means-fast in both formats.

### Last offset

The offset measured at the most recent clock update, same sign sense as System time. It jumps
around; System time is the filtered view.

### RMS offset

A long-term root-mean-square average of the offsets. **Unsigned** — it is a magnitude, a
stability figure. If it is much larger than the current offset, the clock has been having a
rough time.

### Frequency (ppm)

How fast or slow the system clock *would* run with no correction, in parts per million. A
typical Pi crystal shows 10–30 ppm; 17.97 ppm fast means the clock would gain about 1.55
seconds per day if chrony stopped steering it.

**Positive means the clock runs fast (gains time)**, matching the "fast"/"slow" word in
chrony's own report. This value should be almost constant, drifting slowly with temperature.

### Residual frequency

The frequency error still visible in the *current reference's* measurements — how much chrony
thinks its own frequency estimate is off. Near zero once chrony has settled. A persistently
non-zero residual means it is still chasing something.

### Skew

chrony's estimated error bound on the frequency, in ppm. Smaller means more confident. It
widens when samples are noisy or sparse.

### Root delay and root dispersion

`root_delay_s` is the total round-trip network delay accumulated along the whole chain back to
stratum 0. For a locally attached PPS refclock it is essentially zero — nanoseconds.

`root_dispersion_s` is the accumulated *error bound* inherited from the same chain. Together
they give NTP's classic bound on how far off true time you might be:
`root_delay/2 + root_dispersion`.

### Update interval

Seconds between chrony's last two clock updates. It changes as chrony adapts its polling.

### Leap status

`Normal`, `Insert second`, `Delete second` or `Not synchronised`. Anything other than `Normal`
gets an amber pill. `Insert second` appearing in the last hours of June 30 or December 31 is
chrony telling you a real leap second is coming.

### Synchronized

Derived, not printed by chrony: `stratum < 16 && leap_status != "Not synchronised"`. It is what
the green NTP pill and `/api/v1/health`'s `ntp_ok` use.

---

## NTP sources fields

`chronyc sources` gives one row per source. **Mode**: `^` server, `=` peer, `#` refclock.
**State**: `*` current best, `+` combined, `-` not combined, `x` falseticker, `~` too
variable, `?` unusable.

Two parsing quirks StratumTap handles so you do not have to:

- `reach` is printed in **octal even in CSV mode** (`377`). It is parsed base-8 into the
  integer 0–255 and the original text is kept as `reach_octal`. 255 means the last eight polls
  all arrived.
- `LastRx` is `4294967295` (uint32 max) for a source that has never been received; that
  becomes `null`, displayed as an em dash.

`poll` is log₂ seconds — `2^3` is an 8-second interval — and `poll_interval_s` gives you the
seconds directly.

`sourcestats` adds the regression view: **NP** samples retained, **NR** runs of same-signed
residuals (a fit-quality indicator), the **span** they cover, the estimated **frequency**
residual and its **skew**, the **offset** at the last sample and the sample **standard
deviation**.

---

## gpsd fields

### Mode, status and fix text

`mode` is `0` unknown, `1` no fix, `2` 2D, `3` 3D. `status` is a separate enum describing
*how* the fix was obtained: `2` DGPS, `3` RTK fixed, `4` RTK float, `5` dead reckoning, `6`
GNSS+DR, `7` time-only (surveyed), `8` simulated, `9` P(Y). gpsd never emits `0` or `1` for
status, so those come back as `null`.

StratumTap combines them into the cgps-style label you see on the pill: `NO FIX`, `2D FIX`,
`3D FIX`, `3D DGPS FIX`, `FIXED SURVEYED` and so on.

**Fix age** (`fix_age_s`) is how long the fix *mode* has been unchanged — the "(6 secs)" cgps
prints. It is **not** how stale the position is; that is `time_age_s`, below.

### ept — the receiver's time error estimate

`fix.ept_s` is the receiver's own 1σ estimate of the error on the timestamp it just gave you.
Milliseconds on a plain NMEA receiver. It says nothing about PPS quality — PPS bypasses the
serial timestamp entirely.

### The error estimates: eph, sep, epx/epy/epv

All 1σ, all reported by the receiver rather than computed by StratumTap.

| Field | Meaning |
|---|---|
| `epx_m` / `epy_m` | Longitude and latitude error, per axis. |
| `epv_m` | Vertical error. |
| `eph_m` | **Horizontal (2D) error — CEP.** The solid accuracy circle on the map. |
| `sep_m` | **Spherical (3D) error — SEP.** The dashed circle. Always larger than `eph_m`. |
| `eps_mps` / `epd_deg` / `epc_mps` | Speed, track and climb error estimates. |
| `ept_s` | Time error estimate (same value as `fix.ept_s`). |

The distinction that matters: **EPH is a circle in the horizontal plane; SEP is a sphere.**
Vertical accuracy from GNSS is always worse than horizontal — the satellites are all above
you, never below — so SEP exceeds EPH, typically by 30–60 %.

These are estimates the receiver produces from its own solution, not measured truth. Treat
them as an order of magnitude, not a guarantee.

### DOPs

Dilution of precision: pure satellite **geometry**, nothing else. `xdop`/`ydop` east and
north, `vdop` vertical, `hdop` horizontal, `pdop` 3D position, `tdop` time, `gdop` everything
including time.

Lower is better; under 2 is good. Multiply a DOP by the receiver's per-satellite ranging error
to get an error estimate — which is roughly how the EP\* values above are produced.

A high HDOP with strong signals means the satellites are bunched together in the sky. The
[sky plot](../user-guide/detail-view.md#sky-plot) shows you that directly.

### GST — the error ellipse

The NMEA `GST` sentence carries pseudorange-noise statistics, and gpsd passes them through as
its `GST` class. When the receiver sends it, you get a real 1σ error **ellipse** rather than a
circle: `rms_m` of the residuals, `major_m` and `minor_m` semi-axes, `orient_deg` (bearing of
the major axis from true north), and per-axis `lat_err_m`, `lon_err_m`, `alt_err_m`.

The ellipse is drawn on the map in violet. Many receivers never send `GST`; when that happens
the whole block is absent and the map simply has no ellipse.

### Altitudes and geoid separation

`alt_hae_m` is height above the **WGS-84 ellipsoid**, which is what GNSS actually solves for.
`alt_msl_m` is height above **mean sea level** — what a map or an altimeter means. They differ
by the geoid separation (`geoid_sep_m`), which can be tens of meters and is reported
separately.

Use MSL to compare with a map, HAE to compare with another GNSS receiver.

### Maidenhead grid square

`grid_square` is an 8-character Maidenhead locator computed by StratumTap from the latitude
and longitude — the format amateur radio operators use. Eight characters is roughly a 250 m
box.

### Devices

`device` is whichever device produced the last position report; `devices` lists everything
gpsd has told us about. A PPS-disciplined Pi typically shows two: the serial receiver
(`/dev/ttyAMA0`, driver `MTK-3301`, 9600 bps, 1 s cycle) and the PPS device (`/dev/pps0`,
driver `PPS`).

---

## The three time offsets

This is the question the project gets asked most, and all three numbers are correct.

| Shown as | Field | What it measures | Typical size |
|---|---|---|---|
| **System clock offset** (the gauge) | `ntp.system_offset_s` | chrony's estimate of the system clock's error against its reference | nanoseconds |
| **GPS→system offset** | `gps.time_offset.offset_s` | System clock minus GPS time, straight from gpsd's `PPS` or `TOFF` message | µs with PPS, 0.1–1 s with TOFF |
| **Fix age (cgps "Time offset")** | `gps.fix.time_age_s` | Server send time minus the timestamp inside the last position report | 100–1000 ms |

### Why they differ

**System clock offset** is a *disciplined* number. chrony has been steering the clock against
the PPS refclock for hours; this is the residual error after all that work. Nanoseconds is
what success looks like.

**GPS→system offset** is a *raw* measurement, `clock − real`, from a single gpsd message.

- From a **`PPS`** message it is the pulse-per-second edge compared against the system clock at
  the moment of the interrupt. This is a hardware-timed comparison, so it is microseconds or
  better — and it is roughly the raw input chrony is disciplining against.
- From a **`TOFF`** message it is the *serial* timestamp compared against the system clock. The
  receiver stamps the fix, then spends time formatting NMEA and shifting it out at 9600 baud.
  Hundreds of milliseconds of that latency is completely normal and says nothing bad about your
  timing.

StratumTap prefers `PPS` when a recent one exists and falls back to `TOFF` otherwise, and the
tile always names the source it used. Both values are also shown individually on the
[gauge card](../user-guide/detail-view.md#time-accuracy-the-gauge).

**Fix age** is not an offset at all — it is **staleness**. `cgps -s` computes it as
`CLOCK_REALTIME now − TPV.time` and labels the line "Time offset", which is where the
confusion starts. StratumTap computes exactly the same thing (`t_send − fix.time_unix`) at
response time and labels it *Fix age (cgps)* to be clearer, keeping cgps's nine-decimal string
underneath so you can compare directly.

It is dominated by the same serial latency as `TOFF`, plus however long ago the last fix was.

{: .note }
> **Rule of thumb.** If the *system clock offset* is small, your server's time is good — that
> is the number that matters. A large *fix age* or *TOFF offset* is telling you about the
> serial link, not about your clock. The `PPS` offset is the one that should track the system
> offset, because it is essentially the same measurement one step earlier in the chain.

---

## How the gauge picks its scale

The [time-accuracy gauge](../user-guide/detail-view.md#time-accuracy-the-gauge) auto-ranges,
which is only useful if it does so predictably.

**A fixed decade ladder.** Full scale is always one of ±1 µs, 10 µs, 100 µs, 1 ms, 10 ms,
100 ms, 1 s, 10 s. Arbitrary "nice" scales would make two glances at different times
incomparable; a fixed ladder means the rung itself carries information, and the caption always
states it.

**Selection.** It tracks the peak absolute value of the last 60 readings and chooses the
smallest rung that is at least 1.25 × that peak — enough headroom that the needle is not
pinned at the end.

**Asymmetric hysteresis.** Growing is immediate: a spike must be visible at once. Shrinking
only happens after the value has stayed below 20 % of the current full scale for 30
consecutive readings. A rung that is too big is merely unflattering; a rung that flaps between
two scales is unreadable.

**Color is absolute, not relative.** The needle is colored from the magnitude of the offset —
green under 100 µs, amber under 10 ms, red above — never from where it sits on the arc.
Otherwise a 200 ns offset on a ±1 µs scale would swing hard right and look alarming, when it
is in fact excellent.

---

## See also

- [How the browser time correction works](time-correction.md) — a *fourth* offset, entirely
  about your browser, unrelated to the three above
- [Receivers and chrony variants](receivers.md) — which of these fields your hardware will
  actually populate
- [The API contract](../api-contract.md) — every field, with its exact type and units
