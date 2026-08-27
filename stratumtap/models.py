"""Pydantic response models — the single source of truth for ``docs/api-contract.md``.

Every response model forbids extra fields so that a drift between the code and
the documented contract shows up as a test failure rather than as a silently
extra key.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


class ApiModel(BaseModel):
    """Base class for every model that appears in an API response."""

    model_config = _STRICT


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------


class ServerInfo(ApiModel):
    """Timing/identity block attached to every JSON response."""

    t_recv: float
    t_send: float
    t0: float | None = None
    hostname: str
    version: str
    demo: bool
    uptime_s: float


# --------------------------------------------------------------------------
# chrony / NTP
# --------------------------------------------------------------------------


class NtpSnapshot(ApiModel):
    """Parsed ``chronyc tracking`` state."""

    available: bool = False
    error: str | None = None
    collected_at: float | None = None
    age_s: float | None = None

    reference_id: str | None = None
    reference_name: str | None = None
    stratum: int | None = None
    ref_time: str | None = None
    ref_time_unix: float | None = None
    system_offset_s: float | None = None
    last_offset_s: float | None = None
    rms_offset_s: float | None = None
    frequency_ppm: float | None = None
    residual_freq_ppm: float | None = None
    skew_ppm: float | None = None
    root_delay_s: float | None = None
    root_dispersion_s: float | None = None
    update_interval_s: float | None = None
    leap_status: str | None = None
    synchronized: bool = False
    raw: str | None = None


class NtpSource(ApiModel):
    """One row of ``chronyc -c sources``."""

    mode: str | None = None
    mode_text: str | None = None
    state: str | None = None
    state_text: str | None = None
    name: str | None = None
    stratum: int | None = None
    poll: int | None = None
    poll_interval_s: int | float | None = None
    reach: int | None = None
    reach_octal: str | None = None
    last_rx_s: float | None = None
    last_sample_offset_s: float | None = None
    last_sample_adjusted_offset_s: float | None = None
    last_sample_error_s: float | None = None


class NtpSourceStat(ApiModel):
    """One row of ``chronyc -c sourcestats``."""

    name: str | None = None
    np: int | None = None
    nr: int | None = None
    span_s: float | None = None
    frequency_ppm: float | None = None
    freq_skew_ppm: float | None = None
    offset_s: float | None = None
    std_dev_s: float | None = None


class NtpSources(ApiModel):
    """``chronyc sources`` + ``sourcestats``."""

    available: bool = False
    error: str | None = None
    collected_at: float | None = None
    age_s: float | None = None
    sources: list[NtpSource] = []
    sourcestats: list[NtpSourceStat] = []
    raw_sources: str | None = None
    raw_sourcestats: str | None = None


# --------------------------------------------------------------------------
# gpsd / GPS
# --------------------------------------------------------------------------


class GpsDevice(ApiModel):
    """One device gpsd knows about."""

    path: str | None = None
    driver: str | None = None
    subtype: str | None = None
    activated: str | None = None
    bps: int | None = None
    cycle_s: float | None = None


class GpsFix(ApiModel):
    """Fix mode/status/time from ``TPV``."""

    mode: int | None = None
    mode_text: str | None = None
    status: int | None = None
    status_text: str | None = None
    fix_text: str | None = None
    fix_age_s: float | None = None
    time: str | None = None
    time_unix: float | None = None
    time_age_s: float | None = None
    ept_s: float | None = None
    leapseconds: int | None = None


class GpsPosition(ApiModel):
    """Position from ``TPV``."""

    lat: float | None = None
    lon: float | None = None
    alt_hae_m: float | None = None
    alt_msl_m: float | None = None
    geoid_sep_m: float | None = None
    grid_square: str | None = None


class GpsMotion(ApiModel):
    """Course over ground from ``TPV``."""

    speed_mps: float | None = None
    track_deg: float | None = None
    mag_track_deg: float | None = None
    mag_var_deg: float | None = None
    climb_mps: float | None = None


class GpsAccuracy(ApiModel):
    """Error estimates from ``TPV``."""

    epx_m: float | None = None
    epy_m: float | None = None
    epv_m: float | None = None
    eph_m: float | None = None
    sep_m: float | None = None
    eps_mps: float | None = None
    epd_deg: float | None = None
    epc_mps: float | None = None
    ept_s: float | None = None


class GpsDop(ApiModel):
    """Dilution-of-precision values from ``SKY``."""

    xdop: float | None = None
    ydop: float | None = None
    vdop: float | None = None
    hdop: float | None = None
    pdop: float | None = None
    tdop: float | None = None
    gdop: float | None = None


class GpsEcef(ApiModel):
    """Earth-centered, earth-fixed position/velocity from ``TPV``."""

    x_m: float | None = None
    y_m: float | None = None
    z_m: float | None = None
    vx_mps: float | None = None
    vy_mps: float | None = None
    vz_mps: float | None = None
    p_acc_m: float | None = None
    v_acc_mps: float | None = None


class GpsGst(ApiModel):
    """NMEA GST pseudorange-noise statistics (gpsd ``GST``)."""

    time_unix: float | None = None
    rms_m: float | None = None
    major_m: float | None = None
    minor_m: float | None = None
    orient_deg: float | None = None
    lat_err_m: float | None = None
    lon_err_m: float | None = None
    alt_err_m: float | None = None


class GpsTimeOffset(ApiModel):
    """System-clock vs GPS-time offset from ``PPS``/``TOFF``."""

    source: str | None = None
    offset_s: float | None = None
    real_s: float | None = None
    clock_s: float | None = None
    precision: int | None = None
    measured_at: float | None = None
    pps_offset_s: float | None = None
    toff_offset_s: float | None = None


class Satellite(ApiModel):
    """One entry of the ``SKY.satellites`` array."""

    gnss: str | None = None
    gnss_name: str | None = None
    gnssid: int | None = None
    svid: int | None = None
    prn: int | None = None
    sigid: int | None = None
    el_deg: float | None = None
    az_deg: float | None = None
    snr_db: float | None = None
    used: bool | None = None
    health: int | None = None


# Module-level alias so that the field literally named ``list`` (as the contract
# requires) does not shadow the builtin while its annotation is evaluated.
SatelliteList = list[Satellite]


class Satellites(ApiModel):
    """Satellite counts plus the per-satellite list."""

    seen: int | None = None
    used: int | None = None
    collected_at: float | None = None
    list: SatelliteList = []


class GpsSnapshot(ApiModel):
    """Everything gpsd told us, regrouped per ``docs/api-contract.md``."""

    available: bool = False
    error: str | None = None
    connected: bool = False
    collected_at: float | None = None
    age_s: float | None = None
    gpsd_version: str | None = None
    device: GpsDevice = GpsDevice()
    devices: list[GpsDevice] = []
    fix: GpsFix = GpsFix()
    position: GpsPosition = GpsPosition()
    motion: GpsMotion = GpsMotion()
    accuracy: GpsAccuracy = GpsAccuracy()
    dop: GpsDop = GpsDop()
    ecef: GpsEcef = GpsEcef()
    time_offset: GpsTimeOffset = GpsTimeOffset()
    gst: GpsGst | None = None
    satellites: Satellites = Satellites()
    cgps_time_offset_text: str | None = None


# --------------------------------------------------------------------------
# endpoint envelopes
# --------------------------------------------------------------------------


class TimeResponse(ApiModel):
    """``GET /api/v1/time``."""

    server: ServerInfo
    ntp_synchronized: bool
    ntp_system_offset_s: float | None = None
    ntp_stratum: int | None = None


class StatusResponse(ApiModel):
    """``GET /api/v1/status``."""

    server: ServerInfo
    ntp: NtpSnapshot
    gps: GpsSnapshot


class NtpResponse(ApiModel):
    """``GET /api/v1/ntp``."""

    server: ServerInfo
    ntp: NtpSnapshot


class NtpSourcesResponse(ApiModel):
    """``GET /api/v1/ntp/sources``."""

    server: ServerInfo
    ntp_sources: NtpSources


class GpsResponse(ApiModel):
    """``GET /api/v1/gps``."""

    server: ServerInfo
    gps: GpsSnapshot


class SatellitesResponse(ApiModel):
    """``GET /api/v1/gps/satellites``."""

    server: ServerInfo
    satellites: Satellites


class HistoryResponse(ApiModel):
    """``GET /api/v1/history``."""

    server: ServerInfo
    interval_s: float
    requested_seconds: float
    points: int
    columns: list[str]
    rows: list[list[int | float | None]]


class MqttStatus(ApiModel):
    """The ``mqtt`` block of ``GET /api/v1/health``.

    ``enabled`` is false (and everything else zero/null) unless
    ``STRATUMTAP_MQTT_URL`` is set. ``enabled`` with ``connected: false`` and a
    ``last_error`` is exactly the "broker misconfigured" case.
    """

    enabled: bool = False
    connected: bool = False
    publishes: int = 0
    errors: int = 0
    last_publish_at: float | None = None
    last_reason: str | None = None
    last_error: str | None = None


class HealthResponse(ApiModel):
    """``GET /api/v1/health``."""

    ok: bool
    ntp_ok: bool
    gpsd_connected: bool
    gps_fix: bool
    #: Largest event-loop scheduling overshoot over the last 60 s.
    loop_lag_ms: float = 0.0
    #: Currently connected SSE clients.
    stream_clients: int = 0
    #: Optional MQTT publisher state.
    mqtt: MqttStatus = MqttStatus()
    server: ServerInfo


class NmeaLine(ApiModel):
    """One raw NMEA sentence as received from gpsd."""

    t: float
    line: str
    type: str | None = None
    talker: str | None = None
    checksum_ok: bool | None = None


class RawNmeaResponse(ApiModel):
    """``GET /api/v1/raw/nmea`` — the tail of the raw NMEA ring buffer."""

    count: int
    ring_size: int
    rate_per_s: float
    lines: list[NmeaLine] = Field(default_factory=list)
    server: ServerInfo


class ConfigResponse(ApiModel):
    """``GET /api/v1/config`` — non-secret UI configuration."""

    server: ServerInfo
    default_refresh_s: int
    refresh_choices_s: list[int]
    tile_url: str
    tile_attribution: str
    hostname: str
    demo: bool
    history_interval_s: float
    history_size: int
    version: str
