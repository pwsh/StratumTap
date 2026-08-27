# StratumTap

A lightweight web front end for a GPS-disciplined NTP server. If you have a Raspberry Pi (or
any Debian box) running `chrony` disciplined by a GPS receiver via `gpsd` and PPS, this
replaces squinting at `cgps -s` and `chronyc tracking` in two SSH windows with a responsive
dashboard, a detail view (map, sky plot, time-accuracy gauge, 24-hour history), and a
versioned JSON API. It is a single Python process with a handful of dependencies, no database and no
frontend build step — it reads gpsd's JSON socket directly and runs `chronyc` for NTP state,
so it never scrapes curses output.

**[Documentation](https://pwsh.github.io/StratumTap/)** — installation, user guide,
API reference and technical articles. (Sources in [`docs/`](docs/).)

## Features

- **Dashboard** — a big server clock, sync/fix status pills, and the numbers that matter:
  stratum, system-clock offset, frequency, root dispersion, fix mode, position, satellites
  used/seen, HDOP, and accuracy estimates.
- **Detail view** — Leaflet map with an accuracy circle, polar sky plot of every satellite in
  view, per-satellite table (constellation, PRN, elevation, azimuth, SNR, used), an
  auto-scaling time-offset gauge, offset/frequency history charts, the `chronyc sources` and
  `sourcestats` tables, and the raw tool output for when you want to see it verbatim.
- **JSON API** — everything the UI shows is available under `/api/v1/`, documented in
  [`docs/api-contract.md`](docs/api-contract.md) with interactive docs at `/docs`.
- **Browser time correction** — every API response carries `server.t_recv` and `server.t_send`,
  so the browser runs the same four-timestamp exchange NTP uses: with the client's send time
  `t0` and receive time `t3` it computes `delay = (t3−t0) − (t2−t1)` and
  `offset = ((t1−t0) + (t2−t3))/2`. It keeps the last eight samples and trusts the one with the
  lowest delay, so the "server time" you read is corrected for network latency rather than
  being however stale your connection happens to be. The offset and delay are both displayed,
  and the correction can be toggled off.
- **Recording and export** — arm the recorder and every poll is appended to an in-memory
  track you can export as JSON, CSV, GPX or GeoJSON. Server-side history covers the last 24
  hours whether or not a browser was open (`/api/v1/history`, JSON or CSV).
- **Live raw stream** — `/api/v1/stream` pushes the raw NMEA sentences, every gpsd JSON
  object, and each chrony update over Server-Sent Events; the Detail view has a "Live raw"
  panel with per-sentence-type rates, checksum checks, and `.nmea` / `.jsonl` capture download.
  `curl -N http://host:8080/api/v1/stream/nmea.txt > capture.nmea` works too. gpsd relays the
  sentences, so the app never opens the serial port itself. Subscribers get bounded queues
  (drop-oldest) and a client cap, so a stalled browser can never slow down data collection —
  `scripts/stream_loadtest.py` demonstrates this against a running instance.
- **Home Assistant over MQTT** — set `STRATUMTAP_MQTT_URL` and StratumTap publishes one
  retained discovery message that creates a device with ~20 entities (offset, stratum, sync,
  fix, satellites, accuracy, position), plus a last-will availability topic so everything goes
  *unavailable* the moment the service dies. It publishes at most once a minute when nothing
  is happening and immediately when the offset, stratum, sync state or fix actually changes.
  [Home Assistant integration](docs/integrations/home-assistant.md)
- **Demo mode** — `STRATUMTAP_DEMO=1` serves plausible synthetic data, so you can develop the UI
  or check an install on a machine with no GPS receiver at all.
- **Offline friendly** — Leaflet is vendored; only the map *tiles* need internet, and
  `STRATUMTAP_TILE_URL` can point at a local tile server. When tiles fail the map degrades to a
  plain position readout.

## Screenshots

Captured in demo mode (`STRATUMTAP_DEMO=1`, synthetic data at Greenwich).

| Dashboard | Detail |
|---|---|
| ![Dashboard](docs/assets/screenshots/dashboard.png) | ![Detail view](docs/assets/screenshots/detail-full.png) |

## Quick start

Requires Python 3.11 or newer (tested on Debian 12 / Python 3.11 and Debian 13 / Python 3.13).

```sh
git clone https://github.com/pwsh/StratumTap.git
cd StratumTap
make demo            # creates ./.venv, installs, runs with synthetic data
```

Then open <http://127.0.0.1:8080/>. No gpsd or chrony needed in demo mode.

To run against the real local time stack instead:

```sh
make run             # talks to gpsd on 127.0.0.1:2947 and runs chronyc
```

For UI work, `make dev` adds auto-reload. To deploy to your NTP server, see
[Deployment](#deployment-to-debian-12).

## Configuration

Every setting is an environment variable prefixed `STRATUMTAP_`. Locally, export them or use a
`.env` file; under systemd they live in `/etc/default/stratumtap` (see
[`deploy/stratumtap.env.example`](deploy/stratumtap.env.example)).

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_HOST` | `0.0.0.0` | Bind address. Use `127.0.0.1` behind a reverse proxy. |
| `STRATUMTAP_PORT` | `8080` | TCP port. |
| `STRATUMTAP_GPSD_HOST` | `127.0.0.1` | gpsd host. |
| `STRATUMTAP_GPSD_PORT` | `2947` | gpsd port. |
| `STRATUMTAP_CHRONYC_BIN` | `chronyc` | `chronyc` executable (name on `PATH` or absolute path). |
| `STRATUMTAP_CHRONY_POLL_S` | `1.0` | How often `chronyc tracking` is polled, in seconds. |
| `STRATUMTAP_SOURCES_POLL_S` | `10.0` | How often `chronyc sources` / `sourcestats` are polled. |
| `STRATUMTAP_HISTORY_INTERVAL_S` | `5.0` | History sampling interval, in seconds. |
| `STRATUMTAP_HISTORY_SIZE` | `17280` | History ring-buffer size (24 h at 5 s). |
| `STRATUMTAP_DEFAULT_REFRESH_S` | `2` | Default UI auto-refresh interval. |
| `STRATUMTAP_REFRESH_CHOICES_S` | `1,2,5,10,30,60` | Refresh intervals offered in the UI. |
| `STRATUMTAP_DEMO` | `false` | Serve synthetic data; no gpsd or chrony required. |
| `STRATUMTAP_DEMO_LAT` | `51.4779` | Demo-mode latitude (default: Royal Observatory, Greenwich). |
| `STRATUMTAP_DEMO_LON` | `-0.0015` | Demo-mode longitude. |
| `STRATUMTAP_TILE_URL` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | Map tile template. |
| `STRATUMTAP_TILE_ATTRIBUTION` | `© OpenStreetMap contributors` | Attribution shown on the map. |
| `STRATUMTAP_CORS_ORIGINS` | _(empty)_ | Comma-separated origins allowed to call the API cross-origin. Empty disables CORS. |
| `STRATUMTAP_MQTT_URL` | _(empty)_ | Broker URL — `mqtt://host`, `mqtt://user:pass@host:1883`, `mqtts://host:8883`. Empty disables MQTT entirely. |
| `STRATUMTAP_MQTT_TOPIC_PREFIX` | `stratumtap` | Root of the state/position/availability topics. |
| `STRATUMTAP_MQTT_DISCOVERY_PREFIX` | `homeassistant` | Home Assistant discovery prefix. |
| `STRATUMTAP_MQTT_DEVICE_ID` | _(derived)_ | Device id; default is 12 hex from `/etc/machine-id` (or the hostname). |
| `STRATUMTAP_MQTT_CLIENT_ID` | _(derived)_ | MQTT client id; default `stratumtap-<device_id>`. |
| `STRATUMTAP_MQTT_INTERVAL_S` | `60.0` | Floor — publish at least this often even when nothing changed. |
| `STRATUMTAP_MQTT_MIN_INTERVAL_S` | `5.0` | Ceiling — never publish more often than this. |
| `STRATUMTAP_MQTT_DEADBAND_OFFSET_US` | `50.0` | System-offset move (µs) that counts as a change. |
| `STRATUMTAP_MQTT_DEADBAND_PPM` | `0.5` | Frequency move (ppm) that counts as a change. |
| `STRATUMTAP_MQTT_EXPIRE_AFTER_S` | `180` | Seconds after which an un-updated entity goes unavailable in HA. |
| `STRATUMTAP_MQTT_RETAIN_STATE` | `true` | Retain the state message so HA has a value immediately after a restart. |
| `STRATUMTAP_MQTT_TLS_INSECURE` | `false` | Skip certificate/hostname verification for `mqtts://` (self-signed brokers). |
| `STRATUMTAP_MQTT_QOS` | `0` | QoS for state/availability publishes. Discovery is always QoS 1. |
| `STRATUMTAP_STREAM_MAX_CLIENTS` | `16` | Max concurrent `/api/v1/stream` subscribers (beyond that: HTTP 503). |
| `STRATUMTAP_STREAM_QUEUE` | `500` | Per-subscriber event queue; when full the oldest event is dropped. |
| `STRATUMTAP_NMEA_RING` | `1000` | Raw NMEA sentences kept for `/api/v1/raw/nmea`. |
| `STRATUMTAP_LOG_LEVEL` | `info` | `critical`, `error`, `warning`, `info`, `debug` or `trace`. |

## API

Base path `/api/v1`. Full field-by-field contract, sign conventions and examples are in
[`docs/api-contract.md`](docs/api-contract.md); interactive docs are served at `/docs` and the schema at
`/openapi.json`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/time` | Cheapest possible timestamp exchange, for browser clock sync. |
| GET | `/api/v1/status` | Everything: `server`, `ntp`, `gps` (including satellites). |
| GET | `/api/v1/ntp` | chrony tracking state. |
| GET | `/api/v1/ntp/sources` | Parsed `chronyc sources` and `sourcestats`. |
| GET | `/api/v1/gps` | Fix, position, motion, accuracy, DOPs, time offset, satellites. |
| GET | `/api/v1/gps/satellites` | Satellites only. |
| GET | `/api/v1/history` | History ring buffer. `?seconds=`, `?max=`, `?format=json\|csv`. |
| GET | `/api/v1/raw/chronyc/{tracking,sources,sourcestats}` | Verbatim `chronyc` text output. |
| GET | `/api/v1/raw/gpsd` | Last raw gpsd message per class. |
| GET | `/api/v1/config` | Non-secret UI configuration. |
| GET | `/api/v1/health` | Liveness flags. `?strict=1` returns 503 when not healthy. |

Every endpoint accepts an optional `?t0=<unix seconds>` that is echoed back in `server.t0`.
Collector failures never produce a 5xx — they surface as `available: false` with an `error`
string, so the UI can show a degraded panel instead of a broken page.

```sh
curl -s http://ntp.example.org:8080/api/v1/health | python3 -m json.tool
curl -s 'http://ntp.example.org:8080/api/v1/history?seconds=3600&format=csv' -o history.csv
```

## Requirements on the target

- Debian 12 (bookworm) or Debian 13 (trixie) — or similar — with systemd and Python 3.11+ (tested: Debian 12 / Python 3.11 / chrony 4.3 / gpsd 3.22 and Debian 13 / Python 3.13 / chrony 4.6 / gpsd 3.25).
- `chrony` running, and `chronyc tracking` working **for an unprivileged user**. Debian's
  stock `chrony.conf` allows this (`chronyc` reaches `chronyd` over UDP 323 on loopback), so
  there is normally nothing to do. The service also joins the `_chrony` group so it can use
  `/run/chrony/chronyd.sock` where the local configuration permits it.
- `gpsd` running with a real device, ideally with PPS. Verify with `gpspipe -w -n 5`.
- Outbound HTTPS if you want OpenStreetMap tiles; otherwise set `STRATUMTAP_TILE_URL` to a local
  tile server or accept the plain position readout.

The installer will **not** install or reconfigure chrony or gpsd — the time stack on an NTP
server is not something a web UI's installer should touch. It only warns if they are missing.

## Deployment to Debian 12 / 13

From your development machine:

```sh
bash deploy/deploy.sh --dry-run     # show what would be copied, change nothing
bash deploy/deploy.sh               # or: make deploy
```

That rsyncs the repo to `/tmp/stratumtap-src` on the target and runs
`sudo bash /tmp/stratumtap-src/deploy/install.sh` there. Defaults are
`TARGET_HOST=stratum1.local` and `TARGET_USER=pi`; override with `--host` / `--user` or
the `TARGET_HOST` / `TARGET_USER` environment variables.

`deploy/install.sh` (idempotent — run it again to upgrade) does the following:

1. Installs `python3`, `python3-venv`, `rsync` and `curl` if missing; warns if `chronyc` or a
   running `gpsd` is absent.
2. Creates the `stratumtap` system user (no home, no shell) and adds it to `_chrony`.
3. Rsyncs the source into `/opt/stratumtap/app`, excluding `.git`, `node_modules`,
   `.venv`, `tests` and the various caches.
4. Creates `/opt/stratumtap/venv` and installs the pinned `requirements.txt`.
5. Copies the env example to `/etc/default/stratumtap` **only if it does not already
   exist** — your configuration survives upgrades.
6. Installs and enables the hardened systemd unit, restarts it, then polls
   `http://127.0.0.1:$PORT/api/v1/health` for up to 10 s and prints the result and the URL.
   On failure it dumps `journalctl -u stratumtap -n 50`.

When it finishes, the UI is at `http://stratum1.local:8080/`.

### Day-to-day

```sh
# update: just deploy again
bash deploy/deploy.sh

# logs
sudo journalctl -u stratumtap -f
sudo systemctl status stratumtap

# after editing /etc/default/stratumtap
sudo systemctl restart stratumtap

# remove (keeps /etc/default/stratumtap; --purge removes it and the user)
sudo bash /opt/stratumtap/app/deploy/uninstall.sh
```

### Changing the port

Set `STRATUMTAP_PORT` in `/etc/default/stratumtap` and restart. For a port below 1024
(e.g. `STRATUMTAP_PORT=80`) the unit also needs the capability back — uncomment **both**
`CapabilityBoundingSet=CAP_NET_BIND_SERVICE` and `AmbientCapabilities=CAP_NET_BIND_SERVICE`
in `/etc/systemd/system/stratumtap.service`, then `systemctl daemon-reload && systemctl
restart stratumtap`. Putting nginx or Caddy in front and binding `STRATUMTAP_HOST=127.0.0.1`
is the tidier option.

### Firewall

There is no authentication — anyone who can reach the port can see your server's position and
time state. If the host runs a firewall, open the port deliberately and only to the networks
you trust:

```sh
sudo ufw allow from 10.0.0.0/24 to any port 8080 proto tcp
```

Do not expose it to the internet as-is; put it behind a reverse proxy with TLS and auth, or a
VPN.

### Hardening

The unit runs as the unprivileged `stratumtap` user with `ProtectSystem=strict`, `ProtectHome`,
`PrivateTmp`, `NoNewPrivileges`, an empty `CapabilityBoundingSet`, `MemoryDenyWriteExecute`
and friends. The service only reads: it runs `chronyc` and opens a TCP connection to gpsd, and
writes no files. If you tighten it further, keep `AF_INET`/`AF_INET6`/`AF_UNIX` in
`RestrictAddressFamilies=` and do not add `IPAddressDeny=` — either would cut off both
collectors.

## Troubleshooting

**GPS shows "not connected".** The app could not reach gpsd.

```sh
systemctl status gpsd
gpspipe -w -n 5          # should print JSON: VERSION, DEVICES, TPV, SKY...
ss -ltnp | grep 2947     # gpsd listening?
```

If gpsd only listens on a socket-activated unit, `systemctl enable --now gpsd.socket`. If
`gpspipe` prints nothing, the problem is between gpsd and the receiver, not with this app.

**NTP shows "unavailable".** The `chronyc` call failed. Reproduce it as the service user:

```sh
chronyc tracking                      # as yourself
sudo -u stratumtap chronyc tracking       # as the service user — this is the one that matters
```

If the second fails but the first works, your `chrony.conf` restricts the command port; the
default Debian configuration does not. Check `STRATUMTAP_CHRONYC_BIN` if `chronyc` is not on the
service's `PATH`.

**No fix / no satellites.** Not an app problem — check the antenna and give it a few minutes
of sky view. `gpsmon` or `cgps -s` will show the same thing.

**Map is blank / gray.** Tile fetches are failing (no internet, DNS, or a blocking proxy).
The position readout and accuracy figures still work. Point `STRATUMTAP_TILE_URL` at a local tile
server for a fully offline install.

**Service will not start.** `journalctl -u stratumtap -n 50` almost always says why —
usually a port already in use or a typo in `/etc/default/stratumtap` (that file is parsed
by systemd, so no `export`, no shell expansion, no inline comments after a value).

## Development

```sh
make venv       # ./.venv + editable install with dev extras
make dev        # demo data + auto-reload on http://127.0.0.1:8080/
make test       # pytest
make lint       # ruff check + ruff format --check
make fmt        # ruff format + ruff check --fix
make vendor     # re-vendor Leaflet into stratumtap/static/vendor/
make clean
```

For frontend work without a Python environment at all, `python3 scripts/mock_api.py` serves
the static files plus a canned `/api/v1/*` on port 8080, so you can iterate on the UI against
stable, predictable data.

Runtime dependencies are pinned exactly in `requirements.txt` (direct *and* transitive) so the
venv on the target is reproducible; `pyproject.toml` uses compatible-release ranges for
library-style installs. Note that `uvicorn` is installed without the `[standard]` extra on
purpose: `uvloop` and `httptools` publish no `armv7l` wheels and would force a source build on
a 32-bit Raspberry Pi.

## Architecture

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the module layout, the data-domain grouping, and
the design rules (all I/O in background tasks, request handlers only read memory, SI units in
the API, `null` for unavailable values).

## License

MIT — see [`LICENSE`](LICENSE). Copyright (c) 2026 Eric Holzhueter.
