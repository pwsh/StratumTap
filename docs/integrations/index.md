---
title: Integrations
nav_order: 7
has_children: true
---

# Integrations

StratumTap's JSON API is deliberately plain, so anything that can fetch a URL can consume it.

- **[Home Assistant](home-assistant.md)** — a built-in MQTT publisher with device discovery
  (set one environment variable and the device appears), plus a complete `rest` sensor
  configuration for installations without a broker.

## Anything else

There is no dedicated integration for these, but they are all a few lines of configuration
against `/api/v1/status`:

| Tool | Approach |
|---|---|
| **Uptime Kuma / Nagios / Icinga** | HTTP check on `/api/v1/health?strict=1` — it returns 503 when unhealthy. |
| **Prometheus** | A small exporter, or `json_exporter` with a config mapping `ntp.system_offset_s`, `ntp.stratum`, `gps.satellites.used` and friends. |
| **Telegraf / InfluxDB** | The `http` input plugin with `data_format = "json_v2"` against `/api/v1/status`. |
| **Grafana** | The Infinity data source can read `/api/v1/history?format=csv` directly. |
| **A shell script** | `curl -s .../api/v1/status \| jq` — see the [API guide](../api.md#examples). |

{: .note }
> Whatever you use, poll no faster than every 10 seconds. StratumTap itself polls chrony once
> a second; 30–60 s is plenty for a monitoring system and keeps its database small.
