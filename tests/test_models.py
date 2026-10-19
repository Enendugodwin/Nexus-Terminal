"""Device and connection-profile model validation."""

from __future__ import annotations

import pytest

from nexus_remote.devices.models import (
    Device,
    ProtocolConfig,
    RoutingPreferences,
    ValidationError,
    normalize_host,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("10.10.10.25", "10.10.10.25"),
        ("  10.10.10.25  ", "10.10.10.25"),
        ("Example.COM", "example.com"),
        ("example.com.", "example.com"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:db8::1", "2001:db8::1"),
        ("srv-01", "srv-01"),
    ],
)
def test_normalize_host_accepts_valid_values(value, expected):
    assert normalize_host(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "bad host", "http://example.com", "-leading.example.com", "a" * 300])
def test_normalize_host_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        normalize_host(value)


def test_device_defaults_and_validation(device):
    assert device.name == "linux-prod-01"
    assert device.enabled_protocols() == ["ssh", "sftp"]
    assert device.primary_protocol() == "ssh"
    assert device.endpoint("ssh") == ("10.10.10.25", 22)
    assert device.tags == ["linux", "prod"]
    assert device.updated_at


def test_device_requires_a_name():
    with pytest.raises(ValidationError, match="name"):
        Device(name="", hostname="10.0.0.1").validate()


def test_device_requires_a_host():
    with pytest.raises(ValidationError, match="host"):
        Device(name="x", hostname="").validate()


def test_device_rejects_unknown_environment():
    with pytest.raises(ValidationError, match="environment"):
        Device(name="x", hostname="10.0.0.1", environment="narnia").validate()


def test_device_requires_an_enabled_protocol():
    with pytest.raises(ValidationError, match="at least one"):
        Device(
            name="x",
            hostname="10.0.0.1",
            connections={"ssh": ProtocolConfig("ssh", enabled=False)},
        ).validate()


def test_device_from_dict_roundtrip(device):
    payload = device.to_dict()
    restored = Device.from_dict(payload)
    assert restored.id == device.id
    assert restored.signature() == device.signature()
    assert restored.connections["ssh"].port == 22
    assert restored.routing.preferred_method == "direct"


def test_device_touches_updated_at_on_validation(device):
    before = device.updated_at
    device.touch()
    assert device.updated_at >= before


def test_protocol_port_bounds():
    with pytest.raises(ValidationError, match="port"):
        ProtocolConfig("ssh", port=70000)
    with pytest.raises(ValidationError, match="port"):
        ProtocolConfig("ssh", port=0)


def test_protocol_rejects_unknown_protocol():
    with pytest.raises(ValidationError):
        ProtocolConfig("telnet")


def test_protocol_auth_defaults_and_serialisation():
    cfg = ProtocolConfig("rdp")
    assert cfg.port == 3389
    assert cfg.auth_method == "credential_vault"
    payload = cfg.to_dict()
    assert payload["authentication"] == "credential_vault"
    assert "password" not in payload


def test_routing_rejects_unknown_method():
    with pytest.raises(ValidationError):
        RoutingPreferences(preferred_method="telepathy")


def test_routing_drops_duplicate_fallbacks():
    routing = RoutingPreferences(preferred_method="direct", fallbacks=["direct", "vpn", "vpn"])
    assert routing.fallbacks == ["vpn"]


def test_device_signature_identifies_host_and_ports(device):
    twin = Device(name="copy", hostname="10.10.10.25", connections=dict(device.connections))
    assert twin.signature() == device.signature()
    twin.connections["ssh"] = ProtocolConfig("ssh", enabled=True, port=2222)
    assert twin.signature() != device.signature()


def test_search_blob_includes_tags_and_group(device):
    device.group_name = "Core"
    blob = device.search_blob()
    for term in ("linux-prod-01", "10.10.10.25", "production", "linux", "core"):
        assert term in blob
