"""Profile import/export: round-trips and secret safety."""

from __future__ import annotations

import pytest
import yaml

from nexus_remote.devices import profiles
from nexus_remote.devices.models import Device, ProfileError, ProtocolConfig


def test_export_and_reload_roundtrip(repository, device, tmp_path):
    repository.add(device)
    target = profiles.export_devices(repository.list(), tmp_path / "devices.yaml")
    assert target.exists()
    restored = profiles.load_profiles(target)
    assert len(restored) == 1
    assert restored[0].name == device.name
    assert restored[0].hostname == device.hostname
    assert restored[0].connections["ssh"].port == 22
    assert restored[0].environment == "production"


def test_export_contains_inventory_but_no_secrets(device, tmp_path):
    target = profiles.export_devices([device], tmp_path / "export.yaml")
    text = target.read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    assert payload["version"] == profiles.PROFILE_FORMAT_VERSION
    assert payload["devices"][0]["device"]["name"] == device.name
    for forbidden in ("password", "passphrase", "private_key", "token"):
        assert forbidden not in text.lower()


def test_export_refuses_secret_bearing_extra(device, tmp_path):
    device.connections["ssh"].extra = {"password": "hunter2"}
    with pytest.raises(ProfileError):
        profiles.export_devices([device], tmp_path / "bad.yaml")
    assert not (tmp_path / "bad.yaml").exists()


def test_assert_no_secrets_detects_nested(device):
    with pytest.raises(ProfileError, match="password"):
        profiles.assert_no_secrets({"device": {"name": "x"}, "connections": {"ssh": {"password": "x"}}})


def test_import_ignores_smuggled_secrets(repository, tmp_path):
    payload = {
        "devices": [
            {
                "device": {"name": "imported", "hostname": "10.9.9.9"},
                "connections": {"ssh": {"enabled": True, "port": 22, "password": "should-be-dropped"}},
                "routing": {"preferred_method": "direct"},
            }
        ]
    }
    path = tmp_path / "input.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    devices = profiles.load_profiles(path)
    assert devices[0].connections["ssh"].extra == {}


def test_import_modes(repository, device, tmp_path):
    repository.add(device)
    target = profiles.export_devices([device], tmp_path / "devices.yaml")
    result = profiles.import_profiles(target, repository, mode="skip")
    assert result["skipped"] == [device.name]

    result = profiles.import_profiles(target, repository, mode="rename")
    assert result["renamed"]
    assert repository.count() == 2


def test_import_rejects_unknown_mode(repository, tmp_path):
    with pytest.raises(ProfileError):
        profiles.import_profiles(tmp_path / "missing.yaml", repository, mode="merge")


def test_missing_file_raises(tmp_path):
    with pytest.raises(ProfileError, match="not found"):
        profiles.load_profiles(tmp_path / "nope.yaml")


def test_malformed_payload_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("devices: not-a-list\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        profiles.load_profiles(path)


def test_sample_profile_is_valid():
    sample = profiles.sample_profile()
    device = profiles.profile_to_device(sample["devices"][0])
    assert device.name == "linux-production-01"
    assert device.operating_system == "linux"
    assert device.connections["rdp"].enabled is False


def test_export_without_local_ids(tmp_path, device):
    target = profiles.export_devices([device], tmp_path / "shared.yaml")
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert "id" not in payload["devices"][0]["device"]


def test_profile_to_device_flat_shape():
    device = profiles.profile_to_device(
        {
            "name": "flat",
            "hostname": "192.0.2.10",
            "connections": {"smb": {"enabled": True}},
        }
    )
    assert device.name == "flat"
    assert device.connections["smb"].enabled is True
    assert device.primary_protocol() == "smb"
