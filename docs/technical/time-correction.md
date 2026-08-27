---
title: How the browser time correction works
parent: Technical
nav_order: 1
---

# How the browser time correction works
{: .no_toc }

A clock on a web page is normally a lie. StratumTap's is measurably less of one, and it tells
you by how much.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

---

## The problem: a displayed server time is stale

The naive way to show a server's clock in a browser is to ask the server what time it is and
print the answer. That answer is wrong the moment it arrives, and wrong by an unknown amount:

1. Your request takes some time to reach the server.
2. The server does some work.
3. The response takes some time to reach you.

By the time the number is on your screen it is older than the one-way return trip — and you
have no idea whether that was 0.3 ms on a wired LAN or 180 ms over a mobile connection and a
VPN. Printing "the server said 19:49:22.663" without saying how stale it is makes the display
look far more precise than it is, which on a page about a stratum-1 time server is exactly the
wrong impression to give.

Worse, if you then *tick that value forward* using `setInterval`, you have compounded a stale
value with a browser timer that is itself imprecise and gets throttled in background tabs.

The fix is the one NTP itself uses: measure the round trip and the offset, and correct for
them.

---

## The four timestamps

Every StratumTap API response carries two server timestamps, captured by ASGI middleware as
early and as late in request handling as possible. Together with the two the browser records,
that gives four:

| | Taken by | When |
|---|---|---|
| **t0** | Browser | Just before `fetch()` is called |
| **t1** | Server | As soon as the request arrives (`server.t_recv`) |
| **t2** | Server | Just before the response goes out (`server.t_send`) |
| **t3** | Browser | The moment the response resolves |

<figure>
<svg viewBox="0 0 720 280" role="img" width="100%"
     aria-labelledby="tl-title tl-desc" style="max-width:720px">
  <title id="tl-title">Four-timestamp exchange between browser and server</title>
  <desc id="tl-desc">A browser timeline on top and a server timeline below. A request leaves
  the browser at t0 and arrives at the server at t1. The server does its work, then sends the
  response at t2, which arrives back at the browser at t3. The gap between t1 and t2 is server
  processing time; the two diagonal arrows are network time.</desc>
  <g fill="none" stroke="currentColor" stroke-width="1.5">
    <line x1="60" y1="70" x2="680" y2="70"/>
    <line x1="60" y1="210" x2="680" y2="210"/>
  </g>
  <g fill="currentColor" font-family="system-ui, sans-serif" font-size="13">
    <text x="60" y="52" font-weight="600">Browser</text>
    <text x="60" y="242" font-weight="600">Server</text>
    <text x="120" y="42" text-anchor="middle" font-weight="700">t0</text>
    <text x="330" y="248" text-anchor="middle" font-weight="700">t1</text>
    <text x="430" y="248" text-anchor="middle" font-weight="700">t2</text>
    <text x="620" y="42" text-anchor="middle" font-weight="700">t3</text>
    <text x="205" y="132" font-size="12" font-style="italic">request</text>
    <text x="505" y="132" font-size="12" font-style="italic" text-anchor="end">response</text>
    <text x="380" y="196" font-size="12" text-anchor="middle">t2 − t1</text>
    <text x="380" y="272" font-size="11.5" text-anchor="middle" opacity="0.75">server work — excluded from the delay</text>
    <text x="370" y="20" font-size="12" text-anchor="middle">t3 − t0 &nbsp;(everything)</text>
  </g>
  <defs>
    <marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
            orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"/>
    </marker>
  </defs>
  <g stroke="currentColor" stroke-width="2" marker-end="url(#ah)">
    <line x1="120" y1="70" x2="326" y2="206"/>
    <line x1="434" y1="210" x2="616" y2="74"/>
  </g>
  <g fill="currentColor">
    <circle cx="120" cy="70" r="4"/><circle cx="620" cy="70" r="4"/>
    <circle cx="330" cy="210" r="4"/><circle cx="430" cy="210" r="4"/>
  </g>
  <g stroke="currentColor" stroke-width="1" stroke-dasharray="3 3" opacity="0.55">
    <line x1="330" y1="180" x2="330" y2="210"/>
    <line x1="430" y1="180" x2="430" y2="210"/>
    <line x1="120" y1="28" x2="120" y2="66"/>
    <line x1="620" y1="28" x2="620" y2="66"/>
  </g>
