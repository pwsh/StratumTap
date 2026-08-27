---
title: Configuration
nav_order: 4
---

# Configuration
{: .no_toc }

Every setting is an environment variable prefixed `STRATUMTAP_`. There is no configuration
file format to learn and nothing is configurable from the browser.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

---

## Where settings live

**Under systemd** (the normal case): `/etc/default/stratumtap`. The installer creates it from
`deploy/stratumtap.env.example` the first time and **never overwrites it**, so your
configuration survives upgrades.

```sh
sudo nano /etc/default/stratumtap
sudo systemctl restart stratumtap
```

{: .warning }
> That file is parsed by systemd, not by a shell. Use plain `KEY=value` lines only — **no**
> `export`, **no** shell expansion (`$OTHER`), **no** quotes-with-meaning, and **no** inline
> comments after a value. A `#` comment must be on its own line. Getting this wrong is the
> most common reason the service will not start.

**Running by hand:** export them, or put them on the command line.

```sh
STRATUMTAP_DEMO=1 STRATUMTAP_PORT=9000 python3 -m stratumtap
```

Unknown `STRATUMTAP_*` variables are ignored, and every setting is optional.

---

## Full variable reference

### Web server

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_HOST` | `0.0.0.0` | Bind address. Use `127.0.0.1` behind a reverse proxy. |
| `STRATUMTAP_PORT` | `8080` | TCP port. See [Changing the port](#changing-the-port). |

### gpsd

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_GPSD_HOST` | `127.0.0.1` | gpsd host. Can point at another machine's gpsd if that gpsd listens on the network. |
| `STRATUMTAP_GPSD_PORT` | `2947` | gpsd port. |

### chrony

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_CHRONYC_BIN` | `chronyc` | The `chronyc` executable — a name on `PATH` or an absolute path. |
| `STRATUMTAP_CHRONY_POLL_S` | `1.0` | How often `chronyc tracking` is polled, in seconds. |
| `STRATUMTAP_SOURCES_POLL_S` | `10.0` | How often `chronyc sources` / `sourcestats` are polled. |

### Server-side history

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_HISTORY_INTERVAL_S` | `5.0` | History sampling interval, in seconds. |
| `STRATUMTAP_HISTORY_SIZE` | `17280` | Ring-buffer size in samples. The defaults give 24 h (17 280 × 5 s) for a few megabytes of RAM. |

Depth in hours is `HISTORY_SIZE × HISTORY_INTERVAL_S / 3600`. Raising either raises memory
use roughly linearly.

### Streaming

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_STREAM_MAX_CLIENTS` | `16` | Maximum concurrent `/api/v1/stream` subscribers. Beyond that the endpoint answers HTTP 503. |
| `STRATUMTAP_STREAM_QUEUE` | `500` | Per-subscriber event queue. When it is full the **oldest** event is dropped. |
| `STRATUMTAP_NMEA_RING` | `1000` | Raw NMEA sentences kept for `/api/v1/raw/nmea`. |

Why these exist and what they guarantee: [Streaming design](technical/streaming.md).

### MQTT / Home Assistant

Everything here is inert until `STRATUMTAP_MQTT_URL` is set. See
[Home Assistant](integrations/home-assistant.md) for what the entities look like.

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_MQTT_URL` | *(empty)* | Broker URL: `mqtt://host`, `mqtt://user:pass@host:1883` or `mqtts://host:8883`. Empty disables the publisher entirely — no connection is attempted. |
| `STRATUMTAP_MQTT_TOPIC_PREFIX` | `stratumtap` | Root of the data topics: `<prefix>/<device_id>/{state,position,status}`. |
| `STRATUMTAP_MQTT_DISCOVERY_PREFIX` | `homeassistant` | Home Assistant's discovery prefix. Change it only if you changed it in HA. |
| `STRATUMTAP_MQTT_DEVICE_ID` | *(derived)* | Distinguishes two StratumTaps on one broker. The default is the first 12 hex digits of `sha256(/etc/machine-id)`, or of the hostname when that file is unreadable — stable across restarts, unique per machine. |
| `STRATUMTAP_MQTT_CLIENT_ID` | *(derived)* | MQTT client identifier. Default `stratumtap-<device_id>`. Two clients sharing an id will kick each other off the broker. |
| `STRATUMTAP_MQTT_INTERVAL_S` | `60.0` | **Floor.** Publish at least this often, even when nothing changed. |
| `STRATUMTAP_MQTT_MIN_INTERVAL_S` | `5.0` | **Ceiling.** Never publish more often than this, however fast things change. |
| `STRATUMTAP_MQTT_DEADBAND_OFFSET_US` | `50.0` | A system-offset move larger than this (µs) counts as a change worth publishing. |
| `STRATUMTAP_MQTT_DEADBAND_PPM` | `0.5` | The same for the clock frequency, in ppm. |
| `STRATUMTAP_MQTT_EXPIRE_AFTER_S` | `180` | `expire_after` on every entity: HA marks them unavailable this long after the last message. Keep it at three or more times the floor interval. |
| `STRATUMTAP_MQTT_RETAIN_STATE` | `true` | Retain the state message so a restarting Home Assistant has values immediately instead of waiting up to a minute. |
| `STRATUMTAP_MQTT_TLS_INSECURE` | `false` | For `mqtts://` only: skip certificate and hostname verification. Needed for a self-signed broker; it is a real downgrade, so prefer trusting the CA. |
| `STRATUMTAP_MQTT_QOS` | `0` | QoS for the state, position and availability publishes. Discovery is always QoS 1 and retained. |

