"""The optional MQTT publisher: URL parsing, state flattening, discovery, throttling.

The throttling matrix is the part worth being pedantic about — it is the whole
argument for pushing instead of polling, so the floor interval, the minimum
interval and each deadband get their own test. The last two tests exercise the
real run loop: once against a fake client (fast, deterministic) and once against
an in-process `amqtt` broker with a real `aiomqtt` subscriber, which is the only
way to prove the retained/LWT semantics actually work.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket

import pytest

from stratumtap import mqtt as mqtt_mod
from stratumtap.config import Settings
from stratumtap.demo import DemoSource
from stratumtap.mqtt import (
    MqttPublisher,
    build_discovery,
    build_position,
    build_state,
    build_tracker_discovery,
    client_id,
    device_id,
    parse_mqtt_url,
    should_publish,
    significant_change,
    topics,
)
from stratumtap.state import StateStore
from tests.conftest import wait_for


def make_settings(**overrides) -> Settings:
    base = {"_env_file": None, "demo": True, "hostname": "stratum1"}
    base.update(overrides)
    return Settings(**base)


def demo_store(settings: Settings | None = None) -> StateStore:
    """A store filled with one tick of demo data (no background task)."""
    settings = settings or make_settings()
    store = StateStore(settings)
    DemoSource(settings, store).tick()
    return store


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------
# URL parsing
# --------------------------------------------------------------------------


def test_plain_url_defaults_to_1883():
    target = parse_mqtt_url("mqtt://broker.lan")
    assert (target.host, target.port, target.tls) == ("broker.lan", 1883, False)
    assert target.username is None and target.password is None


def test_tls_url_defaults_to_8883():
    target = parse_mqtt_url("mqtts://broker.lan")
    assert (target.host, target.port, target.tls) == ("broker.lan", 8883, True)


def test_explicit_port_wins_over_the_scheme_default():
    assert parse_mqtt_url("mqtt://broker.lan:1884").port == 1884
    assert parse_mqtt_url("mqtts://broker.lan:8884").port == 8884


def test_credentials_are_parsed_and_percent_decoded():
    target = parse_mqtt_url("mqtt://ha:p%40ss%2Fword@broker.lan:1883")
    assert target.username == "ha"
    assert target.password == "p@ss/word"
    assert (target.host, target.port) == ("broker.lan", 1883)


def test_username_without_password():
    target = parse_mqtt_url("mqtt://ha@broker.lan")
    assert target.username == "ha"
    assert target.password is None


def test_ipv6_host_is_unbracketed():
    assert parse_mqtt_url("mqtt://[fd00::1]:1883").host == "fd00::1"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "broker.lan",
        "broker.lan:1883",
        "http://broker.lan",
        "ws://broker.lan",
        "mqtt://",
        "mqtt://broker.lan:notaport",
    ],
)
def test_bad_urls_raise_value_error(url):
    with pytest.raises(ValueError):
        parse_mqtt_url(url)


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------


def test_device_id_is_stable_and_hex():
    settings = make_settings()
    first = device_id(settings)
    assert first == device_id(settings)
    assert len(first) == 12
    int(first, 16)  # raises if it is not hex


def test_configured_device_id_wins():
    assert device_id(make_settings(mqtt_device_id="  pi4  ")) == "pi4"


def test_client_id_defaults_to_the_device_id():
    settings = make_settings(mqtt_device_id="pi4")
    assert client_id(settings) == "stratumtap-pi4"
    assert client_id(make_settings(mqtt_client_id="custom")) == "custom"


def test_device_id_falls_back_to_the_hostname(monkeypatch, tmp_path):
    monkeypatch.setattr(mqtt_mod, "MACHINE_ID_PATH", str(tmp_path / "nope"))
    monkeypatch.setattr(mqtt_mod.socket, "gethostname", lambda: "hostA")
    first = device_id(make_settings())
    monkeypatch.setattr(mqtt_mod.socket, "gethostname", lambda: "hostB")
    assert device_id(make_settings()) != first


def test_topics_layout():
    settings = make_settings(mqtt_topic_prefix="st", mqtt_discovery_prefix="ha")
    tops = topics(settings, "abc123")
    assert tops["state"] == "st/abc123/state"
    assert tops["position"] == "st/abc123/position"
    assert tops["availability"] == "st/abc123/status"
    assert tops["discovery"] == "ha/device/st_abc123/config"
    assert tops["tracker_discovery"] == "ha/device_tracker/st_abc123/position/config"
    assert tops["ha_status"] == "ha/status"


# --------------------------------------------------------------------------
# state flattening
# --------------------------------------------------------------------------


def test_state_is_flat_and_json_serialisable():
    state = build_state(demo_store(), now=1_756_000_000.0)
    assert json.loads(json.dumps(state)) == state
    assert not any(isinstance(v, (dict, list)) for v in state.values())
    assert state["updated"].endswith("Z")


def test_state_carries_the_demo_numbers():
    state = build_state(demo_store())
    assert state["ntp_available"] is True
    assert state["gps_available"] is True
    assert state["stratum"] == 1
    assert state["synchronized"] is True
    assert state["gps_fix"] is True
    assert state["fix_mode"] >= 2
    assert state["sats_used"] > 0
    assert state["lat"] is not None and state["lon"] is not None
    assert state["grid_square"]
    assert state["gpsd_connected"] is True


def test_offsets_are_microseconds_rounded_to_three():
    store = demo_store()
    state = build_state(store)
    assert state["system_offset_us"] == round(store.ntp.system_offset_s * 1e6, 3)
    assert state["rms_offset_us"] == round(store.ntp.rms_offset_s * 1e6, 3)
    # Rounded to 3 decimals: microsecond values with picosecond noise are noise.
    assert state["system_offset_us"] == round(state["system_offset_us"], 3)


def test_pps_offset_only_when_the_source_is_pps():
    store = demo_store()
    state = build_state(store)
    if store.gps.time_offset.source == "PPS":
        assert state["pps_offset_us"] == pytest.approx(
            store.gps.time_offset.offset_s * 1e6, rel=1e-9
        )
    store.gps = store.gps.model_copy(
        update={"time_offset": store.gps.time_offset.model_copy(update={"source": "TOFF"})}
    )
    assert build_state(store)["pps_offset_us"] is None


def test_empty_store_is_all_nulls():
    state = build_state(StateStore(make_settings()))
    assert state["ntp_available"] is False
    assert state["gps_available"] is False
    assert state["synchronized"] is False
    assert state["gps_fix"] is False
    assert state["gpsd_connected"] is False
    for field in ("system_offset_us", "stratum", "reference", "lat", "hdop", "fix_text"):
        assert state[field] is None


def test_position_payload():
    position = build_position(demo_store())
    assert position is not None
    assert -90 <= position["latitude"] <= 90
    assert -180 <= position["longitude"] <= 180
    assert "gps_accuracy" in position and "altitude" in position


def test_position_is_none_without_a_fix():
    assert build_position(StateStore(make_settings())) is None


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------


def discovery() -> dict:
    return build_discovery(make_settings(), "abc123", "stratum1", "0.1.0", 8080)


def test_discovery_has_the_shared_root_keys():
    payload = discovery()
    assert payload["state_topic"] == "stratumtap/abc123/state"
    assert payload["availability_topic"] == "stratumtap/abc123/status"
    assert payload["qos"] == 0
    assert payload["dev"]["ids"] == ["stratumtap_abc123"]
    assert payload["dev"]["name"] == "stratum1"
    assert payload["dev"]["mf"] == "StratumTap"
    assert payload["dev"]["sw"] == "0.1.0"
    assert payload["dev"]["cu"] == "http://stratum1:8080/"
    assert payload["o"]["name"] == "StratumTap"
    assert payload["o"]["url"].startswith("https://github.com/")


def test_every_component_is_well_formed():
    payload = discovery()
    components = payload["cmps"]
    assert len(components) >= 20
    unique_ids = []
    for key, component in components.items():
        assert component["p"] in ("sensor", "binary_sensor"), key
        assert component["value_template"], key
        assert component["name"], key
        assert component["expire_after"] == 180, key
        assert component["unique_id"] == f"stratumtap_abc123_{key}", key
        unique_ids.append(component["unique_id"])
    assert len(unique_ids) == len(set(unique_ids))


def test_expected_components_are_present_with_their_units():
    components = discovery()["cmps"]
    for key in (
        "system_offset",
        "pps_offset",
        "last_offset",
        "rms_offset",
        "frequency",
        "skew",
        "root_dispersion",
        "stratum",
        "reference",
        "ref_time",
        "leap_status",
        "synchronized",
        "gps_fix",
        "fix_text",
        "sats_used",
        "sats_seen",
        "hdop",
        "eph_m",
        "alt_msl",
        "grid_square",
    ):
        assert key in components

    assert components["system_offset"]["unit_of_measurement"] == "µs"
    assert components["system_offset"]["device_class"] == "duration"
    assert components["system_offset"]["state_class"] == "measurement"
    assert components["system_offset"]["suggested_display_precision"] == 3
    assert components["frequency"]["unit_of_measurement"] == "ppm"
    assert components["frequency"]["icon"] == "mdi:sine-wave"
    assert components["eph_m"]["device_class"] == "distance"
    assert components["ref_time"]["device_class"] == "timestamp"
    assert components["synchronized"]["p"] == "binary_sensor"
    assert components["synchronized"]["device_class"] == "connectivity"
    assert components["gps_fix"]["p"] == "binary_sensor"
    assert components["pps_offset"]["entity_category"] == "diagnostic"


def test_binary_templates_render_on_off():
    components = discovery()["cmps"]
    for key in ("synchronized", "gps_fix"):
        template = components[key]["value_template"]
        assert "ON" in template and "OFF" in template
        assert f"value_json.{key}" in template


def test_discovery_is_json_serialisable():
    payload = discovery()
    assert json.loads(json.dumps(payload)) == payload


def test_tracker_discovery():
    payload = build_tracker_discovery(make_settings(), "abc123", "stratum1", "0.1.0", 8080)
    assert payload["name"] == "Position"
    assert payload["unique_id"] == "stratumtap_abc123_position"
    assert payload["source_type"] == "gps"
    assert payload["json_attributes_topic"] == "stratumtap/abc123/position"
    assert payload["availability_topic"] == "stratumtap/abc123/status"
    # Same identifiers => HA attaches the tracker to the very same device.
    assert payload["device"]["identifiers"] == ["stratumtap_abc123"]
    assert json.loads(json.dumps(payload)) == payload


# --------------------------------------------------------------------------
# throttling
# --------------------------------------------------------------------------


THROTTLE = dict(
    mqtt_interval_s=60.0,
    mqtt_min_interval_s=5.0,
    mqtt_deadband_offset_us=50.0,
    mqtt_deadband_ppm=0.5,
)


def base_state(**overrides) -> dict:
    state = {
        "system_offset_us": 10.0,
        "frequency_ppm": 17.0,
        "stratum": 1,
        "synchronized": True,
        "gps_fix": True,
        "fix_mode": 3,
        "reference": "GPS",
        "leap_status": "Normal",
        "ntp_available": True,
        "gps_available": True,
        "sats_used": 9,
    }
    state.update(overrides)
    return state


def test_first_publish_is_immediate():
    settings = make_settings(**THROTTLE)
    assert should_publish(None, base_state(), None, 1000.0, settings) == (True, "interval")


def test_the_floor_interval_publishes_even_when_nothing_changed():
    settings = make_settings(**THROTTLE)
    state = base_state()
    assert should_publish(state, state, 1000.0, 1059.9, settings) == (False, "")
    assert should_publish(state, state, 1000.0, 1060.0, settings) == (True, "interval")


def test_the_min_interval_gates_even_a_huge_change():
    settings = make_settings(**THROTTLE)
    prev = base_state()
    new = base_state(system_offset_us=100_000.0, stratum=4, synchronized=False)
    assert should_publish(prev, new, 1000.0, 1004.9, settings) == (False, "")
    ok, reason = should_publish(prev, new, 1000.0, 1005.0, settings)
    assert ok and reason == "change:system_offset_us"


def test_the_offset_deadband():
    settings = make_settings(**THROTTLE)
    prev = base_state(system_offset_us=0.0)
    inside = base_state(system_offset_us=50.0)  # exactly the deadband: not "more than"
    outside = base_state(system_offset_us=50.1)
    assert should_publish(prev, inside, 1000.0, 1010.0, settings) == (False, "")
    assert should_publish(prev, outside, 1000.0, 1010.0, settings) == (
        True,
        "change:system_offset_us",
    )


def test_the_frequency_deadband():
    settings = make_settings(**THROTTLE)
    prev = base_state(frequency_ppm=17.0)
    assert should_publish(prev, base_state(frequency_ppm=17.5), 1000.0, 1010.0, settings) == (
        False,
        "",
    )
    assert should_publish(prev, base_state(frequency_ppm=17.6), 1000.0, 1010.0, settings) == (
        True,
        "change:frequency_ppm",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stratum", 2),
        ("synchronized", False),
        ("gps_fix", False),
        ("fix_mode", 2),
        ("reference", "PPS"),
        ("leap_status", "Insert second"),
        ("ntp_available", False),
        ("gps_available", False),
        ("sats_used", 8),
    ],
)
def test_every_flag_change_triggers_a_publish(field, value):
    settings = make_settings(**THROTTLE)
    prev = base_state()
    ok, reason = should_publish(prev, base_state(**{field: value}), 1000.0, 1010.0, settings)
    assert ok and reason == f"change:{field}"


def test_a_value_appearing_or_disappearing_counts_as_a_change():
    settings = make_settings(**THROTTLE)
    assert significant_change(base_state(system_offset_us=None), base_state(), settings) == (
        "system_offset_us"
    )
    assert significant_change(base_state(), base_state(system_offset_us=None), settings) == (
        "system_offset_us"
    )


def test_no_previous_state_means_no_significant_change():
    assert significant_change(None, base_state(), make_settings(**THROTTLE)) is None
    assert significant_change({}, base_state(), make_settings(**THROTTLE)) is None


# --------------------------------------------------------------------------
# the run loop, against a fake client
# --------------------------------------------------------------------------


class FakeMessage:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


class FakeClient:
    """Minimal stand-in for ``aiomqtt.Client`` recording every publish."""

    instances: list[FakeClient] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.published: list[tuple[str, str, int, bool]] = []
        self.subscribed: list[str] = []
        self._inbox: asyncio.Queue = asyncio.Queue()
        FakeClient.instances.append(self)

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def publish(self, topic, payload=None, qos=0, retain=False, **kwargs) -> None:
        text = payload.decode() if isinstance(payload, (bytes, bytearray)) else str(payload)
        self.published.append((str(topic), text, qos, retain))

    async def subscribe(self, topic, qos=0, **kwargs) -> None:
        self.subscribed.append(str(topic))

    @property
    def messages(self):
        return self._iterate()

    async def _iterate(self):
        while True:
            yield await self._inbox.get()

    def deliver(self, topic: str, payload: bytes) -> None:
        self._inbox.put_nowait(FakeMessage(topic, payload))

    def topics_published(self) -> list[str]:
        return [topic for topic, _, _, _ in self.published]


@pytest.fixture
def fake_client(monkeypatch):
    import aiomqtt

    FakeClient.instances.clear()
    monkeypatch.setattr(aiomqtt, "Client", FakeClient)
    yield FakeClient
    FakeClient.instances.clear()


async def test_run_loop_announces_subscribes_and_publishes(fake_client):
    settings = make_settings(
        mqtt_url="mqtt://broker.lan",
        mqtt_device_id="abc123",
        mqtt_interval_s=1.0,
        mqtt_min_interval_s=0.1,
    )
    publisher = MqttPublisher(settings, demo_store(settings), version="9.9.9")
    await publisher.start()
    try:
        await wait_for(lambda: fake_client.instances and publisher.publishes > 0)
        client = fake_client.instances[0]

        assert publisher.connected is True
        assert client.subscribed == ["homeassistant/status"]
        assert client.kwargs["hostname"] == "broker.lan"
        assert client.kwargs["port"] == 1883
        assert client.kwargs["identifier"] == "stratumtap-abc123"
        will = client.kwargs["will"]
        assert (will.topic, will.retain) == ("stratumtap/abc123/status", True)
        assert will.payload == "offline"

        published = dict((topic, payload) for topic, payload, _, _ in client.published)
        assert published["stratumtap/abc123/status"] == "online"

        discovery_payload = json.loads(published["homeassistant/device/stratumtap_abc123/config"])
        assert "cmps" in discovery_payload
        tracker = json.loads(
            published["homeassistant/device_tracker/stratumtap_abc123/position/config"]
        )
        assert tracker["source_type"] == "gps"

        state = json.loads(published["stratumtap/abc123/state"])
        assert state["stratum"] == 1
        assert json.loads(published["stratumtap/abc123/position"])["latitude"] is not None
        assert publisher.last_reason == "interval"

        # Both discovery messages are retained at qos 1 so a late HA still sees them.
        for topic, _, qos, retain in client.published:
            if topic.startswith("homeassistant/"):
                assert (qos, retain) == (1, True)

        # Home Assistant restarting re-announces everything.
        before = len(client.published)
        client.deliver("homeassistant/status", b"online")
        await wait_for(lambda: len(client.published) > before + 3)
        after = client.topics_published()[before:]
        assert "homeassistant/device/stratumtap_abc123/config" in after
        assert "stratumtap/abc123/status" in after
        assert "stratumtap/abc123/state" in after
    finally:
        await publisher.stop()

    assert publisher.connected is False
    assert fake_client.instances[0].published[-1] == (
        "stratumtap/abc123/status",
        "offline",
        0,
        True,
    )


async def test_a_broker_that_refuses_is_counted_and_retried_not_raised(monkeypatch):
    import aiomqtt

    attempts = 0

    class RefusingClient(FakeClient):
        async def __aenter__(self):
            nonlocal attempts
            attempts += 1
            raise aiomqtt.MqttError("Connection Refused: not authorised")

    monkeypatch.setattr(mqtt_mod, "BACKOFF_MIN_S", 0.01)
    monkeypatch.setattr(aiomqtt, "Client", RefusingClient)
    settings = make_settings(mqtt_url="mqtt://broker.lan", mqtt_device_id="abc123")
    publisher = MqttPublisher(settings, demo_store(settings), version="9.9.9")
    await publisher.start()
    try:
        await wait_for(lambda: publisher.errors >= 2)
    finally:
        await publisher.stop()

    # The task retried instead of dying, and said so in /health.
    assert attempts >= 2
    assert publisher.status() == {
        "enabled": True,
        "connected": False,
        "publishes": 0,
        "errors": publisher.errors,
        "last_publish_at": None,
        "last_reason": None,
        "last_error": "Connection Refused: not authorised",
    }


async def test_status_block_reports_disabled_when_no_url():
    publisher = MqttPublisher(make_settings(), demo_store(), version="9.9.9")
    await publisher.start()
    assert publisher.status() == {
        "enabled": False,
        "connected": False,
        "publishes": 0,
        "errors": 0,
        "last_publish_at": None,
        "last_reason": None,
        "last_error": None,
    }
    await publisher.stop()


async def test_a_bad_url_disables_the_publisher_without_crashing():
    settings = make_settings(mqtt_url="not-a-url")
    publisher = MqttPublisher(settings, demo_store(), version="9.9.9")
    await publisher.start()
    status = publisher.status()
    assert status["enabled"] is True
    assert status["connected"] is False
    assert status["last_error"]
    await publisher.stop()


async def test_missing_aiomqtt_logs_once_and_keeps_running(monkeypatch, caplog):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "aiomqtt":
            raise ImportError("no module named aiomqtt")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    settings = make_settings(mqtt_url="mqtt://broker.lan")
    publisher = MqttPublisher(settings, demo_store(settings), version="9.9.9")
    with caplog.at_level("ERROR"):
        await publisher.start()
    monkeypatch.undo()

    status = publisher.status()
    assert status["enabled"] is True
    assert status["connected"] is False
    assert "stratumtap[mqtt]" in status["last_error"]
    assert any("stratumtap[mqtt]" in record.message for record in caplog.records)
    await publisher.stop()


async def test_health_endpoint_exposes_the_mqtt_block():
    import httpx

    from stratumtap.app import create_app

    app = create_app(make_settings())
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        response = await client.get("/api/v1/health")
    payload = response.json()
    assert payload["mqtt"] == {
        "enabled": False,
        "connected": False,
        "publishes": 0,
        "errors": 0,
        "last_publish_at": None,
        "last_reason": None,
        "last_error": None,
    }


# --------------------------------------------------------------------------
# end to end against a real broker
# --------------------------------------------------------------------------


try:  # amqtt is a dev-only dependency; its absence skips the broker test, never fails it.
    from amqtt.broker import Broker as AmqttBroker
except Exception:  # pragma: no cover - depends on the dev environment
    AmqttBroker = None

needs_amqtt = pytest.mark.skipif(AmqttBroker is None, reason="amqtt is not installed")


@pytest.fixture
async def broker():
    """An in-process MQTT broker on a free port; anonymous access allowed."""
    if AmqttBroker is None:  # pragma: no cover - guarded by needs_amqtt
        pytest.skip("amqtt is not installed")
    port = free_port()
    instance = AmqttBroker(
        {
            "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
            "plugins": {
                "amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}
            },
        }
    )
    await instance.start()
    try:
        yield port
    finally:
        with contextlib.suppress(Exception):
            await instance.shutdown()


@needs_amqtt
async def test_end_to_end_through_a_real_broker(broker):
    import aiomqtt

    settings = make_settings(
        mqtt_url=f"mqtt://127.0.0.1:{broker}",
        mqtt_device_id="e2e",
        mqtt_interval_s=1.0,
        mqtt_min_interval_s=0.5,
    )
    store = demo_store(settings)
    publisher = MqttPublisher(settings, store, version="9.9.9")
    seen: dict[str, str] = {}

    async def collect(client: aiomqtt.Client) -> None:
        async for message in client.messages:
            seen[str(message.topic)] = message.payload.decode()

    async with aiomqtt.Client(
        hostname="127.0.0.1", port=broker, identifier="test-subscriber"
    ) as subscriber:
        await subscriber.subscribe("stratumtap/e2e/#")
        await subscriber.subscribe("homeassistant/#")
        reader = asyncio.ensure_future(collect(subscriber))
        try:
            await publisher.start()
            await wait_for(
                lambda: (
                    {
                        "stratumtap/e2e/status",
                        "stratumtap/e2e/state",
                        "stratumtap/e2e/position",
                        "homeassistant/device/stratumtap_e2e/config",
                    }
                    <= set(seen)
                ),
                timeout=8.0,
            )

            assert seen["stratumtap/e2e/status"] == "online"

            discovery_payload = json.loads(seen["homeassistant/device/stratumtap_e2e/config"])
            assert discovery_payload["cmps"]["stratum"]["p"] == "sensor"
            assert discovery_payload["state_topic"] == "stratumtap/e2e/state"

            state = json.loads(seen["stratumtap/e2e/state"])
            assert state["stratum"] == store.ntp.stratum
            assert json.loads(seen["stratumtap/e2e/position"])["latitude"] is not None
            assert publisher.status()["connected"] is True

            await publisher.stop()
            await wait_for(
                lambda: seen.get("stratumtap/e2e/status") == "offline",
                timeout=5.0,
            )
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
            with contextlib.suppress(Exception):
                await publisher.stop()
