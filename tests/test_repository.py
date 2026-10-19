"""Device repository behaviour: CRUD, search, groups, duplicates, import modes."""

from __future__ import annotations

import pytest

from nexus_remote.devices.models import Device, ProtocolConfig
from nexus_remote.devices.repository import DuplicateDeviceError


def test_add_and_get(repository, device):
    repository.add(device)
    loaded = repository.get(device.id)
    assert loaded is not None
    assert loaded.name == device.name
    assert loaded.connections["ssh"].username == "deploy"
    assert loaded.routing.preferred_method == "direct"


def test_update_persists_connection_changes(repository, device):
    repository.add(device)
    device.name = "renamed"
    device.connections["ssh"] = ProtocolConfig("ssh", enabled=True, port=2222, username="ops")
    repository.update(device)
    loaded = repository.get(device.id)
    assert loaded.name == "renamed"
    assert loaded.connections["ssh"].port == 2222
    assert loaded.connections["ssh"].username == "ops"


def test_duplicate_detection_blocks_by_default(repository, device):
    repository.add(device)
    clone = Device(name="clone", hostname=device.hostname, connections=dict(device.connections))
    with pytest.raises(DuplicateDeviceError) as excinfo:
        repository.add(clone)
    assert excinfo.value.duplicates[0].id == device.id


def test_allow_duplicate_flag(repository, device):
    repository.add(device)
    clone = Device(name="clone", hostname=device.hostname, connections=dict(device.connections))
    repository.add(clone, allow_duplicate=True)
    assert repository.count() == 2


def test_search_filters_by_name_host_and_tag(repository, device):
    repository.add(device)
    other = Device(name="win-app-01", hostname="10.20.0.5", operating_system="windows", tags=["windows"])
    repository.add(other)
    assert [d.name for d in repository.list("linux")] == ["linux-prod-01"]
    assert [d.name for d in repository.list("10.20")] == ["win-app-01"]
    assert [d.name for d in repository.list("prod")] == ["linux-prod-01"]
    assert repository.list("no-such-term") == []


def test_filters_by_environment_protocol_and_favourite(repository, device):
    repository.add(device)
    repository.set_favorite(device.id, True)
    assert len(repository.list(environment="production")) == 1
    assert repository.list(environment="development") == []
    assert len(repository.list(protocol="ssh")) == 1
    assert repository.list(protocol="rdp") == []
    assert len(repository.list(favorites_only=True)) == 1


def test_groups_are_created_and_assigned(repository, device):
    repository.add(device)
    group = repository.ensure_group("Core infrastructure")
    repository.assign_group(device.id, group.id)
    loaded = repository.get(device.id)
    assert loaded.group_name == "Core infrastructure"
    assert [item.name for item in repository.list_groups()] == ["Core infrastructure"]
    assert len(repository.list(group="Core infrastructure")) == 1
    repository.delete_group(group.id)
    assert repository.get(device.id).group_name == ""


def test_ensure_group_is_case_insensitive(repository):
    first = repository.ensure_group("Core")
    second = repository.ensure_group("core")
    assert first.id == second.id


def test_record_connection_updates_status(repository, device):
    repository.add(device)
    repository.record_connection(device.id, False)
    loaded = repository.get(device.id)
    assert loaded.last_connection_ok is False
    assert loaded.last_connected_at


def test_delete_removes_profile_and_connections(repository, device):
    repository.add(device)
    assert repository.delete(device.id) is True
    assert repository.get(device.id) is None
    assert repository.delete(device.id) is False


def test_sorting_options(repository):
    for name, host, env in (
        ("zeta", "10.0.0.3", "production"),
        ("alpha", "10.0.0.1", "development"),
        ("mike", "10.0.0.2", "production"),
    ):
        repository.add(Device(name=name, hostname=host, environment=env))
    assert [d.name for d in repository.list(sort="name")] == ["alpha", "mike", "zeta"]
    assert [d.name for d in repository.list(sort="host")] == ["alpha", "mike", "zeta"]
    assert [d.name for d in repository.list(sort="environment")][0] == "alpha"


def test_counts(repository, device):
    repository.add(device)
    repository.add(Device(name="win", hostname="10.0.0.9", operating_system="windows", environment="lab"))
    assert repository.count() == 2
    assert repository.count_by_environment() == {"lab": 1, "production": 1}
    assert repository.count_by_operating_system() == {"linux": 1, "windows": 1}


def test_import_many_skip_and_rename(repository, device):
    repository.add(device)
    duplicate = Device(name=device.name, hostname=device.hostname, connections=dict(device.connections))
    result = repository.import_many([duplicate], mode="skip")
    assert result["skipped"] == [device.name]

    renamed = Device(name=device.name, hostname=device.hostname, connections=dict(device.connections))
    result = repository.import_many([renamed], mode="rename")
    assert result["renamed"] and repository.count() == 2


def test_import_many_overwrite_keeps_id(repository, device):
    repository.add(device)
    replacement = Device(name=device.name, hostname=device.hostname, connections=dict(device.connections))
    replacement.description = "updated description"
    result = repository.import_many([replacement], mode="overwrite")
    assert result["overwritten"] == [device.name]
    loaded = repository.get(device.id)
    assert loaded.description == "updated description"
    assert repository.count() == 1


def test_invalid_sort_key(repository):
    with pytest.raises(Exception):
        repository.list(sort="nonsense")