The publisher is one more background task next to the chrony poller and the gpsd reader. It
only *reads* the in-memory snapshots, so a slow or unreachable broker can never delay data
collection — it retries with backoff and reports itself in
[`/api/v1/health`](api.md) as `mqtt.connected: false`.

### UI defaults

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_DEFAULT_REFRESH_S` | `2` | Auto-refresh interval offered to a browser that has not chosen one. |
| `STRATUMTAP_REFRESH_CHOICES_S` | `1,2,5,10,30,60` | Comma-separated intervals offered in the refresh dropdown. |
| `STRATUMTAP_HOSTNAME` | *(empty)* | Display name shown in the header, the page title and `server.hostname`. Empty means use the machine's real hostname. |

`STRATUMTAP_HOSTNAME` is useful when the machine's hostname is ugly, or when you would rather
not publish it — set `STRATUMTAP_HOSTNAME=stratum1` and that is what everyone sees.

### Demo mode

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_DEMO` | `false` | Serve plausible synthetic data; no gpsd or chrony required. |
| `STRATUMTAP_DEMO_LAT` | `51.4779` | Demo latitude (Royal Observatory, Greenwich). |
| `STRATUMTAP_DEMO_LON` | `-0.0015` | Demo longitude. |

Demo mode synthesizes the whole surface, including valid-checksum NMEA sentences on the live
stream, so you can exercise every feature without a receiver. The header shows *demo data* in
its tooltip and `server.demo` is `true` in every API response.

### Map tiles

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_TILE_URL` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | Tile URL template. |
| `STRATUMTAP_TILE_ATTRIBUTION` | `© OpenStreetMap contributors` | Attribution shown on the map. |

### Miscellaneous

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_CORS_ORIGINS` | *(empty)* | Comma-separated origins allowed to call the API cross-origin. Empty disables CORS entirely. |
| `STRATUMTAP_LOG_LEVEL` | `info` | `critical`, `error`, `warning`, `info`, `debug` or `trace`. |

---

## Changing the port

Set it in the environment file and restart:

```sh
echo 'STRATUMTAP_PORT=9000' | sudo tee -a /etc/default/stratumtap
sudo systemctl restart stratumtap
```

### Port 80 or any port below 1024

The systemd unit runs with an empty capability set, so binding a privileged port fails until
you give the capability back. Edit `/etc/systemd/system/stratumtap.service` and uncomment
**both** lines:

```ini
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
```

Then:

```sh
sudo systemctl daemon-reload
sudo systemctl restart stratumtap
```

{: .note }
> Putting nginx or Caddy in front and binding `STRATUMTAP_HOST=127.0.0.1` is the tidier
> option, and gives you TLS and authentication at the same time.

{: .warning }
> Uncommenting those lines edits a file the installer overwrites on every upgrade. Re-apply
> the change after deploying, or use a systemd drop-in
> (`/etc/systemd/system/stratumtap.service.d/port80.conf`) which upgrades leave alone.

---

## Reverse proxy

Bind to loopback and let the proxy handle the outside world:

```sh
# /etc/default/stratumtap
STRATUMTAP_HOST=127.0.0.1
STRATUMTAP_PORT=8080
```

### nginx

{: .warning }
> **Server-Sent Events need `proxy_buffering off`.** Without it nginx buffers the stream and
> the Live raw panel sits there showing nothing. StratumTap already sends
> `X-Accel-Buffering: no`, which nginx honors, but setting it explicitly is safer and costs
> nothing.

