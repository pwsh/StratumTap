---
title: Technical
nav_order: 6
has_children: true
---

# Technical articles

Longer pieces for readers who want to know how something works, or why a number is what it is.
None of this is required reading to use StratumTap.

- **[How the browser time correction works](time-correction.md)** — the four-timestamp
  exchange, the arithmetic, a worked example, and the honest limitations. Start here if you
  have ever wondered whether a clock on a web page can mean anything.
- **[What every number means](measurements.md)** — a field-by-field tour of the chrony and
  gpsd values, including why StratumTap shows three different "time offsets" that disagree by
  a factor of a million.
- **[Streaming design](streaming.md)** — why Server-Sent Events, how a stalled browser is
  prevented from starving the data collectors, and the load-test numbers from a Raspberry Pi.
- **[Architecture](architecture.md)** — components, data flow, why the serial port is never
  opened, deployment layout and the hardening summary.
- **[Receivers and chrony variants](receivers.md)** — what changes with a different GPS
  receiver, what "not supported" actually means, and where the extension points are.