</svg>
</figure>

---

## The arithmetic

Two formulas, straight out of the NTP specification:

```
round-trip delay = (t3 − t0) − (t2 − t1)
clock offset     = ((t1 − t0) + (t2 − t3)) / 2      // server − browser
```

The delay is the total elapsed time **minus the time the server spent working on the
request**. That subtraction is the whole reason `t_recv` and `t_send` exist: without them a
slow handler would look like a slow network, and every correction would be wrong.

The offset averages the two one-way discrepancies. Going out, the server's clock appeared to
be `t1 − t0` ahead of yours; coming back, it appeared to be `t2 − t3` ahead. If the path were
perfectly symmetric, the transit time would cancel exactly and the average would be the true
offset.

### A worked example

Say the wall clocks read like this, in milliseconds past some second:

| | Value |
|---|---|
| t0 (browser sends) | `…000.0` |
| t1 (server receives) | `…001.4` |
| t2 (server sends) | `…001.9` |
| t3 (browser receives) | `…003.2` |

```
delay  = (3.2 − 0.0) − (1.9 − 1.4)
       = 3.2 − 0.5
       = 2.7 ms                       ← time actually spent on the wire

offset = ((1.4 − 0.0) + (1.9 − 3.2)) / 2
       = (1.4 + (−1.3)) / 2
       = 0.05 ms                      ← the server is 0.05 ms ahead of the browser
```

The request took 3.2 ms end to end, but 0.5 ms of that was the server building the response.
Only 2.7 ms was network. And the two clocks are within 50 µs of each other — much closer than
the round trip, which is exactly what the averaging buys you.

Note how *little* the offset is compared to the delay. That is the normal case on a LAN, and
it is why the readout shows both: a 0.05 ms offset with a 2.7 ms RTT is a good measurement; a
0.05 ms offset with a 400 ms RTT would be a coincidence.

---

## Why the lowest-delay sample wins

One exchange is a sample, not a measurement. Queueing in a switch, a busy Wi-Fi channel,
another tab hogging the browser's event loop — any of these adds delay to *one* direction and
biases that sample's offset.

The observation NTP is built on: **the least-delayed sample is the least distorted.** Extra
delay can only be added, never subtracted, so the exchange that completed fastest is the one
whose path was closest to unloaded and closest to symmetric.

StratumTap keeps a ring of the **last 8 exchanges** and reports the offset from the one with
the smallest delay — this is NTP's "clock filter", in about ten lines of JavaScript. Every API
call feeds the ring, not just the dedicated `/api/v1/time` endpoint, so at the default 2 s
refresh the estimator has a fresh 16-second window at all times.

It also tracks the spread of the offsets in the ring as a rough confidence indicator: a ring
whose offsets all agree is a ring you can trust.

---

## The symmetric-path assumption

The offset formula assumes the outbound and return trips take the same time. When they do not,
the error is bounded:

> If the true one-way times differ, the computed offset is wrong by at most **±delay/2**.

That is why the delay is displayed right next to the offset. An offset of +2 ms with an RTT of
3 ms is meaningful — the error cannot exceed 1.5 ms. An offset of +2 ms with an RTT of 400 ms
tells you almost nothing.

There is no way to detect asymmetry from a single endpoint; NTP has the same limitation. The
honest response is to show the bound and let you judge, which is what StratumTap does.

---

## What "corrected" and "as received" actually do

The badge next to the clock names the mode, and the toggle is **Correct for network delay** in
the header.

**corrected** — `Date.now() + offset`
: Your browser's clock, shifted by the measured offset. It is a genuinely live clock: it stays
  right between polls, it does not depend on a timer firing on schedule, and the network delay
  has been removed from it rather than baked into it. This is the default.

**as received** — `t2 + (now − t3)`
: The server's timestamp exactly as it arrived, ticking forward on your local clock. No
  correction at all. It lags true server time by roughly the one-way return delay, and it
  drifts at whatever rate your local clock drifts.

Switching between the two is a good way to *see* the correction: on a LAN they differ by a
fraction of a millisecond, over a VPN the jump is obvious.

{: .note }
> Both modes use your browser's clock as the ticking mechanism, because it is the only clock
> the browser has. The difference is whether the measured offset is applied.

---

## Two implementation details you might notice

