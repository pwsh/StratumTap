---
title: Home Assistant
parent: Integrations
nav_order: 1
---

# Home Assistant
{: .no_toc }

Two paths. **MQTT is the built-in one and the one to use**: set a broker URL and a
StratumTap device with about twenty entities appears in Home Assistant by itself. REST
polling is the alternative when you have no broker — it needs no changes to StratumTap but
a page of YAML, and it cannot see anything faster than its poll interval.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

---

## A. MQTT push — built in, recommended

StratumTap ships an MQTT publisher with Home Assistant
[device-based discovery](https://www.home-assistant.io/integrations/mqtt/#device-discovery-payload).
Point it at your broker and a **StratumTap device with about twenty entities** appears in Home
Assistant on its own — no YAML, no custom component, no HACS, no template writing.

It is off until you set `STRATUMTAP_MQTT_URL`. Nothing connects, nothing publishes, and the
extra dependency sits idle.

### What appears in Home Assistant

One device named after the host (**Settings → Devices & services → MQTT**), with a link back
to StratumTap's own web UI. Its entities:

| Entity | Domain | Unit | Notes |
|---|---|---|---|
| System clock offset | sensor | µs | The headline number: chrony's system-clock offset. |
| PPS offset | sensor | µs | GPS→system offset, only while the source is PPS. Diagnostic. |
| Last offset, RMS offset | sensor | µs | Diagnostic. |
| Clock frequency, Clock skew | sensor | ppm | Diagnostic. |
| Root dispersion | sensor | µs | Diagnostic. |
| Stratum | sensor | — | `1` on a healthy GPS-disciplined server. |
| Reference | sensor | — | `PPS`, `GPS`, or an upstream server's name. Diagnostic. |
| Reference time | sensor | timestamp | Diagnostic. |
| Leap status | sensor | — | Diagnostic. |
| **Synchronized** | binary_sensor | — | `connectivity`: on = chrony is in sync. |
| **GPS fix** | binary_sensor | — | `connectivity`: on = 2D or better. |
| Fix | sensor | — | `3D FIX`, `2D DGPS FIX`, … Diagnostic. |
| Satellites used / seen | sensor | — | |
| HDOP, Horizontal error | sensor | — / m | Diagnostic. |
| Altitude | sensor | m | MSL. Diagnostic. |
| Maidenhead grid | sensor | — | Diagnostic. |
| Position | device_tracker | — | Latitude, longitude, accuracy, altitude. |

Everything marked *diagnostic* lands in the device page's Diagnostic section rather than on
your dashboard, which is where most of it belongs.

### Setup

1. **Have an MQTT broker** and the
   [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) configured in Home
   Assistant. The Mosquitto broker add-on is the usual choice; any broker both machines can
   reach works.

2. **Point StratumTap at it** in `/etc/default/stratumtap`:

   ```sh
   STRATUMTAP_MQTT_URL=mqtt://stratumtap:secret@192.0.2.5:1883
   ```

   Percent-encode `@` as `%40` and `:` as `%3A` in the password. For TLS use
   `mqtts://broker.lan:8883`; add `STRATUMTAP_MQTT_TLS_INSECURE=true` only if the broker's
   certificate is self-signed and you cannot install its CA.

3. **Restart and check:**

   ```sh
   sudo systemctl restart stratumtap
   curl -s http://localhost:8080/api/v1/health | jq .mqtt
   ```

   ```json
   {
     "enabled": true,
     "connected": true,
     "publishes": 3,
     "errors": 0,
     "last_publish_at": 1787866000.12,
     "last_reason": "change:system_offset_us",
     "last_error": null
   }
   ```

4. The device appears in Home Assistant within a second or two. Nothing else to do.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `STRATUMTAP_MQTT_URL` | *(empty)* | `mqtt://host`, `mqtt://user:pass@host:1883` or `mqtts://host:8883`. Empty disables everything below. |
| `STRATUMTAP_MQTT_TOPIC_PREFIX` | `stratumtap` | Root of the data topics. |
| `STRATUMTAP_MQTT_DISCOVERY_PREFIX` | `homeassistant` | Must match Home Assistant's discovery prefix. |
| `STRATUMTAP_MQTT_DEVICE_ID` | *(derived)* | 12 hex digits from `sha256(/etc/machine-id)`, or of the hostname. Stable across reboots, unique per machine. |
| `STRATUMTAP_MQTT_CLIENT_ID` | *(derived)* | `stratumtap-<device_id>`. |
| `STRATUMTAP_MQTT_INTERVAL_S` | `60.0` | Floor: publish at least this often. |
| `STRATUMTAP_MQTT_MIN_INTERVAL_S` | `5.0` | Ceiling: never publish more often than this. |
| `STRATUMTAP_MQTT_DEADBAND_OFFSET_US` | `50.0` | Offset move (µs) worth publishing for. |
| `STRATUMTAP_MQTT_DEADBAND_PPM` | `0.5` | Frequency move (ppm) worth publishing for. |
| `STRATUMTAP_MQTT_EXPIRE_AFTER_S` | `180` | `expire_after` on every entity. |
| `STRATUMTAP_MQTT_RETAIN_STATE` | `true` | Retain the state message. |
| `STRATUMTAP_MQTT_TLS_INSECURE` | `false` | `mqtts://` only: skip certificate/hostname checks. |
| `STRATUMTAP_MQTT_QOS` | `0` | QoS for state/position/availability. Discovery is always QoS 1. |

Full reference: [Configuration](../configuration.md#mqtt--home-assistant).

### How often it publishes

This is the real advantage over polling, so it is worth understanding exactly.

A ticker evaluates the rule once a second:

- **Floor.** If nothing has been published for `MQTT_INTERVAL_S` (60 s), publish. Entities
  never go stale just because the server is behaving.
- **Ceiling.** Within `MQTT_MIN_INTERVAL_S` (5 s) of the last message, publish *nothing*,
  however dramatic the change. A chrony step correction cannot turn into a message storm.
- **In between**, publish only on a **significant change**: the system offset moved by more
  than `MQTT_DEADBAND_OFFSET_US` (50 µs), the frequency by more than `MQTT_DEADBAND_PPM`
  (0.5 ppm), or any change at all in stratum, sync state, GPS fix, fix mode, reference, leap
  status, availability of chrony or gpsd, or the satellite count.

So a quiet, well-disciplined server produces **one message a minute**; a server that loses its
fix or steps its clock produces one within five seconds. `mqtt.last_reason` in `/api/v1/health`
tells you which rule fired last: `"interval"` or `"change:<field>"`.

`expire_after: 180` (three times the floor) is the backstop: if the publisher stops without a
clean goodbye *and* the broker somehow misses the last will, every entity goes unavailable
after three minutes rather than showing a stale reading that looks current forever.

### Topics

With the default prefix and a device id of `a1b2c3d4e5f6`:

| Topic | Retained | Contents |
|---|---|---|
| `stratumtap/a1b2c3d4e5f6/status` | yes | `online` / `offline`. The last will, so the broker publishes `offline` for us if the process dies or the network drops. |
| `stratumtap/a1b2c3d4e5f6/state` | yes* | One flat JSON object; every entity reads a field from it. |
| `stratumtap/a1b2c3d4e5f6/position` | yes | The `device_tracker` attributes. |
| `homeassistant/device/stratumtap_a1b2c3d4e5f6/config` | yes | The device-discovery payload (QoS 1). |
| `homeassistant/device_tracker/stratumtap_a1b2c3d4e5f6/position/config` | yes | Discovery for the tracker (QoS 1). |

\* controlled by `STRATUMTAP_MQTT_RETAIN_STATE`.

StratumTap also subscribes to `homeassistant/status` and re-publishes discovery, availability
and the current state whenever Home Assistant announces `online` — so an HA restart brings the
device straight back rather than leaving it missing until the next retained-message read.

The state document, in full:

```json
{
  "updated": "2026-08-26T21:45:24.359Z",
  "ntp_available": true,
  "system_offset_us": 0.246,
  "pps_offset_us": -0.243,
  "last_offset_us": 0.124,
  "rms_offset_us": 0.597,
  "frequency_ppm": 17.970,
  "skew_ppm": 0.099,
  "root_dispersion_us": 10.99,
  "stratum": 1,
  "reference": "PPS",
  "reference_id": "50505300",
  "ref_time": "2026-08-26T21:45:23.000000Z",
  "leap_status": "Normal",
  "synchronized": true,
  "gps_available": true,
  "gps_fix": true,
  "fix_mode": 3,
  "fix_text": "3D FIX",
  "sats_used": 10,
  "sats_seen": 12,
  "hdop": 0.96,
  "pdop": 1.22,
  "eph_m": 4.212,
  "sep_m": 6.108,
  "lat": 51.477900041,
  "lon": -0.001500343,
  "alt_hae_m": 46.1,
  "alt_msl_m": 0.9,
  "grid_square": "IO91xl94",
  "gpsd_connected": true
}
```

Anything unavailable is `null` — never a plausible-looking zero.

### The discovery payload

Abbreviated to three of the twenty components; the rest follow the same shape:

{% raw %}
```json
{
  "dev": {
    "ids": ["stratumtap_a1b2c3d4e5f6"],
    "name": "stratum1",
    "mf": "StratumTap",
    "mdl": "GPS-disciplined NTP monitor",
    "sw": "0.1.0",
    "cu": "http://stratum1:8080/"
  },
  "o": { "name": "StratumTap", "sw": "0.1.0", "url": "https://github.com/pwsh/StratumTap" },
  "availability_topic": "stratumtap/a1b2c3d4e5f6/status",
  "state_topic": "stratumtap/a1b2c3d4e5f6/state",
  "qos": 0,
  "cmps": {
    "system_offset": {
      "p": "sensor",
      "name": "System clock offset",
      "unit_of_measurement": "µs",
      "device_class": "duration",
      "state_class": "measurement",
      "suggested_display_precision": 3,
      "icon": "mdi:clock-check-outline",
      "unique_id": "stratumtap_a1b2c3d4e5f6_system_offset",
      "value_template": "{% if value_json.system_offset_us is not none %}{{ value_json.system_offset_us }}{% endif %}",
      "expire_after": 180
    },
    "stratum": {
      "p": "sensor",
      "name": "Stratum",
      "state_class": "measurement",
      "icon": "mdi:layers-triple",
      "unique_id": "stratumtap_a1b2c3d4e5f6_stratum",
      "value_template": "{% if value_json.stratum is not none %}{{ value_json.stratum }}{% endif %}",
      "expire_after": 180
    },
    "synchronized": {
      "p": "binary_sensor",
      "name": "Synchronized",
      "device_class": "connectivity",
      "unique_id": "stratumtap_a1b2c3d4e5f6_synchronized",
      "value_template": "{% if value_json.synchronized %}ON{% else %}OFF{% endif %}",
      "expire_after": 180
    }
  }
}
```
{% endraw %}

Three details are deliberate:

- **`availability_topic` and `state_topic` sit at the root** and are inherited by every
  component, so the payload stays small.
- **The `is not none` guard** makes a null field render as an *empty* payload, which Home
  Assistant ignores. Without it a missing value would be recorded as the literal string
  `None`; with it, the entity keeps its last reading until `expire_after` retires it.
- **`unique_id` on everything**, derived from the stable device id. Rename entities, move them
  between areas, change their units — none of it breaks on restart.

{: .note }
> **If Home Assistant rejects `µs`** for `device_class: duration` on an older release, that
> entity will show as unavailable with an error in the log. Older HA releases accept only
> `s`/`min`/`h`/`d` for `duration`. There is no setting for it; if you are on such a release,
> use the REST path below for the offset sensors, or upgrade.

### Recorder tips

Twenty entities at up to one message per five seconds can add up. The diagnostic ones are
worth watching live and not worth keeping forever:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.stratum1_last_offset
      - sensor.stratum1_rms_offset
      - sensor.stratum1_clock_skew
      - sensor.stratum1_root_dispersion
      - sensor.stratum1_hdop
      - sensor.stratum1_satellites_seen
      - sensor.stratum1_reference_time
      - sensor.stratum1_maidenhead_grid
```

Keep `system_offset`, `stratum`, `synchronized`, `gps_fix` and `sats_used` — those are the
ones you will actually look back at. Everything numeric carries `state_class: measurement`, so
they all feed long-term statistics.

### Troubleshooting

Start with `curl -s http://localhost:8080/api/v1/health | jq .mqtt` — it distinguishes every
failure mode below.

| Symptom | `health.mqtt` | Cause |
|---|---|---|
| No device in HA | `"enabled": false` | `STRATUMTAP_MQTT_URL` is not set, or the env file was not reloaded. `systemctl show stratumtap -p Environment` shows what the unit actually got. |
| No device in HA | `"connected": false`, `last_error` mentions *Not authorized* / *Connection Refused* | Wrong credentials, or the broker requires auth. Check that `@`/`:` in the password are percent-encoded. |
| No device in HA | `"connected": false`, `last_error` mentions *certificate verify failed* | The broker's certificate is not trusted. Install its CA, or set `STRATUMTAP_MQTT_TLS_INSECURE=true` knowing what that costs. |
| No device in HA | `"connected": true`, `publishes > 0` | StratumTap is publishing fine; the problem is on the HA side. Check `STRATUMTAP_MQTT_DISCOVERY_PREFIX` matches HA's, and subscribe with `mosquitto_sub -v -t 'homeassistant/#'` to confirm the retained config message is on the broker. |
| Device vanished after an HA restart | `"connected": true` | Should not happen — StratumTap re-announces on `homeassistant/status: online`. If HA and the broker restarted together, restart `stratumtap` to force a re-announce. |
| Entities *unavailable* after ~3 minutes | `"connected": false` | The publisher lost the broker. The last will (or `expire_after`) did its job. It reconnects with backoff up to 60 s; `journalctl -u stratumtap` has the reason. |
| Entities *unavailable* immediately | — | The availability topic says `offline`. Either the service is stopped, or a stale retained `offline` is sitting on the broker from a previous run — it is corrected on the next connect. |
| Entities show `unknown` | `"connected": true` | The underlying value is `null`: chrony or gpsd has no data yet. Check `/api/v1/status`. |
| `journalctl` shows *install with pip install 'stratumtap\[mqtt\]'* | `"enabled": true`, `"connected": false` | The `aiomqtt` package is missing from the venv. Re-run the installer, or `/opt/stratumtap/venv/bin/pip install aiomqtt`. |

Watch the traffic directly with:

```sh
mosquitto_sub -h broker.lan -v -t 'stratumtap/#' -t 'homeassistant/device/stratumtap_#'
```

---

## B. REST polling — the alternative

No broker? Home Assistant's built-in
[RESTful integration](https://www.home-assistant.io/integrations/rest/) can poll
`/api/v1/status` once and derive as many sensors as you like from the single response. No
custom component, no HACS, no changes to StratumTap — but you write and maintain the templates
yourself, and the worst-case latency is your whole `scan_interval`.

Add this to `configuration.yaml` (or a `!include`d package) and restart Home Assistant.

{% raw %}
```yaml
rest:
  - resource: http://192.0.2.10:8080/api/v1/status
    scan_interval: 60
    timeout: 10
    # One HTTP request feeds every entity below.
    sensor:
      - name: "StratumTap system clock offset"
        unique_id: stratumtap_system_offset
        # SI seconds in the API; microseconds are the readable unit here.
        value_template: >-
          {{ (value_json.ntp.system_offset_s | float(0) * 1000000) | round(3) }}
        unit_of_measurement: "µs"
        device_class: duration
        state_class: measurement
        availability: "{{ value_json.ntp.available }}"
        icon: mdi:clock-check-outline

      - name: "StratumTap stratum"
        unique_id: stratumtap_stratum
        value_template: "{{ value_json.ntp.stratum }}"
        state_class: measurement
        availability: "{{ value_json.ntp.available }}"
        icon: mdi:layers-triple-outline

      - name: "StratumTap frequency"
        unique_id: stratumtap_frequency_ppm
        value_template: "{{ value_json.ntp.frequency_ppm | round(3) }}"
        unit_of_measurement: "ppm"
        state_class: measurement
        availability: "{{ value_json.ntp.available }}"

      - name: "StratumTap reference"
        unique_id: stratumtap_reference
        value_template: "{{ value_json.ntp.reference_name }}"
        availability: "{{ value_json.ntp.available }}"
        icon: mdi:satellite-uplink

      - name: "StratumTap reference time"
        unique_id: stratumtap_ref_time
        value_template: "{{ value_json.ntp.ref_time }}"
        device_class: timestamp
        availability: "{{ value_json.ntp.available }}"

      - name: "StratumTap satellites used"
        unique_id: stratumtap_sats_used
        value_template: "{{ value_json.gps.satellites.used }}"
        state_class: measurement
        availability: "{{ value_json.gps.available }}"
        icon: mdi:satellite-variant

      - name: "StratumTap satellites seen"
        unique_id: stratumtap_sats_seen
        value_template: "{{ value_json.gps.satellites.seen }}"
        state_class: measurement
        availability: "{{ value_json.gps.available }}"
        icon: mdi:satellite-variant

      - name: "StratumTap HDOP"
        unique_id: stratumtap_hdop
        value_template: "{{ value_json.gps.dop.hdop | round(2) }}"
        state_class: measurement
        availability: "{{ value_json.gps.available }}"

      - name: "StratumTap horizontal error"
        unique_id: stratumtap_eph
        value_template: "{{ value_json.gps.accuracy.eph_m | round(2) }}"
        unit_of_measurement: "m"
        device_class: distance
        state_class: measurement
        availability: "{{ value_json.gps.available }}"

      - name: "StratumTap fix"
        unique_id: stratumtap_fix
        value_template: "{{ value_json.gps.fix.fix_text }}"
        availability: "{{ value_json.gps.available }}"
        icon: mdi:crosshairs-gps

      - name: "StratumTap GPS to system offset"
        unique_id: stratumtap_gps_time_offset
        value_template: >-
          {{ (value_json.gps.time_offset.offset_s | float(0) * 1000000) | round(3) }}
        unit_of_measurement: "µs"
        device_class: duration
        state_class: measurement
        availability: >-
          {{ value_json.gps.available and value_json.gps.time_offset.source is not none }}

    binary_sensor:
      - name: "StratumTap NTP synchronized"
        unique_id: stratumtap_ntp_synchronized
        value_template: "{{ value_json.ntp.synchronized }}"
        availability: "{{ value_json.ntp.available }}"
        icon: mdi:clock-check

      - name: "StratumTap GPS fix problem"
        unique_id: stratumtap_gps_fix_problem
        # device_class "problem": on = there IS a problem, so invert.
        value_template: "{{ (value_json.gps.fix.mode | int(0)) < 2 }}"
        device_class: problem
        availability: "{{ value_json.gps.available }}"
```
{% endraw %}

Replace `192.0.2.10` with your server's address — `stratum1.local` works too if mDNS
resolution is available to Home Assistant.

### Why `scan_interval: 60`

{: .warning }
> **Never poll faster than every 10 seconds**, and 30–60 s is the right answer for Home
> Assistant.

StratumTap itself polls chrony once a second and gpsd delivers a fix once per receiver cycle,
so a faster `scan_interval` does not get you fresher data — it just fills Home Assistant's
recorder database. At 60 s, eleven sensors produce about 16 000 states a day, which is
comfortable. At 5 s it would be 190 000, and your database will notice.

If you want fine-grained history, take it from StratumTap's own
[history endpoint](../user-guide/recording-export.md#server-side-history) — it samples every
5 s and downsamples on request — and leave Home Assistant on the coarse view.

### `unique_id` matters

Every entity above has one. Without a `unique_id`, Home Assistant will not let you rename the
entity, assign it to an area, or change its unit from the UI. Adding them later changes the
entity IDs, so put them in from the start.

### `state_class: measurement`

This is what makes an entity eligible for long-term statistics — the hourly min/mean/max rows
that survive recorder purges and drive the statistics graph card. Apply it to anything
numeric and continuously varying; leave it off text sensors such as *reference* and *fix*.

### `availability`

Each domain object in the API carries `available` and `error`, and a failing collector returns
HTTP 200 with `available: false` rather than an error status. Without an `availability`
template your sensors would happily record `0` or `unknown` as if it were a real reading. The
templates above mark the entity *unavailable* instead, which is both more honest and easier to
alert on.

### Recorder tips

Eleven sensors at 60 s is fine, but some of them are noisy and not worth keeping forever.
Exclude the ones you only ever look at live:

```yaml
recorder:
  exclude:
    entities:
      - sensor.stratumtap_hdop
      - sensor.stratumtap_satellites_seen
      - sensor.stratumtap_reference_time
      - sensor.stratumtap_gps_to_system_offset
```

Or invert it and keep only what you care about:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.stratumtap_*
  include:
    entities:
      - sensor.stratumtap_system_clock_offset
      - sensor.stratumtap_satellites_used
      - binary_sensor.stratumtap_ntp_synchronized
```

### A card and an automation

```yaml
type: entities
title: Time server
entities:
  - binary_sensor.stratumtap_ntp_synchronized
  - sensor.stratumtap_system_clock_offset
  - sensor.stratumtap_stratum
  - sensor.stratumtap_reference
  - sensor.stratumtap_fix
  - sensor.stratumtap_satellites_used
```

{% raw %}
```yaml
automation:
  - alias: "Time server lost sync"
    triggers:
      - trigger: state
        entity_id: binary_sensor.stratumtap_ntp_synchronized
        to: "off"
        for: "00:05:00"
    actions:
      - action: notify.persistent_notification
        data:
          title: "NTP server out of sync"
          message: >-
            {{ states('sensor.stratumtap_stratum') }} ·
            offset {{ states('sensor.stratumtap_system_clock_offset') }} µs
```
{% endraw %}

The `for: "00:05:00"` matters — a brief blip during a chrony step correction is not worth a
notification.

{: .note }
> **If Home Assistant rejects `µs`** for `device_class: duration` on an older release, drop the
> `device_class` line and keep `unit_of_measurement` and `state_class`. Everything else works
> unchanged.

### Position

The API also gives you `gps.position.lat`, `gps.position.lon` and `gps.position.alt_hae_m` if
you want them as sensors. For a timing installation they never change, so most people skip
them.

Home Assistant also ships a
[built-in `gpsd` integration](https://www.home-assistant.io/integrations/gpsd/) that connects
straight to the Pi's gpsd on port 2947. It gives you **position and fix mode only** — no
chrony data, no accuracy estimates, no satellite counts — so it complements the REST sensors
above rather than replacing them. Note that it needs gpsd listening on the network (`-G`),
which is not the default.

---

## See also

- [The API guide](../api.md) — the endpoints and their shapes
- [The API contract](../api-contract.md) — every field, exactly
- [Recording and export](../user-guide/recording-export.md#server-side-history) — for
  high-resolution history without touching Home Assistant's database
