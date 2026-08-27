---
title: Troubleshooting
nav_order: 8
---

# Troubleshooting
{: .no_toc }

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

---

## Not running after a reboot or OS upgrade

**Symptom.** The page does not load after the server was rebooted. `systemctl status stratumtap`
shows `activating (auto-restart)` with a growing restart counter, and
`journalctl -u stratumtap -b` repeats:

```
ModuleNotFoundError: No module named 'uvicorn'
```

**Cause.** StratumTap runs from its own Python virtual environment in `/opt/stratumtap/venv`. A
venv is bound to one interpreter version: its `bin/python` is a symlink to the system
`python3`, but its packages live in `lib/python3.X/site-packages` for the version it was
created with. If the operating system is upgraded to a release with a newer Python (for
example Debian 12 → 13 moves `python3` from 3.11 to 3.13), the symlink now starts the new
interpreter, which cannot see the old package directory — so the very first import fails.
Nothing about the service configuration is wrong; systemd keeps retrying exactly as it should,
and the service comes back the moment the venv is fixed.

**Fix.** Re-run the installer; it detects the interpreter mismatch and rebuilds the venv:

```sh
# from your workstation
TARGET_HOST=stratum1.local TARGET_USER=pi bash deploy/deploy.sh
# or, on the server itself, from the source tree
sudo bash deploy/install.sh
```

Or by hand on the server:

```sh
sudo rm -rf /opt/stratumtap/venv
sudo python3 -m venv /opt/stratumtap/venv
sudo /opt/stratumtap/venv/bin/pip install -r /opt/stratumtap/app/requirements.txt
sudo systemctl restart stratumtap
```

{: .note }
> After a major OS upgrade also check that `chrony` and `gpsd` still run and that the
> `python3-venv` package is installed. StratumTap is tested on Debian 12 (Python 3.11,
> chrony 4.3, gpsd 3.22) and Debian 13 (Python 3.13, chrony 4.6, gpsd 3.25).

