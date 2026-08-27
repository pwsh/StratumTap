---
title: Receivers and chrony variants
parent: Technical
nav_order: 5
---

# Receivers and chrony variants
{: .no_toc }

What StratumTap needs from your hardware, what changes when the hardware changes, and what is
genuinely not supported.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

---

## The compatibility rule

**If gpsd supports your receiver, StratumTap does.**

StratumTap never talks to the receiver. It reads gpsd's JSON socket, which is a
device-independent abstraction — the same one `cgps`, `gpsmon` and `xgps` use. There is no
device list to maintain here, no driver, no baud rate, no protocol quirks. gpsd's own
[hardware compatibility list](https://gpsd.gitlab.io/gpsd/hardware.html) is the authority.

That covers essentially everything: u-blox (all generations), MediaTek/MTK, SiRF, Garmin,
Trimble, NavSpark, generic NMEA-0183 serial and USB receivers, network sources, and gpsd's own
PPS handling.

The corollary: if `gpspipe -w -n 5` prints JSON, StratumTap will show it. If it does not, the
problem is between gpsd and the receiver, and StratumTap cannot help — but neither can it make
things worse.

---

## What changes with a different receiver

Every field in the API is `null` when the receiver does not report it, and every `null` shows
as an em dash (—) in the UI. **A dash means "your receiver does not send this", not "something
is broken".** No card disappears and nothing errors.

### Missing fields

Common differences you will actually notice:

| If the receiver omits… | You lose |
|---|---|
| `GST` | The violet error ellipse on the map and the whole *GST error statistics* block. Everything else is unaffected. |
| `epx`/`epy`/`epv`/`eph`/`sep` | The corresponding tiles, and the map's accuracy circles. DOPs usually remain. |
| `altMSL` or `geoidSep` | The MSL altitude tile; HAE remains, since that is what GNSS solves for. |
| `magtrack` / `magvar` | The magnetic bearing sub-line under Track. |
| `leapseconds` | The Leap seconds tile. |
| `SKY.time` | Nothing visible — StratumTap falls back to the server's arrival time for that message. |
| `status` | The DGPS/RTK word on the fix pill; the 2D/3D mode still shows. |
| `ecef*` | Nothing in the UI (ECEF is exposed in the API only). |

A perfectly good receiver may report only mode, position, HDOP and satellites. The dashboard
still reads correctly.

### No PPS

Without a PPS signal:

- gpsd sends `TOFF` messages instead of `PPS`. **GPS→system offset** then reads the *serial*
  timing — typically 0.1–1 s of NMEA latency — rather than microseconds. That is expected;
  see [The three time offsets](measurements.md#the-three-time-offsets).
- If gpsd sends neither message, `gps.time_offset` is `null` throughout and the tile reads
  *no PPS/TOFF seen*.
- chrony's own numbers are unaffected in form, but a serial-only refclock gives you
  millisecond-class discipline instead of sub-microsecond. The gauge will settle on a coarser
  scale, which is the honest picture.

StratumTap prefers `PPS` when a recent one exists and falls back to `TOFF`, and the tile always
names the source it used.

{: .note }
> Some gpsd builds emit `PPS` messages to JSON watchers even when PPS is not explicitly
> enabled in the watch, and a Pi with kernel PPS may produce **two** `PPS` messages a second —
> one from `/dev/pps0` and one from the KPPS on the serial device — with identical values.
> Either is fine; StratumTap uses the most recent.

### Multi-constellation and dual-band receivers

Modern receivers track GPS, GLONASS, Galileo, BeiDou, QZSS, NavIC and SBAS at once. All are
supported and color-coded consistently across the sky plot, the satellite bars and the table:
GPS blue, GLONASS red, Galileo green, BeiDou amber, SBAS violet, QZSS aqua, NavIC magenta,
unknown gray.

Two things to expect:

- **Apparent duplicates.** A dual-band receiver reports one entry per *signal*, so the same
  satellite can appear twice with different `Sig` (signal id) values. The sky plot will show
  two markers at the same spot. This is the receiver's own reporting, passed through
  faithfully.
- **Two satellite numbers.** `Sat` is the `svid` — the conventional per-constellation number —
  and `PRN` is gpsd's internal flat numbering. They agree for GPS and diverge elsewhere (SBAS
  133 is PRN 46, for instance). See
  [the satellite table](../user-guide/detail-view.md#satellites-in-view).

Satellites are sorted by constellation then number, and the sky plot legend lists only the
constellations actually in view.

### Multiple devices

gpsd can manage several devices at once. StratumTap:

- reports **all** of them under the map (`devices`), including PPS-only devices such as
  `/dev/pps0`;
- attributes fix data to whichever device produced the last position report, and shows that
  one as the active receiver.

The typical PPS-disciplined Pi shows exactly two: the serial receiver and the PPS device.

### A remote gpsd

Point `STRATUMTAP_GPSD_HOST` at another machine and StratumTap reads that gpsd instead — raw
NMEA stream included. Useful when the receiver lives on a different box from chrony, or when
you want one StratumTap watching a shared gpsd.

The remote gpsd must be listening on the network (`-G`, or `GPSD_OPTIONS="-G"`), which by
default it is not.

{: .warning }
> gpsd has no authentication either. Only do this on a trusted network.

---

## chrony variants

### An NTP-only server (no GPS at all)

StratumTap works. The GPS panels report `gpsd not connected` or "waiting for the first
position report", and everything on the chrony side — offset, frequency, sources, sourcestats,
history, gauge — works normally.

The one thing to know: `/api/v1/health?strict=1` returns **503** when there is no fix, because
`ok` requires `gps_fix`. For a GPS-less host, monitor the plain `/api/v1/health` endpoint and
check `ntp_ok` yourself rather than relying on the strict form.

### chronyc permissions

`chronyc tracking` must work **for an unprivileged user**. Debian's stock `chrony.conf`
already allows this — `chronyc` reaches `chronyd` over UDP 323 on loopback, which is open to
local users by default. Normally there is nothing to do.

Test it as the service user, which is the case that actually matters:

```sh
chronyc tracking                          # as yourself
sudo -u stratumtap chronyc tracking       # as the service user
```

If the second fails and the first succeeds, your `chrony.conf` restricts the command port.

### chrony group naming

Debian and Ubuntu name the group `_chrony`; some other distributions use `chrony`, and some
have no such group. It only matters for the Unix socket at `/run/chrony/chronyd.sock`.

- The installer adds the service user to `_chrony` **if that group exists**, and removes the
  `SupplementaryGroups=_chrony` line from the unit if it does not. systemd refuses to start a
  unit referencing a missing group, so this is not optional bookkeeping.
- On a system that uses `chrony` instead, edit the unit's `SupplementaryGroups=` line
  accordingly — or leave it out entirely and rely on UDP 323, which is what the fallback does.

Either path works. The socket is simply the tidier one where it is available.

### chrony versions

Parsing is written against **chrony 4.x** CSV and text output, and tested against captured
real output. The CSV field order has been stable across 4.x. If both forms are available the
CSV wins; if the CSV fails but the human text parses, the text is used and the panel stays
available — so a format surprise degrades rather than blanking the card.

See [the CSV-versus-text sign flip](measurements.md#the-csv-versus-text-sign-flip) for the one
real subtlety.

---

## Not supported

### ntpd and NTPsec

StratumTap reads `chronyc`. It does **not** support `ntpd`, NTPsec, `openntpd`, or systemd's
`timesyncd`. There is no `ntpq` parser, and the field set StratumTap displays is modeled on
chrony's tracking report rather than on NTP's peer variables.

This is a deliberate scope decision, not an oversight: chrony is what Debian ships, what
Raspberry Pi OS ships, and what a GPS/PPS refclock setup on those platforms normally uses.

**The extension point** if you want it: `stratumtap/chrony.py` isolates all of this behind a
collector plus pure parser functions that produce an `NtpSnapshot`. An `ntpq -c rv`-based
collector producing the same model would slot into the same place, and nothing above it — the
API, the UI, the history, the gauge — would need to change. The parsers are pure functions
precisely so they can be tested against captured output.

### Anything that requires writing

StratumTap never writes: no configuration changes, no clock steering, no commands to the
receiver, no `chronyc makestep`, no `gpsctl`. It is a viewer. If you want a control panel, that
is a different program with a very different security model.

### Authentication and multi-tenancy

There is no login, no user model and no per-user configuration. Browser settings live in
`localStorage`. Put a reverse proxy in front if you need access control — see
[Configuration](../configuration.md#reverse-proxy).

---

## See also

- [What every number means](measurements.md) — which fields your receiver populates and why
- [Troubleshooting](../troubleshooting.md) — when the GPS panel is empty
- [Configuration](../configuration.md#gpsd) — pointing at a different gpsd
