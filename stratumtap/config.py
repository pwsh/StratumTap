"""Application settings (pydantic-settings, env prefix ``STRATUMTAP_``)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_ints(value: str) -> list[int]:
    out: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


def _split_strs(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


class Settings(BaseSettings):
    """Runtime configuration. Every field is settable as ``STRATUMTAP_<FIELD>``."""

    model_config = SettingsConfigDict(
        env_prefix="STRATUMTAP_",
        env_file=None,
        extra="ignore",
        case_sensitive=False,
    )

    # HTTP server
    host: str = "0.0.0.0"
    port: int = 8080

    # gpsd
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947

    # chrony
    chronyc_bin: str = "chronyc"
    chrony_poll_s: float = 1.0
    sources_poll_s: float = 10.0

    # history ring buffer
    history_interval_s: float = 5.0
    history_size: int = 17280

    # streaming (SSE fan-out + raw NMEA ring)
    stream_max_clients: int = 16
    stream_queue: int = 500
    nmea_ring: int = 1000

    # UI
    default_refresh_s: int = 2
    refresh_choices_s: str = "1,2,5,10,30,60"

    # demo mode
    demo: bool = False
    demo_lat: float = 51.4779  # Royal Observatory, Greenwich
    demo_lon: float = -0.0015

    # map
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    tile_attribution: str = "© OpenStreetMap contributors"

    # MQTT publisher (Home Assistant discovery); empty URL = disabled
    #: ``mqtt://host``, ``mqtt://user:pass@host:1883`` or ``mqtts://host:8883``.
    mqtt_url: str = ""
    mqtt_topic_prefix: str = "stratumtap"
    mqtt_discovery_prefix: str = "homeassistant"
    #: Empty = derived from ``/etc/machine-id`` (or the hostname).
    mqtt_device_id: str = ""
    #: Empty = ``stratumtap-<device_id>``.
    mqtt_client_id: str = ""
    #: Floor: publish at least this often even when nothing changed.
    mqtt_interval_s: float = 60.0
    #: Ceiling: never publish more often than this, however fast things change.
    mqtt_min_interval_s: float = 5.0
    mqtt_deadband_offset_us: float = 50.0
    mqtt_deadband_ppm: float = 0.5
    mqtt_expire_after_s: int = 180
    mqtt_retain_state: bool = True
    mqtt_tls_insecure: bool = False
    mqtt_qos: int = 0

    # misc
    cors_origins: str = ""
    log_level: str = "info"
    #: Display name shown in the UI/API instead of the machine hostname (empty = hostname).
    hostname: str = ""

    @property
    def refresh_choices(self) -> list[int]:
        """``refresh_choices_s`` parsed into a list of integers."""
        return _split_ints(self.refresh_choices_s)

    @property
    def cors_origin_list(self) -> list[str]:
        """``cors_origins`` parsed into a list of origins (empty = CORS disabled)."""
        return _split_strs(self.cors_origins)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings`."""
    return Settings()