See [Keeping the service running](getting-started.md#keeping-the-service-running) for how the
service is configured to start at boot and recover from failures.

## Quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| Red **NTP unavailable** pill | `chronyc` cannot be run or cannot reach `chronyd` | [NTP unavailable](#ntp-unavailable) |
| Red **GPS** pill, "gpsd not connected" | gpsd not running, or not listening on 2947 | [GPS unavailable](#gps-unavailable) |
| "waiting for the first position report" | gpsd is up, the receiver has not delivered a fix yet | [No fix](#no-fix) |
| Fix pill reads **NO FIX** | Antenna, sky view, or cold start | [No fix](#no-fix) |
| **GPS→system offset** shows "no PPS/TOFF" | The receiver has no PPS and gpsd sends neither message | [PPS offset missing](#pps-offset-missing) |
| Map is blank or gray | Tile fetches failing | [Map blank](#map-blank) |
| Browser clock offset looks huge | Your workstation's clock, or an asymmetric path | [Browser clock offset](#browser-clock-offset-looks-huge) |
| Live raw says **refused** | Stream client cap reached, or a buffering proxy | [Stream refused](#stream-says-server-refused) |
| Service will not start | Port in use, env-file syntax, missing group | [Service will not start](#service-will-not-start) |
| Page looks unchanged after upgrade | Browser cache | [After upgrading](#after-upgrading-the-page-looks-old) |
| Values show — everywhere | The receiver does not report those fields | [Receivers](technical/receivers.md#missing-fields) |

Whatever the symptom, start with the log:

```sh
sudo journalctl -u stratumtap -n 50 --no-pager
```

Collector problems are logged **once when they start and once when they clear**, so a
permanently broken collector does not bury the interesting lines.

---

## NTP unavailable

The pill is red and the Time sync card shows a banner with chrony's own message.

### `506 Cannot talk to daemon`

`chronyd` is not running, or is not listening where `chronyc` expects.

```sh
systemctl status chrony        # Debian's unit is called "chrony"
sudo systemctl start chrony
```

If chronyd *is* running, the command port may be restricted. Debian's stock `chrony.conf`
allows local users on UDP 323; check for `cmdport 0` or a restrictive `bindcmdaddress` in
`/etc/chrony/chrony.conf`.

### It works for me but not for the service

This is the case that matters — reproduce it as the service user:

```sh
chronyc tracking                          # as yourself
sudo -u stratumtap chronyc tracking       # as the service user
```

If the second fails and the first works:

- Your `chrony.conf` restricts the command port to specific users or addresses.
- Or the unit's `SupplementaryGroups=_chrony` was removed and the socket path is the only one
  permitted. Add it back (see [Receivers](technical/receivers.md#chrony-group-naming)).

### `chronyc not found`

The binary is not on the service's `PATH`.

```sh
which chronyc                             # e.g. /usr/bin/chronyc
# then, in /etc/default/stratumtap:
STRATUMTAP_CHRONYC_BIN=/usr/bin/chronyc
sudo systemctl restart stratumtap
```

### chrony is not installed

The installer warns about this and carries on, deliberately. Install it yourself:

```sh
sudo apt install chrony
```

{: .warning }
> Configuring chrony as a GPS-disciplined time server is outside StratumTap's scope, and
> StratumTap will never touch your `chrony.conf`. If you are starting from scratch, get
> `chronyc tracking` showing a sane stratum first, then come back.

---

## GPS unavailable

### gpsd not running

```sh
systemctl status gpsd
ss -ltnp | grep 2947          # is anything listening?
gpspipe -w -n 5               # should print VERSION, DEVICES, TPV, SKY...
```

If `gpspipe` prints JSON, gpsd is fine and the problem is between gpsd and StratumTap — check
`STRATUMTAP_GPSD_HOST` and `STRATUMTAP_GPSD_PORT`.

If `gpspipe` prints nothing, the problem is between gpsd and the receiver. StratumTap cannot
help, and neither can it make things worse.

### Socket-activated gpsd

Debian ships gpsd socket-activated, which means `gpsd.service` can look inactive while
everything is actually fine — until nothing has connected yet.

```sh
sudo systemctl enable --now gpsd.socket
sudo systemctl status gpsd.socket
```

If you want gpsd permanently running rather than started on demand:

```sh
sudo systemctl enable --now gpsd.service
```

### Wrong device

```sh
cat /etc/default/gpsd          # DEVICES="..."
ls -l /dev/ttyAMA0 /dev/ttyUSB0 /dev/serial0 /dev/pps0 2>/dev/null
```

On a Raspberry Pi the serial GPIO port is usually `/dev/ttyAMA0` or `/dev/serial0`, and PPS
appears as `/dev/pps0` once `pps-gpio` is loaded. Restart gpsd after changing `DEVICES`.

### "no GPS device" / "waiting for the first report"

These come from StratumTap itself and mean the TCP session to gpsd is **up** — the connection
is fine, gpsd just has nothing to tell us yet.

- **"no GPS device"** — gpsd is running but has no device attached. A gpsd with no receiver is
  legitimately quiet; StratumTap probes it periodically rather than churning reconnects.
- **"waiting for the first position report"** — gpsd has the device but no `TPV` has arrived
  yet. Normal for the first seconds after a start, and normal indefinitely if the receiver has
  no sky view.

---

## No fix

Not an application problem. `cgps -s` or `gpsmon` will show exactly the same thing.

- **Antenna and sky view.** A GPS antenna indoors, on a windowsill or under a metal roof may
  never get a fix. It needs to see sky.
- **Cold start.** A receiver with no almanac and no backup battery can take 15 minutes on
  first fix, and it will look completely dead for most of that time. Watch the satellite bars:
  signals appearing at all means the antenna is working.
- **Backup battery.** A receiver that cold-starts on every power cycle usually has a dead
  backup cell.
- **Check the satellites card.** Signals present but nothing *used* means it is tracking and
  has not solved yet. No signals at all points at antenna or cabling.

The [sky plot](user-guide/detail-view.md#sky-plot) is useful here: satellites all clustered on
one side is a blocked view, and it explains a high HDOP at the same time.

---

## PPS offset missing

The **GPS→system offset** tile reads "no PPS/TOFF seen", or the offset is hundreds of
milliseconds rather than microseconds.

This is a hardware and gpsd question, not a StratumTap one.

- **No PPS wired.** Without a PPS signal gpsd sends `TOFF` messages, whose offset reflects
  *serial* latency — 0.1 to 1 s is completely normal. See
  [the three time offsets](technical/measurements.md#the-three-time-offsets).
- **PPS present but not visible to gpsd.** Check `/dev/pps0` exists and `ppstest /dev/pps0`
  produces output. On a Pi that means `dtoverlay=pps-gpio,gpiopin=18` (or your pin) in
  `/boot/firmware/config.txt` and a reboot.
- **Neither message arrives.** `gpspipe -w | grep -E 'PPS|TOFF'` will confirm. If gpsd is not
  emitting them, StratumTap has nothing to show.

{: .note }
> A missing PPS offset does **not** mean your time is bad. Check the system clock offset on
> the gauge — that is chrony's verdict, and it is the number that matters.

---

## Map blank

Tile fetches are failing. Everything else — position, accuracy circles, the readout below the
map — still works.

Common causes:

- No internet, or DNS not resolving `tile.openstreetmap.org`.
- A proxy or firewall blocking the tile host.
- **Mixed content**: StratumTap served over HTTPS with an `http://` tile URL. The browser
  blocks it silently; the console will say so.

Fixes:

```sh
# check from the browser's machine, not the server
curl -sI https://tile.openstreetmap.org/0/0/0.png | head -1
```

For a fully offline install, point at a local tile server — see
[Configuration](configuration.md#map-tiles-offline-or-local).

{: .note }
> Leaflet itself is vendored into StratumTap, so a blank map is always a *tile* problem, never
> a missing-library problem. If the map controls and the marker draw, the code loaded fine.

---

## Browser clock offset looks huge

The line next to the clock reads something like "Browser clock is +4.2 s vs server".

**Look at the RTT in the same line first.**

- **Large offset, small RTT** (say +4 s with 3 ms RTT). The measurement is trustworthy, so
  believe it: **your workstation's clock is wrong**. Check it:
  `timedatectl status` on Linux, `w32tm /query /status` on Windows,
  `sudo sntp -sS time.apple.com` on macOS. A VM resumed from a snapshot is a classic cause.
- **Large offset, large RTT** (say +200 ms with 400 ms RTT). Suspect the path. A VPN, an
  HTTPS-terminating proxy, mobile data or Wi-Fi with asymmetric latency biases the offset by
  up to ±RTT/2. Try again from a machine on the same LAN as the server, or against
  StratumTap's own port rather than through a proxy.

Either way this measurement is about *your browser's* clock, not the server's. The server's
own accuracy is on the gauge. See
[the limitations section](technical/time-correction.md#limitations).

If the readout says *clock offset not yet usable*, every exchange is being rejected — usually a
local clock that is stepping while you watch.

---

## Stream says "server refused"

The Live raw panel's status chip reads **refused** and offers a Retry button.

### Client cap reached

`/api/v1/stream` answers `503 {"detail": "too many stream clients"}` beyond
`STRATUMTAP_STREAM_MAX_CLIENTS` (16 by default). Check what is connected:

```sh
curl -s http://stratum1.local:8080/api/v1/health | python3 -m json.tool | grep stream_clients
```

Close a stream in another tab or on another machine and press Retry. If you genuinely need
more, raise the cap in `/etc/default/stratumtap` — memory use is bounded by
`max_clients × queue_size`.

Note that a forgotten browser tab holds a slot. **Pause when hidden** (the default) closes the
stream when the tab goes to the background, which prevents most of this.

### A buffering reverse proxy

The stream connects but nothing ever appears. nginx buffers proxied responses by default.

```nginx
location / {
    proxy_pass      http://127.0.0.1:8080;
    proxy_buffering off;          # required for SSE
    proxy_cache     off;
    proxy_read_timeout 3600s;
}
```

StratumTap already sends `X-Accel-Buffering: no`, which nginx honors, but setting it
explicitly costs nothing. Caddy streams by default. See
[Configuration](configuration.md#reverse-proxy).

### Other causes

- A browser with no `EventSource` support (the panel says so directly).
- A corporate proxy that will not hold a long-lived connection open. The **Snapshot last 200**
  button works over ordinary request/response and is the fallback.

---

## Service will not start

```sh
sudo journalctl -u stratumtap -n 50 --no-pager
```

That almost always says why. The three usual answers:

### Port already in use

```
[Errno 98] Address already in use
```

```sh
sudo ss -ltnp | grep 8080
```

Something else has the port. Change `STRATUMTAP_PORT` in `/etc/default/stratumtap`, or stop
the other service.

For a port below 1024 you also need the capability back — see
[Changing the port](configuration.md#port-80-or-any-port-below-1024).

### A typo in the environment file

`/etc/default/stratumtap` is parsed by **systemd**, not by a shell.

{: .warning }
> No `export`. No shell expansion. No inline comments after a value. A `#` comment must be on
> its own line. `STRATUMTAP_PORT=8080  # web` sets the port to the literal string
> `8080  # web` and the service fails to start.

Check the file:

```sh
sudo systemd-analyze verify /etc/systemd/system/stratumtap.service
grep -vE '^\s*(#|$)' /etc/default/stratumtap
```

### `SupplementaryGroups` names a group that does not exist

```
Failed to determine supplementary groups: No such process
```

systemd refuses to start a unit referencing a missing group. If your system has no `_chrony`
group, remove that line:

```sh
sudo sed -i '/^SupplementaryGroups=_chrony/d' /etc/systemd/system/stratumtap.service
sudo systemctl daemon-reload
sudo systemctl restart stratumtap
```

`chronyc` then reaches `chronyd` over UDP 323 on loopback, which is the default path anyway.

### Something else

Run it in the foreground as the service user and read the traceback:

```sh
sudo -u stratumtap env $(grep -vE '^\s*(#|$)' /etc/default/stratumtap | xargs) \
  /opt/stratumtap/venv/bin/python -m stratumtap
```

---

## After upgrading, the page looks old

The API sets `Cache-Control: no-store`, so data is never stale — but the **static files**
(HTML, JavaScript, CSS) can be held by your browser.

- **Hard refresh:** Ctrl-Shift-R (Cmd-Shift-R on macOS).
- **Or** open developer tools, right-click the reload button and choose *Empty cache and hard
  reload*.
- **Or** check the version: hover the hostname in the header, or
  `curl -s http://stratum1.local:8080/api/v1/config | python3 -m json.tool | grep version`.
  If the server reports the new version and the page still looks old, it is definitely your
  browser.

A reverse proxy with its own cache can do the same thing. Add `proxy_cache off;` while you are
adding `proxy_buffering off;`.

---

## Reading `/api/v1/health`

```sh
curl -s http://stratum1.local:8080/api/v1/health | python3 -m json.tool
```

```json
{
  "ok": false,
  "ntp_ok": true,
  "gpsd_connected": true,
  "gps_fix": false,
  "loop_lag_ms": 2.1,
  "stream_clients": 1,
  "mqtt": {"enabled": false, "connected": false, "publishes": 0, "errors": 0, "last_publish_at": null, "last_reason": null, "last_error": null},
  "server": { "hostname": "stratum1", "version": "0.1.0", "demo": false, "uptime_s": 12345.6 }
}
```

Read it as a checklist, in order:

| Flag | If false |
|---|---|
| `ntp_ok` | chrony is unavailable **or** not synchronized → [NTP unavailable](#ntp-unavailable) |
| `gpsd_connected` | The TCP session to gpsd is down → [GPS unavailable](#gps-unavailable) |
| `gps_fix` | Connected to gpsd, but the fix mode is below 2D → [No fix](#no-fix) |
| `ok` | All three together. |

Two extras that are not pass/fail:

- **`loop_lag_ms`** — worst event-loop scheduling overshoot in the last 60 s. Single digits on
  a healthy Pi. Consistently high means the machine is overloaded, not that StratumTap is
  broken.
- **`stream_clients`** — current SSE subscribers. Useful when the stream is being refused.

For monitoring, use `?strict=1`, which returns **503** whenever `ok` is false:

```sh
curl -fsS 'http://stratum1.local:8080/api/v1/health?strict=1' >/dev/null \
  || echo "StratumTap unhealthy"
```

{: .note }
> `strict=1` goes red when there is no GPS fix. On a GPS-less NTP server that is a permanent
> false alarm — monitor the plain endpoint and check `ntp_ok` yourself instead.

---

## Still stuck?

Collect this and it will usually be obvious:

```sh
sudo journalctl -u stratumtap -n 100 --no-pager
curl -s http://127.0.0.1:8080/api/v1/health | python3 -m json.tool
sudo -u stratumtap chronyc tracking
gpspipe -w -n 5
systemctl --no-pager status stratumtap gpsd chrony
```

{: .warning }
> Before pasting any of that in public, remember it contains your server's **position** and
> hostname. Redact the latitude and longitude.
