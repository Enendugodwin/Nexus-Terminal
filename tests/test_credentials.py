"""Credential vault and reference management (never stores secrets in SQLite)."""

from __future__ import annotations

import pytest

from nexus_remote.devices.models import Device
from nexus_remote.security.credentials import (
    CredentialError,
    CredentialService,
    CredentialStore,
    MemoryBackend,
)

SECRET = "S3cret-p@ssw0rd!"


def test_store_and_resolve(credentials, stored_device):
    reference = credentials.save(stored_device.id, "password", "deploy", SECRET)
    assert reference.key_name.endswith(":password:deploy")
    assert credentials.resolve(stored_device.id, "password", "deploy") == SECRET
    assert reference.username == "deploy"


def test_database_never_contains_the_secret(credentials, database, stored_device):
    credentials.save(stored_device.id, "password", "deploy", SECRET)
    rows = database.query("SELECT * FROM credential_refs")
    serialised = " ".join(str(value) for row in rows for value in tuple(row))
    assert SECRET not in serialised
    assert stored_device.id in serialised


def test_save_replaces_reference_without_duplicating(credentials, database, stored_device):
    first = credentials.save(stored_device.id, "password", "deploy", "one")
    second = credentials.save(stored_device.id, "password", "deploy", "two")
    assert first.id == second.id
    assert len(database.query("SELECT * FROM credential_refs")) == 1
    assert credentials.resolve(stored_device.id, "password", "deploy") == "two"


def test_multiple_kinds_coexist(credentials, stored_device):
    credentials.save(stored_device.id, "password", "deploy", SECRET)
    credentials.save(stored_device.id, "ssh_key_passphrase", "deploy", "phrase")
    assert len(credentials.references(stored_device.id)) == 2
    assert credentials.resolve(stored_device.id, "ssh_key_passphrase", "deploy") == "phrase"


def test_unknown_kind_is_rejected(credentials, stored_device):
    with pytest.raises(CredentialError):
        credentials.save(stored_device.id, "credit-card", "deploy", SECRET)


def test_empty_secret_is_rejected(credentials, stored_device):
    with pytest.raises(CredentialError):
        credentials.save(stored_device.id, "password", "deploy", "")


def test_revoke_removes_value_and_reference(credentials, stored_device):
    reference = credentials.save(stored_device.id, "password", "deploy", SECRET)
    credentials.revoke(reference)
    assert credentials.resolve(stored_device.id, "password", "deploy") is None
    assert credentials.references(stored_device.id) == []


def test_revoke_device_removes_all(credentials, stored_device):
    credentials.save(stored_device.id, "password", "deploy", SECRET)
    credentials.save(stored_device.id, "api_token", "api", "token-value")
    assert credentials.revoke_device(stored_device.id) == 2
    assert credentials.references(stored_device.id) == []


def test_describe_never_includes_the_secret(credentials, stored_device):
    reference = credentials.save(stored_device.id, "password", "deploy", SECRET)
    described = credentials.describe(reference)
    assert described["stored"] is True
    assert SECRET not in str(described)


def test_memory_backend_is_not_persistent(credential_store):
    assert credential_store.persistent is False
    assert "memory" in credential_store.backend_name


def test_store_rejects_empty_secret(credential_store):
    with pytest.raises(CredentialError):
        credential_store.store("device", "password", "user", "")


def test_key_name_defaults_username_placeholder():
    assert CredentialStore.key_name("d1", "password", "") == "d1:password:-"


def test_service_uses_injected_backend(database, repository):
    device = repository.add(Device(name="dev", hostname="dev.local"))
    backend = MemoryBackend()
    service = CredentialService(database, CredentialStore(backend))
    service.save(device.id, "password", "u", "value")
    assert backend.get(CredentialStore.key_name(device.id, "password", "u")) == "value"
