"""YAML import/export of device profiles.

Exported profiles are intentionally lossy: they carry inventory, connection and
routing metadata but **never** credential material. Any payload that tries to
smuggle a secret key is rejected on export, and ignored on import.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Iterable

import yaml

from nexus_remote import config
from nexus_remote.devices.models import Device, ProfileError, ProtocolConfig, RoutingPreferences
from nexus_remote.devices.repository import DeviceRepository
from nexus_remote.util import utcnow_iso

PROFILE_FORMAT_VERSION = 1

#: Keys that must never appear in an exported/imported profile.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "private_key",
        "privatekey",
        "passphrase",
        "token",
        "api_key",
        "apikey",
        "access_key",
        "credential",
        "credentials",
        "key_content",
    }
)


def _find_sensitive_keys(payload: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            name = str(key).lower()
            path = f"{prefix}.{key}" if prefix else str(key)
            if name in SENSITIVE_KEYS:
                found.append(path)
            found.extend(_find_sensitive_keys(value, path))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(_find_sensitive_keys(value, f"{prefix}[{index}]"))
    return found


def assert_no_secrets(payload: Any) -> None:
    offenders = _find_sensitive_keys(payload)
    if offenders:
        raise ProfileError(
            "profile payload contains secret-bearing fields and cannot be exported: "
            + ", ".join(sorted(set(offenders)))
        )


def device_to_profile(device: Device) -> dict[str, Any]:
    """Render a device in the documented profile layout (secrets excluded)."""
    payload = {
        "device": {
            "name": device.name,
            "hostname": device.hostname,
            "operating_system": device.operating_system,
            "environment": device.environment,
            "criticality": device.criticality,
            "tags": list(device.tags),
            "description": device.description,
            "owner": device.owner,
            "notes": device.notes,
            "favorite": device.favorite,
            "group": device.group_name or None,
        },
        "connections": {name: cfg.to_dict() for name, cfg in device.connections.items()},
        "routing": device.routing.to_dict(),
    }
    assert_no_secrets(payload)
    return payload


def profile_to_device(payload: dict[str, Any]) -> Device:
    """Build a Device from either the nested profile layout or a flat dict."""
    data = dict(payload or {})
    if "device" in data:
        device_part = dict(data.get("device") or {})
        connections = dict(data.get("connections") or {})
        routing = dict(data.get("routing") or {})
    else:
        device_part = data
        connections = dict(data.get("connections") or {})
        routing = dict(data.get("routing") or {})

    # Silently drop any secret-bearing field that a hand-edited file may carry.
    for key in list(device_part):
        if str(key).lower() in SENSITIVE_KEYS:
            device_part.pop(key)
    for name, cfg in list(connections.items()):
        if isinstance(cfg, dict):
            connections[name] = {
                key: value for key, value in cfg.items() if str(key).lower() not in SENSITIVE_KEYS
            }

    device = Device(
        id=device_part.get("id") or uuid.uuid4().hex,
        name=device_part.get("name", ""),
        hostname=device_part.get("hostname", ""),
        operating_system=device_part.get("operating_system", "unknown"),
        environment=device_part.get("environment", "unspecified"),
        criticality=device_part.get("criticality", "standard"),
        tags=list(device_part.get("tags") or []),
        description=device_part.get("description", ""),
        owner=device_part.get("owner", ""),
        notes=device_part.get("notes", ""),
        favorite=bool(device_part.get("favorite", False)),
        group_name=str(device_part.get("group") or ""),
        connections={name: ProtocolConfig.from_dict(name, cfg) for name, cfg in connections.items()},
        routing=RoutingPreferences.from_dict(routing),
    )
    if not device.name:
        raise ProfileError("profile is missing a device name")
    return device.validate()


def export_devices(devices: Iterable[Device], path: str | Path) -> Path:
    """Write devices to a YAML file and return the path written."""
    target = Path(path)
    if target.is_dir():
        target = target / f"nexus-devices-{utcnow_iso().replace(':', '')}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": PROFILE_FORMAT_VERSION,
        "application": config.APP_SLUG,
        "exported_at": utcnow_iso(),
        "devices": [device_to_profile(device) for device in devices],
    }
    assert_no_secrets(payload)
    target.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return target


def load_profiles(path: str | Path) -> list[Device]:
    """Parse a YAML export into Device objects."""
    source = Path(path)
    if not source.exists():
        raise ProfileError(f"profile file not found: {source}")
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - surfaced to the UI
        raise ProfileError(f"could not parse {source.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProfileError("profile file must contain a mapping with a 'devices' list")
    entries = payload.get("devices")
    if entries is None:
        entries = [payload] if "hostname" in payload else []
    if not isinstance(entries, list):
        raise ProfileError("'devices' must be a list")
    devices: list[Device] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ProfileError(f"device entry #{index + 1} must be a mapping")
        devices.append(profile_to_device(entry))
    return devices


def import_profiles(
    path: str | Path,
    repository: DeviceRepository,
    *,
    mode: str = "skip",
) -> dict[str, list[str]]:
    """Import a YAML export into the repository (``skip``/``rename``/``overwrite``)."""
    if mode not in {"skip", "rename", "overwrite"}:
        raise ProfileError("import mode must be skip, rename or overwrite")
    devices = load_profiles(path)
    result = repository.import_many(devices, mode=mode)
    result["total"] = [str(len(devices))]  # type: ignore[assignment]
    return result


def sample_profile() -> dict[str, Any]:
    """The documented example profile, used by the UI's export template/help."""
    return {
        "version": PROFILE_FORMAT_VERSION,
        "devices": [
            {
                "device": {
                    "name": "linux-production-01",
                    "hostname": "10.10.10.25",
                    "operating_system": "linux",
                    "environment": "production",
                    "tags": ["linux", "production"],
                },
                "connections": {
                    "ssh": {"enabled": True, "port": 22, "authentication": "credential_vault"},
                    "sftp": {"enabled": True},
                    "rdp": {"enabled": False},
                },
                "routing": {
                    "preferred_method": "direct",
                    "jump_host": None,
                    "gateway": None,
                    "relay": None,
                },
            }
        ],
    }
