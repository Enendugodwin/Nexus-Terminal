"""Shared pytest fixtures.

Every test runs against a temporary data directory and an in-memory credential
backend, so no real Windows Credential Manager entries are created or read.
"""

from __future__ import annotations

import pytest

from nexus_remote import config
from nexus_remote.assistant.command_history import CommandHistory
from nexus_remote.assistant.snippets import SnippetStore
from nexus_remote.connections.manager import SessionManager
from nexus_remote.connections.routing import RoutingEngine
from nexus_remote.devices.models import Device, ProtocolConfig, RoutingPreferences
from nexus_remote.devices.repository import DeviceRepository
from nexus_remote.security.audit import AuditLog
from nexus_remote.security.credentials import CredentialService, CredentialStore, MemoryBackend
from nexus_remote.security.host_keys import KnownHosts
from nexus_remote.security.policies import PolicyEngine
from nexus_remote.services import build_services
from nexus_remote.storage.database import Database


@pytest.fixture()
def database(tmp_path):
    db = Database(tmp_path / "nexus-test.db").initialize()
    yield db
    db.close()


@pytest.fixture()
def repository(database) -> DeviceRepository:
    return DeviceRepository(database)


@pytest.fixture()
def memory_backend() -> MemoryBackend:
    return MemoryBackend()


@pytest.fixture()
def credential_store(memory_backend) -> CredentialStore:
    return CredentialStore(memory_backend)


@pytest.fixture()
def credentials(database, credential_store) -> CredentialService:
    return CredentialService(database, credential_store)


@pytest.fixture()
def audit(database, tmp_path) -> AuditLog:
    return AuditLog(database, tmp_path / "audit.jsonl")


@pytest.fixture()
def host_keys(database, tmp_path) -> KnownHosts:
    return KnownHosts(database, tmp_path / "known_hosts")


@pytest.fixture()
def history(database) -> CommandHistory:
    return CommandHistory(database)


@pytest.fixture()
def snippets(database) -> SnippetStore:
    return SnippetStore(database)


@pytest.fixture()
def manager(repository, credentials, audit, history) -> SessionManager:
    policy = PolicyEngine()
    return SessionManager(
        repository,
        credentials=credentials,
        audit=audit,
        policy=policy,
        routing=RoutingEngine(policy),
        history=history,
    )


@pytest.fixture()
def device() -> Device:
    return Device(
        name="linux-prod-01",
        hostname="10.10.10.25",
        operating_system="linux",
        environment="production",
        criticality="high",
        tags=["linux", "prod"],
        connections={
            "ssh": ProtocolConfig("ssh", enabled=True, port=22, username="deploy"),
            "sftp": ProtocolConfig("sftp", enabled=True, port=22, username="deploy"),
            "rdp": ProtocolConfig("rdp", enabled=False),
            "smb": ProtocolConfig("smb", enabled=False),
        },
        routing=RoutingPreferences(preferred_method="direct"),
    ).validate()


@pytest.fixture()
def stored_device(repository, device) -> Device:
    """The standard test device, persisted so foreign keys are satisfied."""
    return repository.add(device)


@pytest.fixture()
def services(tmp_path):
    app = build_services(config.Paths(tmp_path / "home"), credential_backend=MemoryBackend())
    yield app
    app.close()
