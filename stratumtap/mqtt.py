"""Optional MQTT publisher with Home Assistant discovery.

Disabled unless ``STRATUMTAP_MQTT_URL`` is set. When it is, one background task
(the same shape as every other producer in this app) owns a single broker
connection: it publishes a retained device-discovery message, a retained
availability message backed by an LWT, and a flat JSON state document.

Two rules shape the whole module:

1. **It never blocks a collector.** The publisher only ever *reads* the shared
   :class:`~stratumtap.state.StateStore` snapshots on its own 1 s ticker. Nothing
   here is awaited by chrony, gpsd or a request handler.
2. **It never dies.** Every broker error is caught, counted and retried with
   exponential backoff; the task is cancellation-safe and an unusable broker
   degrades to "``mqtt.connected`` is false in ``/api/v1/health``", never to a
   crashed process.

Publishing is throttled by a floor interval (say something at least once a
minute so entities stay fresh) combined with a change trigger and a deadband
(say something *immediately* when the numbers actually move). See
:func:`should_publish`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

log = logging.getLogger("stratumtap.mqtt")

#: Identity advertised to Home Assistant.
MANUFACTURER = "StratumTap"
MODEL = "GPS-disciplined NTP monitor"
ORIGIN_NAME = "StratumTap"
ORIGIN_URL = "https://github.com/pwsh/StratumTap"

#: Seeds the default device id when it is readable.
MACHINE_ID_PATH = "/etc/machine-id"

#: Micro sign + s, the unit every offset is published in.
MICROSECONDS = "µs"
PPM = "ppm"
METRES = "m"

#: Default ports per URL scheme.
DEFAULT_PORT = 1883
DEFAULT_TLS_PORT = 8883

#: The ticker evaluates :func:`should_publish` this often.
TICK_S = 1.0
#: Reconnect backoff bounds, in seconds.
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 60.0
#: Position is republished when it moved further than this.
POSITION_EPSILON_M = 0.5
#: Best-effort budget for the farewell ``offline`` publish.
SHUTDOWN_TIMEOUT_S = 2.0

#: Fields whose *any* change triggers a publish (subject to the min interval).
CHANGE_FIELDS = (
    "stratum",
    "synchronized",
    "gps_fix",
    "fix_mode",
    "reference",
    "leap_status",
    "ntp_available",
    "gps_available",
    "sats_used",
)

MISSING_AIOMQTT = (
    "STRATUMTAP_MQTT_URL is set but the aiomqtt package is not installed; "
    "install with pip install 'stratumtap[mqtt]'"
)


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MqttTarget:
    """A broker address parsed out of ``STRATUMTAP_MQTT_URL``."""

    host: str
    port: int
    username: str | None = None
    password: str | None = None
    tls: bool = False


def parse_mqtt_url(url: str) -> MqttTarget:
    """Parse ``mqtt(s)://[user[:pass]@]host[:port]`` into a :class:`MqttTarget`.

    The port defaults to 1883 for ``mqtt://`` and 8883 for ``mqtts://``. Anything
    that is not one of those two schemes, or that carries no host, is a
    :class:`ValueError` — a typo in the broker URL must be loud, not silent.
    """
    text = (url or "").strip()
    if not text:
        raise ValueError("empty MQTT URL")
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ("mqtt", "mqtts"):
        raise ValueError(f"unsupported MQTT URL scheme {parts.scheme!r} (use mqtt:// or mqtts://)")
    try:
        port = parts.port
    except ValueError as exc:  # non-numeric or out-of-range port
        raise ValueError(f"invalid port in MQTT URL {text!r}") from exc
    host = parts.hostname
    if not host:
        raise ValueError(f"MQTT URL {text!r} has no host")
    tls = scheme == "mqtts"
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None
    return MqttTarget(
        host=host,
        port=port or (DEFAULT_TLS_PORT if tls else DEFAULT_PORT),
        username=username,
        password=password,
        tls=tls,
    )


def _machine_seed() -> str:
    """``/etc/machine-id`` when readable, else the hostname."""
    try:
        text = Path(MACHINE_ID_PATH).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        text = ""
    return text or socket.gethostname()


def device_id(settings) -> str:
    """Stable per-machine id: the configured one, else 12 hex from the machine id.

    Two StratumTap instances on one network must not collide in Home Assistant,
    and the id must survive a restart — hence the machine id (or the hostname)
    rather than anything random.
    """
    configured = (getattr(settings, "mqtt_device_id", "") or "").strip()
    if configured:
        return configured
    return hashlib.sha256(_machine_seed().encode("utf-8")).hexdigest()[:12]


def client_id(settings) -> str:
    """MQTT client identifier: the configured one, else ``stratumtap-<device_id>``."""
    configured = (getattr(settings, "mqtt_client_id", "") or "").strip()
    return configured or f"stratumtap-{device_id(settings)}"


def _us(seconds: float | None) -> float | None:
    """Seconds → microseconds, rounded to 3 decimals (i.e. picosecond noise dropped)."""
    if seconds is None:
        return None
    return round(float(seconds) * 1e6, 3)


def _iso(unix: float) -> str:
    return datetime.fromtimestamp(unix, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_state(store, now: float | None = None) -> dict:
    """Flatten the current snapshots into the single JSON document HA consumes.

    Flat on purpose: every entity's ``value_template`` is then a one-level lookup,
    which keeps the discovery payload small and the templates cheap to render.
    Anything unavailable is ``null`` rather than a plausible-looking zero.
    """
    if now is None:
        now = time.time()
    ntp = store.ntp
    gps = store.gps
    ntp_ok = bool(ntp.available)
    gps_ok = bool(gps.available)

    offset = gps.time_offset
    pps_offset_s = offset.offset_s if (gps_ok and offset.source == "PPS") else None
    mode = gps.fix.mode if gps_ok else None

    return {
        "updated": _iso(now),
        # chrony
        "ntp_available": ntp_ok,
        "system_offset_us": _us(ntp.system_offset_s) if ntp_ok else None,
        "pps_offset_us": _us(pps_offset_s),
        "last_offset_us": _us(ntp.last_offset_s) if ntp_ok else None,
        "rms_offset_us": _us(ntp.rms_offset_s) if ntp_ok else None,
        "frequency_ppm": ntp.frequency_ppm if ntp_ok else None,
        "skew_ppm": ntp.skew_ppm if ntp_ok else None,
        "root_dispersion_us": _us(ntp.root_dispersion_s) if ntp_ok else None,
        "stratum": ntp.stratum if ntp_ok else None,
        "reference": ntp.reference_name if ntp_ok else None,
        "reference_id": ntp.reference_id if ntp_ok else None,
        "ref_time": ntp.ref_time if ntp_ok else None,
        "leap_status": ntp.leap_status if ntp_ok else None,
        "synchronized": bool(ntp_ok and ntp.synchronized),
        # gpsd
        "gps_available": gps_ok,
        "gps_fix": bool(mode is not None and mode >= 2),
        "fix_mode": mode,
        "fix_text": gps.fix.fix_text if gps_ok else None,
        "sats_used": gps.satellites.used if gps_ok else None,
        "sats_seen": gps.satellites.seen if gps_ok else None,
        "hdop": gps.dop.hdop if gps_ok else None,
        "pdop": gps.dop.pdop if gps_ok else None,
        "eph_m": gps.accuracy.eph_m if gps_ok else None,
        "sep_m": gps.accuracy.sep_m if gps_ok else None,
        "lat": gps.position.lat if gps_ok else None,
        "lon": gps.position.lon if gps_ok else None,
        "alt_hae_m": gps.position.alt_hae_m if gps_ok else None,
        "alt_msl_m": gps.position.alt_msl_m if gps_ok else None,
        "grid_square": gps.position.grid_square if gps_ok else None,
        "gpsd_connected": bool(gps.connected),
    }


def build_position(store) -> dict | None:
    """The ``device_tracker`` attribute document, or ``None`` without a position."""
    gps = store.gps
    if not gps.available:
        return None
    lat = gps.position.lat
    lon = gps.position.lon
    if lat is None or lon is None:
        return None
    payload: dict = {"latitude": lat, "longitude": lon}
    eph = gps.accuracy.eph_m
    payload["gps_accuracy"] = None if eph is None else round(float(eph), 1)
    payload["altitude"] = gps.position.alt_msl_m
    return payload


def position_distance_m(old: dict | None, new: dict | None) -> float:
    """Rough metres between two position payloads (``inf`` when one is missing)."""
    if not old or not new:
        return math.inf
    try:
        dlat = float(new["latitude"]) - float(old["latitude"])
        dlon = float(new["longitude"]) - float(old["longitude"])
        lat = math.radians(float(new["latitude"]))
    except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
        return math.inf
    # Equirectangular approximation: exact enough to answer "did it move a metre?".
    return math.hypot(dlat, dlon * math.cos(lat)) * 111_320.0


def _exceeds(old, new, deadband: float) -> bool:
    """Numeric change larger than *deadband*; appearing/disappearing always counts."""
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    try:
        return abs(float(new) - float(old)) > max(0.0, float(deadband))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return old != new


def significant_change(prev: dict | None, new: dict, settings) -> str | None:
    """Name the first field that moved enough to be worth a message, else ``None``."""
    if not prev:
        return None
    if _exceeds(
        prev.get("system_offset_us"),
        new.get("system_offset_us"),
        getattr(settings, "mqtt_deadband_offset_us", 50.0),
    ):
        return "system_offset_us"
    if _exceeds(
        prev.get("frequency_ppm"),
        new.get("frequency_ppm"),
        getattr(settings, "mqtt_deadband_ppm", 0.5),
    ):
        return "frequency_ppm"
    for field in CHANGE_FIELDS:
        if prev.get(field) != new.get(field):
            return field
    return None


def should_publish(
    prev_state: dict | None,
    new_state: dict,
    last_publish_at: float | None,
    now: float,
    settings,
) -> tuple[bool, str]:
    """Decide whether to publish *new_state*, and say why.

    The contract, in order:

    * nothing published yet → publish immediately (``"interval"``);
    * ``now - last_publish >= mqtt_interval_s`` → publish (``"interval"``);
    * ``now - last_publish < mqtt_min_interval_s`` → never publish, whatever changed;
    * otherwise publish iff :func:`significant_change` names a field
      (``"change:<field>"``).
    """
    if last_publish_at is None:
        return True, "interval"
    elapsed = now - last_publish_at
    if elapsed >= max(0.0, float(getattr(settings, "mqtt_interval_s", 60.0))):
        return True, "interval"
    if elapsed < max(0.0, float(getattr(settings, "mqtt_min_interval_s", 5.0))):
        return False, ""
    field = significant_change(prev_state, new_state, settings)
    if field is not None:
        return True, f"change:{field}"
    return False, ""


# --------------------------------------------------------------------------
# discovery payloads
# --------------------------------------------------------------------------


def _nullable(field: str) -> str:
    """A template that renders nothing when the field is ``null``.

    An empty payload makes Home Assistant *ignore* the update rather than record
    the string ``"None"`` as a number; the entity then ages out via
    ``expire_after`` instead of showing a fabricated value.
    """
    return "{% if value_json." + field + " is not none %}{{ value_json." + field + " }}{% endif %}"


def _flag(field: str) -> str:
    return "{% if value_json." + field + " %}ON{% else %}OFF{% endif %}"


#: ``key -> (state field, component spec)``. The keys are stable: they become the
#: object ids and the ``unique_id`` suffixes, so renaming one renames an entity.
_COMPONENTS: tuple[tuple[str, str, dict], ...] = (
    (
        "system_offset",
        "system_offset_us",
        {
            "p": "sensor",
            "name": "System clock offset",
            "unit_of_measurement": MICROSECONDS,
            "device_class": "duration",
            "state_class": "measurement",
            "suggested_display_precision": 3,
            "icon": "mdi:clock-check-outline",
        },
    ),
    (
        "pps_offset",
        "pps_offset_us",
        {
            "p": "sensor",
            "name": "PPS offset",
            "unit_of_measurement": MICROSECONDS,
            "device_class": "duration",
            "state_class": "measurement",
            "suggested_display_precision": 3,
            "entity_category": "diagnostic",
        },
    ),
    (
        "last_offset",
        "last_offset_us",
        {
            "p": "sensor",
            "name": "Last offset",
            "unit_of_measurement": MICROSECONDS,
            "device_class": "duration",
            "state_class": "measurement",
            "suggested_display_precision": 3,
            "entity_category": "diagnostic",
        },
    ),
    (
        "rms_offset",
        "rms_offset_us",
        {
            "p": "sensor",
            "name": "RMS offset",
            "unit_of_measurement": MICROSECONDS,
            "device_class": "duration",
            "state_class": "measurement",
            "suggested_display_precision": 3,
            "entity_category": "diagnostic",
        },
    ),
    (
        "frequency",
        "frequency_ppm",
        {
            "p": "sensor",
            "name": "Clock frequency",
            "unit_of_measurement": PPM,
            "state_class": "measurement",
            "suggested_display_precision": 3,
            "entity_category": "diagnostic",
            "icon": "mdi:sine-wave",
        },
    ),
    (
        "skew",
        "skew_ppm",
        {
            "p": "sensor",
            "name": "Clock skew",
            "unit_of_measurement": PPM,
            "state_class": "measurement",
            "suggested_display_precision": 3,
            "entity_category": "diagnostic",
            "icon": "mdi:sine-wave",
        },
    ),
    (
        "root_dispersion",
        "root_dispersion_us",
        {
            "p": "sensor",
            "name": "Root dispersion",
            "unit_of_measurement": MICROSECONDS,
            "device_class": "duration",
            "state_class": "measurement",
            "suggested_display_precision": 1,
            "entity_category": "diagnostic",
        },
    ),
    (
        "stratum",
        "stratum",
        {
            "p": "sensor",
            "name": "Stratum",
            "state_class": "measurement",
            "icon": "mdi:layers-triple",
        },
    ),
    (
        "reference",
        "reference",
        {
            "p": "sensor",
            "name": "Reference",
            "entity_category": "diagnostic",
            "icon": "mdi:satellite-uplink",
        },
    ),
    (
        "ref_time",
        "ref_time",
        {
            "p": "sensor",
            "name": "Reference time",
            "device_class": "timestamp",
            "entity_category": "diagnostic",
        },
    ),
    (
        "leap_status",
        "leap_status",
        {
            "p": "sensor",
            "name": "Leap status",
            "entity_category": "diagnostic",
            "icon": "mdi:calendar-clock",
        },
    ),
    (
        "synchronized",
        "synchronized",
        {
            "p": "binary_sensor",
            "name": "Synchronized",
            "device_class": "connectivity",
        },
    ),
    (
        "gps_fix",
        "gps_fix",
        {
            "p": "binary_sensor",
            "name": "GPS fix",
            "device_class": "connectivity",
        },
    ),
    (
        "fix_text",
        "fix_text",
        {
            "p": "sensor",
            "name": "Fix",
            "entity_category": "diagnostic",
            "icon": "mdi:crosshairs-gps",
        },
    ),
    (
        "sats_used",
        "sats_used",
        {
            "p": "sensor",
            "name": "Satellites used",
            "state_class": "measurement",
            "icon": "mdi:satellite-variant",
        },
    ),
    (
        "sats_seen",
        "sats_seen",
        {
            "p": "sensor",
            "name": "Satellites seen",
            "state_class": "measurement",
            "entity_category": "diagnostic",
            "icon": "mdi:satellite-variant",
        },
    ),
    (
        "hdop",
        "hdop",
        {
            "p": "sensor",
            "name": "HDOP",
            "state_class": "measurement",
            "suggested_display_precision": 2,
            "entity_category": "diagnostic",
        },
    ),
    (
        "eph_m",
        "eph_m",
        {
            "p": "sensor",
            "name": "Horizontal error",
            "unit_of_measurement": METRES,
            "device_class": "distance",
            "state_class": "measurement",
            "suggested_display_precision": 1,
            "entity_category": "diagnostic",
        },
    ),
    (
        "alt_msl",
        "alt_msl_m",
        {
            "p": "sensor",
            "name": "Altitude",
            "unit_of_measurement": METRES,
            "device_class": "distance",
            "state_class": "measurement",
            "suggested_display_precision": 1,
            "entity_category": "diagnostic",
        },
    ),
    (
        "grid_square",
        "grid_square",
        {
            "p": "sensor",
            "name": "Maidenhead grid",
            "entity_category": "diagnostic",
            "icon": "mdi:grid",
        },
    ),
)

#: Components that are booleans in the state document.
_BINARY_KEYS = frozenset({"synchronized", "gps_fix"})


def object_id(settings, dev_id: str) -> str:
    """The discovery object id / device identifier: ``<prefix>_<device_id>``."""
    return f"{settings.mqtt_topic_prefix}_{dev_id}"


def topics(settings, dev_id: str) -> dict[str, str]:
    """Every topic this publisher uses, keyed by role."""
    prefix = settings.mqtt_topic_prefix
    discovery = settings.mqtt_discovery_prefix
    obj = object_id(settings, dev_id)
    return {
        "state": f"{prefix}/{dev_id}/state",
        "position": f"{prefix}/{dev_id}/position",
        "availability": f"{prefix}/{dev_id}/status",
        "discovery": f"{discovery}/device/{obj}/config",
        "tracker_discovery": f"{discovery}/device_tracker/{obj}/position/config",
        "ha_status": f"{discovery}/status",
    }


def _device_block(settings, dev_id: str, hostname: str, version: str, port: int) -> dict:
    return {
        "ids": [object_id(settings, dev_id)],
        "name": hostname,
        "mf": MANUFACTURER,
        "mdl": MODEL,
        "sw": version,
        "cu": f"http://{hostname}:{port}/",
    }


def build_discovery(settings, dev_id: str, hostname: str, version: str, port: int) -> dict:
    """The single retained device-discovery payload declaring every entity.

    Device-based discovery (HA 2024.11+) means one message creates the device and
    all of its components; the shared ``state_topic``, ``availability_topic`` and
    ``qos`` at the root are inherited by every component.
    """
    obj = object_id(settings, dev_id)
    tops = topics(settings, dev_id)
    expire = int(getattr(settings, "mqtt_expire_after_s", 180))
    components: dict[str, dict] = {}
    for key, field, spec in _COMPONENTS:
        component = dict(spec)
        component["unique_id"] = f"{obj}_{key}"
        component["value_template"] = _flag(field) if key in _BINARY_KEYS else _nullable(field)
        component["expire_after"] = expire
        components[key] = component
    return {
        "dev": _device_block(settings, dev_id, hostname, version, port),
        "o": {"name": ORIGIN_NAME, "sw": version, "url": ORIGIN_URL},
        "availability_topic": tops["availability"],
        "state_topic": tops["state"],
        "qos": int(getattr(settings, "mqtt_qos", 0)),
        "cmps": components,
    }


def build_tracker_discovery(settings, dev_id: str, hostname: str, version: str, port: int) -> dict:
    """Classic (per-component) discovery for the ``device_tracker``.

    Device trackers are not expressible in a device-discovery ``cmps`` block, so
    this one entity keeps the old single-component form and joins the same device
    through the identical ``identifiers``.
    """
    obj = object_id(settings, dev_id)
    tops = topics(settings, dev_id)
    return {
        "name": "Position",
        "unique_id": f"{obj}_position",
        "json_attributes_topic": tops["position"],
        "source_type": "gps",
        "availability_topic": tops["availability"],
        "qos": int(getattr(settings, "mqtt_qos", 0)),
        "icon": "mdi:crosshairs-gps",
        "device": {
            "identifiers": [obj],
            "name": hostname,
            "manufacturer": MANUFACTURER,
            "model": MODEL,
            "sw_version": version,
            "configuration_url": f"http://{hostname}:{port}/",
        },
    }


def dumps(payload) -> str:
    """Compact JSON for the wire (never raises on odd values)."""
    return json.dumps(payload, separators=(",", ":"), default=str)


# --------------------------------------------------------------------------
# the publisher
# --------------------------------------------------------------------------


class MqttPublisher:
    """Background task publishing :class:`~stratumtap.state.StateStore` to a broker.

    Started from the app lifespan only when ``mqtt_url`` is set. :meth:`status`
    feeds ``/api/v1/health`` so an operator can tell a misconfigured broker from
    a silent one without reading logs.
    """

    def __init__(self, settings, store, *, version: str) -> None:
        self._settings = settings
        self._store = store
        self._version = version
        self._task: asyncio.Task | None = None
        self._client = None

        self.device_id = device_id(settings)
        self.topics = topics(settings, self.device_id)
        self.hostname = settings.hostname or socket.gethostname()

        self.enabled = bool((settings.mqtt_url or "").strip())
        self.connected = False
        self.publishes = 0
        self.errors = 0
        self.last_publish_at: float | None = None
        self.last_reason: str | None = None
        self.last_error: str | None = None

        self._prev_state: dict | None = None
        self._last_position: dict | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start the publisher task; a no-op when no broker URL is configured."""
        if not self.enabled or self._task is not None:
            return
        try:
            import aiomqtt  # noqa: F401
        except ImportError:
            self.connected = False
            self.last_error = MISSING_AIOMQTT
            log.error("%s", MISSING_AIOMQTT)
            return
        try:
            parse_mqtt_url(self._settings.mqtt_url)
        except ValueError as exc:
            self.connected = False
            self.last_error = str(exc)
            log.error("MQTT disabled: %s", exc)
            return
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        """Say ``offline`` (best effort) and cancel the task."""
        task, self._task = self._task, None
        await self._say_offline()
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover - defensive
            log.debug("MQTT publisher raised on shutdown", exc_info=True)
        self.connected = False

    async def _say_offline(self) -> None:
        client = self._client
        if client is None or not self.connected:
            return
        try:
            await asyncio.wait_for(
                client.publish(self.topics["availability"], "offline", qos=self._qos, retain=True),
                SHUTDOWN_TIMEOUT_S,
            )
        except (TimeoutError, asyncio.CancelledError):
            log.debug("timed out publishing the farewell offline message")
        except Exception as exc:  # broker already gone: the LWT covers us
            log.debug("could not publish offline: %s", exc)

    # -- status ------------------------------------------------------------

    @property
    def _qos(self) -> int:
        return int(getattr(self._settings, "mqtt_qos", 0))

    def status(self) -> dict:
        """The ``mqtt`` block of ``/api/v1/health``."""
        if not self.enabled:
            return {
                "enabled": False,
                "connected": False,
                "publishes": 0,
                "errors": 0,
                "last_publish_at": None,
                "last_reason": None,
                "last_error": None,
            }
        return {
            "enabled": True,
            "connected": self.connected,
            "publishes": self.publishes,
            "errors": self.errors,
            "last_publish_at": self.last_publish_at,
            "last_reason": self.last_reason,
            "last_error": self.last_error,
        }

    # -- connection --------------------------------------------------------

    def _make_client(self):
        import aiomqtt

        target = parse_mqtt_url(self._settings.mqtt_url)
        tls_context = None
        if target.tls:
            tls_context = ssl.create_default_context()
            if self._settings.mqtt_tls_insecure:
                tls_context.check_hostname = False
                tls_context.verify_mode = ssl.CERT_NONE
        return aiomqtt.Client(
            hostname=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            identifier=client_id(self._settings),
            will=aiomqtt.Will(
                topic=self.topics["availability"],
                payload="offline",
                qos=self._qos,
                retain=True,
            ),
            tls_context=tls_context,
            keepalive=60,
        )

    async def _run(self) -> None:
        """Connect, serve, reconnect — forever, and never raise."""
        import aiomqtt

        backoff = BACKOFF_MIN_S
        while True:
            had_connected = False
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except (aiomqtt.MqttError, OSError, ValueError) as exc:
                had_connected = self.connected
                self.errors += 1
                self.last_error = str(exc) or type(exc).__name__
                # Noisy once per state change, quiet while a dead broker is retried.
                if had_connected or backoff <= BACKOFF_MIN_S:
                    log.warning(
                        "MQTT connection to %s lost/failed (%s); retrying",
                        self._settings.mqtt_url,
                        self.last_error,
                    )
                else:
                    log.debug("MQTT retry in %.0fs: %s", backoff, self.last_error)
            except Exception as exc:  # pragma: no cover - must never die
                had_connected = self.connected
                self.errors += 1
                self.last_error = str(exc) or type(exc).__name__
                log.exception("MQTT publisher failed unexpectedly")
            finally:
                if self.connected:
                    log.info("MQTT disconnected from %s", self._settings.mqtt_url)
                self.connected = False
                self._client = None
            # A session that actually worked earns a fresh, short backoff.
            if had_connected:
                backoff = BACKOFF_MIN_S
            await asyncio.sleep(backoff)
            backoff = min(BACKOFF_MAX_S, backoff * 2)

    async def _session(self) -> None:
        """One broker connection: announce, subscribe, then publish until it drops."""
        async with self._make_client() as client:
            self._client = client
            self.connected = True
            self.last_error = None
            self._prev_state = None
            self.last_publish_at = None
            self._last_position = None
            log.info(
                "MQTT connected to %s as %s (device %s)",
                self._settings.mqtt_url,
                client_id(self._settings),
                self.device_id,
            )
            await self._announce(client)
            await client.subscribe(self.topics["ha_status"], qos=self._qos)

            listener = asyncio.ensure_future(self._listen(client))
            ticker = asyncio.ensure_future(self._ticker(client))
            try:
                await asyncio.gather(listener, ticker)
            finally:
                for task in (listener, ticker):
                    task.cancel()
                await asyncio.gather(listener, ticker, return_exceptions=True)

    async def _announce(self, client) -> None:
        """Availability + both discovery messages, all retained."""
        await client.publish(self.topics["availability"], "online", qos=self._qos, retain=True)
        port = int(getattr(self._settings, "port", 8080))
        discovery = build_discovery(
            self._settings, self.device_id, self.hostname, self._version, port
        )
        tracker = build_tracker_discovery(
            self._settings, self.device_id, self.hostname, self._version, port
        )
        await client.publish(self.topics["discovery"], dumps(discovery), qos=1, retain=True)
        await client.publish(self.topics["tracker_discovery"], dumps(tracker), qos=1, retain=True)

    # -- the two concurrent halves ----------------------------------------

    async def _listen(self, client) -> None:
        """Re-announce whenever Home Assistant says it came back up.

        HA drops its non-retained entity registry on restart and republishes
        ``homeassistant/status: online``; without this the device would only
        reappear when the retained discovery message happens to be re-read.
        """
        async for message in client.messages:
            payload = message.payload
            if isinstance(payload, (bytes, bytearray)):
                text = payload.decode("utf-8", errors="replace")
            else:  # pragma: no cover - paho always hands us bytes
                text = str(payload)
            if text.strip().lower() != "online":
                continue
            log.info("Home Assistant came online; republishing MQTT discovery")
            try:
                await self._announce(client)
                await self._publish_state(client, time.time(), "interval", force_position=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - covered by the session retry
                self.errors += 1
                self.last_error = str(exc)
                raise

    async def _ticker(self, client) -> None:
        """Evaluate :func:`should_publish` once a second and publish when it says so."""
        while True:
            now = time.time()
            state = build_state(self._store, now)
            publish, reason = should_publish(
                self._prev_state, state, self.last_publish_at, now, self._settings
            )
            if publish:
                await self._publish_state(client, now, reason, state=state)
            await asyncio.sleep(TICK_S)

    async def _publish_state(
        self,
        client,
        now: float,
        reason: str,
        *,
        state: dict | None = None,
        force_position: bool = False,
    ) -> None:
        if state is None:
            state = build_state(self._store, now)
        retain = bool(getattr(self._settings, "mqtt_retain_state", True))
        await client.publish(self.topics["state"], dumps(state), qos=self._qos, retain=retain)
        self.publishes += 1
        self.last_publish_at = now
        self.last_reason = reason
        self._prev_state = state

        position = build_position(self._store)
        if position is None:
            return
        moved = position_distance_m(self._last_position, position) > POSITION_EPSILON_M
        if force_position or reason == "interval" or moved:
            await client.publish(
                self.topics["position"], dumps(position), qos=self._qos, retain=True
            )
            self._last_position = position