```nginx
server {
    listen 443 ssl;
    server_name stratum1.example.org;

    # ... your ssl_certificate lines ...

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # required for /api/v1/stream (SSE)
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 3600s;

        # optional: password-protect the whole thing
        # auth_basic           "StratumTap";
        # auth_basic_user_file /etc/nginx/.htpasswd;
    }
}
```

### Caddy

Caddy streams by default and needs no special configuration:

```caddyfile
stratum1.example.org {
    reverse_proxy 127.0.0.1:8080
    # basicauth { alice $2a$14$... }
}
```

### Apache

Use `mod_proxy_http` with `SetEnv proxy-sendchunked` and no output buffering; disable
`mod_deflate` for `text/event-stream`.

{: .note }
> A proxy adds delay on the *network* side of the four-timestamp exchange, so it inflates the
> measured RTT and can bias the browser clock offset if the two directions are asymmetric.
> Time spent inside StratumTap is excluded either way.
> [Why](technical/time-correction.md#limitations).

---

## Map tiles: offline or local

Only the map *tiles* need the internet. Leaflet itself is vendored into the application.

**A blocked or offline network** is handled gracefully: the map says so once, and the marker,
the accuracy circles and the recorded track still draw on a blank background. The position
readout and every number are unaffected.

**A local tile server** gives you a real map with no internet at all. Point the template at
it:

```sh
# /etc/default/stratumtap
STRATUMTAP_TILE_URL=http://192.0.2.20:8080/tile/{z}/{x}/{y}.png
STRATUMTAP_TILE_ATTRIBUTION=Local tiles
```

Anything that serves `{z}/{x}/{y}` raster tiles works — a `tileserver-gl` container, an
`openstreetmap-tile-server` container, a directory of tiles served by nginx.

{: .note }
> If StratumTap is served over HTTPS, the tile URL must be HTTPS too. Browsers block mixed
> content, and the map will be blank with a console error rather than a tile error.

---

## CORS

By default CORS is **disabled**: browsers on other origins cannot call the API. That is right
for the normal case, where the page and the API come from the same origin.

Enable it only if you are building your own front end or dashboard on another origin:

```sh
# /etc/default/stratumtap
STRATUMTAP_CORS_ORIGINS=https://dash.example.org,http://192.0.2.10:3000
```

Comma-separated, exact origins (scheme, host and port). There is no wildcard shortcut, on
purpose.

{: .note }
> CORS does not affect `curl`, scripts, Home Assistant or anything else that is not a browser
> — those are unaffected by the same-origin policy and can always call the API.

---

## Logging

The service logs to the journal under the identifier `stratumtap`.

```sh
sudo journalctl -u stratumtap -f            # follow
sudo journalctl -u stratumtap -n 100        # the last 100 lines
sudo journalctl -u stratumtap --since today
```

Raise the level while debugging:

```sh
# /etc/default/stratumtap
STRATUMTAP_LOG_LEVEL=debug
```

Levels: `critical`, `error`, `warning`, `info` (default), `debug`, `trace`.

{: .note }
> Collector problems are logged at WARNING **only when the state changes** — once when
> `chronyc` starts failing and once when it recovers. A permanently broken collector does not
> fill your journal with one line per second.

---

## Hardening

The installed unit already runs as an unprivileged `stratumtap` user with
`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `PrivateDevices`, `ProtectClock`,
`NoNewPrivileges`, an empty `CapabilityBoundingSet`, `MemoryDenyWriteExecute`,
`RestrictNamespaces`, `LockPersonality` and friends. The service only reads: it runs `chronyc`
and opens a TCP connection to gpsd, and it writes no files anywhere.

{: .warning }
> If you tighten it further, **keep `AF_INET`, `AF_INET6` and `AF_UNIX` in
> `RestrictAddressFamilies=` and do not add `IPAddressDeny=`.** Either change cuts off both
> collectors — `chronyc` talks to `chronyd` over UDP 323 on loopback or an `AF_UNIX` socket,
> and gpsd is a TCP connection to 127.0.0.1.

`SupplementaryGroups=_chrony` lets `chronyc` use `/run/chrony/chronyd.sock` where the local
configuration permits it. It is harmless otherwise. If the group does not exist on your
system, the installer removes the line — systemd refuses to start a unit that references a
missing group.

---

## Firewall

There is no authentication. Open the port deliberately, and only to networks you trust:

```sh
sudo ufw allow from 192.0.2.0/24 to any port 8080 proto tcp
```

{: .warning }
> Do not expose StratumTap to the internet as it is. Put it behind a reverse proxy with TLS
> and authentication, or reach it over a VPN.