**Tiny negative delays get clamped.** `Date.now()` returns whole milliseconds — browsers
deliberately coarsen it as a side-channel defense — while the server stamps sub-millisecond
floats. On a fast LAN this rounding can make `(t3 − t0) − (t2 − t1)` come out slightly
negative, which is physically impossible. StratumTap treats delays between −5 ms and 0 as
zero, and rejects anything below −5 ms or above 60 s as a genuine clock jump or a stalled tab
rather than a measurement.

**A response whose `t2` precedes its `t1` is discarded.** That means the server's clock moved
backwards mid-request — a step correction, most likely. The sample is thrown away rather than
poisoning the ring.

---

## Reading the sync line

> Browser clock is +2.7 ms vs server · RTT 3.2 ms · 8 samples

| Part | Meaning |
|---|---|
| **Browser clock is +2.7 ms vs server** | *Your* clock is 2.7 ms **ahead of** the server's. Negative would mean behind. Note the direction: this is browser−server, the sign that is useful when you are judging your own workstation. |
| **RTT 3.2 ms** | Round-trip delay of the best sample, server processing time already removed. The offset above could be off by up to ±1.6 ms from path asymmetry. |
| **8 samples** | The ring is full. Fewer means StratumTap has not been open long. |

Before the first usable sample it says *measuring clock offset…*, and if the exchanges are all
being rejected, *clock offset not yet usable*.

---

## Using it to check your workstation's clock

This is the practical payoff. Your NTP server is disciplined to GPS and, per the dashboard,
within nanoseconds of true time. So the number in the sync line is, to a good approximation,
**your own machine's clock error**.

- **A few milliseconds, either way, on a LAN.** Normal. That is what NTP over a network gets
  you.
- **Tens or hundreds of milliseconds.** Your workstation's time service is struggling or not
  running. On Linux, `timedatectl status` and `chronyc tracking`; on macOS,
  `sudo sntp -sS time.apple.com`; on Windows, `w32tm /query /status`.
- **Seconds.** Something is properly broken — a VM resumed from a snapshot, a dead RTC
  battery, or no time synchronization at all.
- **A large offset but also a large RTT.** Suspect the path before you suspect the clock. Try
  again from a machine on the same LAN as the server.

Watch it for a minute rather than trusting one reading: the ring needs a few samples, and a
Wi-Fi link can produce one unlucky exchange.

---

## Limitations

Worth being clear about, because the whole point of this feature is honesty about precision.

**Browser clock drift between polls.** In *corrected* mode the display is your clock plus a
constant. Between polls, it drifts exactly as fast as your clock does. At 2 s refresh this is
irrelevant; with refresh **Off** for an hour, it is not. Pausing polling freezes the
correction, not the clock.

**Asymmetric paths.** Wi-Fi, powerline, mobile networks and anything with asymmetric
uplink/downlink bandwidth routinely take longer in one direction than the other. The
±delay/2 bound still holds, but the offset within it is biased. A wired connection gives
much better numbers.

**Proxies and HTTPS terminators.** Delay added *inside* StratumTap is excluded by the `t1`/`t2`
pair. Delay added by a reverse proxy, a TLS terminator, or a load balancer is **not** — from
the browser's point of view, that is network. A proxy that buffers, or that adds asymmetric
latency, inflates the RTT and can bias the offset. If you care about the measurement, take it
directly against StratumTap's own port.

**Browser timer coarsening.** `Date.now()` is whole milliseconds, and can be coarsened further
by cross-origin isolation policies. Sub-millisecond claims from a browser are not credible, and
StratumTap does not make any — the numbers it shows to sub-millisecond precision are the
*server's*, measured server-side.

**Background tabs.** Browsers throttle timers in hidden tabs. With **Pause when hidden** on
(the default), polling stops entirely and resumes when you return; the first reading after you
come back is briefly based on a stale ring.

{: .note }
> None of this affects the *server's* numbers. The chrony offset, the PPS offset and the
> history are all measured on the server with the server's clock. The four-timestamp exchange
> only concerns how faithfully a browser can display a remote clock.

---

## See also

- [What every number means](measurements.md) — including why the server's own offsets are a
  completely separate thing from this one
- [Dashboard: Server time](../user-guide/dashboard.md#server-time) — the card this article
  describes
- [The API `server` block](../api.md#the-server-block) — the timestamps, if you want to
  implement this yourself
