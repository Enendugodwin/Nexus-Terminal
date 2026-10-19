"""Persistence for device profiles, connection profiles and groups."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from nexus_remote.devices.models import (
    Device,
    ProtocolConfig,
    RoutingPreferences,
    ValidationError,
)
from nexus_remote.storage.database import Database
from nexus_remote.util import json_dumps, json_loads, searchable, utcnow_iso


class DuplicateDeviceError(ValidationError):
    """Raised when a profile duplicates an existing host/protocol/port pair."""

    def __init__(self, message: str, duplicates: list[Device] | None = None) -> None:
        super().__init__(message)
        self.duplicates = duplicates or []


@dataclass(frozen=True)
class Group:
    id: str
    name: str
    description: str = ""
    created_at: str = ""


class DeviceRepository:
    """CRUD over the ``devices``/``connection_profiles``/``routing_profiles`` tables."""

    def __init__(self, database: Database) -> None:
        self.db = database

    # -- reads --------------------------------------------------------------
    def get(self, device_id: str) -> Device | None:
        row = self.db.query_one(
            "SELECT d.*, g.name AS group_name FROM devices d "
            "LEFT JOIN device_groups g ON g.id = d.group_id WHERE d.id = ?",
            (device_id,),
        )
        if row is None:
            return None
        devices = self._hydrate([row], with_connections=True)
        return devices[0] if devices else None

    def find_by_name(self, name: str) -> Device | None:
        row = self.db.query_one(
            "SELECT d.*, g.name AS group_name FROM devices d "
            "LEFT JOIN device_groups g ON g.id = d.group_id "
            "WHERE lower(d.name) = lower(?)",
            (name,),
        )
        if row is None:
            return None
        hydrated = self._hydrate([row], with_connections=True)
        return hydrated[0] if hydrated else None

    def list(
        self,
        search: str = "",
        *,
        group: str | None = None,
        tag: str | None = None,
        protocol: str | None = None,
        environment: str | None = None,
        favorites_only: bool = False,
        sort: str = "name",
    ) -> list[Device]:
        rows = self.db.query(
            "SELECT d.*, g.name AS group_name FROM devices d "
            "LEFT JOIN device_groups g ON g.id = d.group_id"
        )
        devices = self._hydrate(list(rows), with_connections=True)
        selected: list[Device] = []
        for device in devices:
            if search and not searchable(device.search_blob(), search):
                continue
            if group and (device.group_name or "") != group:
                continue
            if tag and tag not in device.tags:
                continue
            if environment and device.environment != environment:
                continue
            if favorites_only and not device.favorite:
                continue
            if protocol and not (device.connection(protocol) and device.connection(protocol).enabled):
                continue
            selected.append(device)
        if sort == "name":
            selected.sort(key=lambda item: item.name.casefold())
        elif sort == "host":
            selected.sort(key=lambda item: item.hostname)
        elif sort == "environment":
            selected.sort(key=lambda item: (item.environment, item.name.casefold()))
        elif sort == "recent":
            selected.sort(key=lambda item: (item.last_connected_at or "", item.name.casefold()), reverse=True)
        elif sort == "updated":
            selected.sort(key=lambda item: item.updated_at, reverse=True)
        else:
            raise ValidationError(f"unknown sort key {sort!r}")
        return selected

    def count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM devices")
        return int(row["n"]) if row else 0

    def count_by_environment(self) -> dict[str, int]:
        rows = self.db.query(
            "SELECT environment, COUNT(*) AS n FROM devices GROUP BY environment ORDER BY environment"
        )
        return {row["environment"]: int(row["n"]) for row in rows}

    def count_by_operating_system(self) -> dict[str, int]:
        rows = self.db.query(
            "SELECT operating_system, COUNT(*) AS n FROM devices "
            "GROUP BY operating_system ORDER BY operating_system"
        )
        return {row["operating_system"]: int(row["n"]) for row in rows}

    def find_duplicates(self, device: Device) -> list[Device]:
        """Return stored devices that share this host/protocol/port combination."""
        signature = device.signature()
        matches: list[Device] = []
        for candidate in self.list():
            if candidate.id == device.id:
                continue
            if candidate.signature() == signature:
                matches.append(candidate)
        return matches

    # -- writes -------------------------------------------------------------
    def add(self, device: Device, *, allow_duplicate: bool = False) -> Device:
        device.validate()
        duplicates = self.find_duplicates(device)
        if duplicates and not allow_duplicate:
            names = ", ".join(item.name for item in duplicates)
            raise DuplicateDeviceError(
                f"{device.hostname} with the same protocol/port set already exists as: {names}",
                duplicates,
            )
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO devices (id, name, hostname, operating_system, environment, "
                "criticality, tags, description, owner, notes, favorite, group_id, "
                "last_connected_at, last_connection_ok, created_at, updated_at) "
                "VALUES (:id, :name, :hostname, :operating_system, :environment, :criticality, "
                ":tags, :description, :owner, :notes, :favorite, :group_id, :last_connected_at, "
                ":last_connection_ok, :created_at, :updated_at)",
                self._device_row(device),
            )
            self._write_connections(connection, device)
        return device

    def update(self, device: Device) -> Device:
        device.validate()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE devices SET name = :name, hostname = :hostname, "
                "operating_system = :operating_system, environment = :environment, "
                "criticality = :criticality, tags = :tags, description = :description, "
                "owner = :owner, notes = :notes, favorite = :favorite, group_id = :group_id, "
                "last_connected_at = :last_connected_at, last_connection_ok = :last_connection_ok, "
                "updated_at = :updated_at WHERE id = :id",
                self._device_row(device),
            )
            if cursor.rowcount == 0:
                raise ValidationError(f"no device with id {device.id!r}")
            connection.execute("DELETE FROM connection_profiles WHERE device_id = ?", (device.id,))
            connection.execute("DELETE FROM routing_profiles WHERE device_id = ?", (device.id,))
            self._write_connections(connection, device)
        return device

    def save(self, device: Device) -> Device:
        """Insert or update depending on whether the id already exists."""
        existing = self.db.query_one("SELECT id FROM devices WHERE id = ?", (device.id,))
        return self.update(device) if existing else self.add(device)

    def delete(self, device_id: str) -> bool:
        with self.db.transaction() as connection:
            cursor = connection.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        return cursor.rowcount > 0

    def set_favorite(self, device_id: str, favorite: bool) -> None:
        self.db.execute(
            "UPDATE devices SET favorite = ?, updated_at = ? WHERE id = ?",
            (int(bool(favorite)), utcnow_iso(), device_id),
        )

    def record_connection(self, device_id: str, success: bool) -> None:
        self.db.execute(
            "UPDATE devices SET last_connected_at = ?, last_connection_ok = ?, updated_at = ? "
            "WHERE id = ?",
            (utcnow_iso(), int(bool(success)), utcnow_iso(), device_id),
        )

    # -- groups -------------------------------------------------------------
    def create_group(self, name: str, description: str = "") -> Group:
        label = str(name or "").strip()
        if not label:
            raise ValidationError("group name is required")
        group_id = uuid.uuid4().hex
        created = utcnow_iso()
        self.db.execute(
            "INSERT INTO device_groups (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            (group_id, label, description, created),
        )
        return Group(id=group_id, name=label, description=description, created_at=created)

    def ensure_group(self, name: str) -> Group:
        label = str(name or "").strip()
        if not label:
            raise ValidationError("group name is required")
        row = self.db.query_one("SELECT * FROM device_groups WHERE lower(name) = lower(?)", (label,))
        if row:
            return Group(id=row["id"], name=row["name"], description=row["description"], created_at=row["created_at"])
        return self.create_group(label)

    def list_groups(self) -> list[Group]:
        rows = self.db.query("SELECT * FROM device_groups ORDER BY name COLLATE NOCASE")
        return [
            Group(id=row["id"], name=row["name"], description=row["description"], created_at=row["created_at"])
            for row in rows
        ]

    def assign_group(self, device_id: str, group_id: str | None) -> None:
        self.db.execute(
            "UPDATE devices SET group_id = ?, updated_at = ? WHERE id = ?",
            (group_id, utcnow_iso(), device_id),
        )

    def delete_group(self, group_id: str) -> None:
        self.db.execute("DELETE FROM device_groups WHERE id = ?", (group_id,))

    # -- internals ----------------------------------------------------------
    def _device_row(self, device: Device) -> dict[str, Any]:
        return {
            "id": device.id,
            "name": device.name,
            "hostname": device.hostname,
            "operating_system": device.operating_system,
            "environment": device.environment,
            "criticality": device.criticality,
            "tags": json_dumps(device.tags),
            "description": device.description,
            "owner": device.owner,
            "notes": device.notes,
            "favorite": int(bool(device.favorite)),
            "group_id": device.group_id,
            "last_connected_at": device.last_connected_at,
            "last_connection_ok": None if device.last_connection_ok is None else int(bool(device.last_connection_ok)),
            "created_at": device.created_at,
            "updated_at": device.updated_at,
        }

    def _write_connections(self, connection: Any, device: Device) -> None:
        for name, cfg in device.connections.items():
            connection.execute(
                "INSERT INTO connection_profiles (id, device_id, protocol, enabled, port, "
                "auth_method, username, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    device.id,
                    name,
                    int(bool(cfg.enabled)),
                    cfg.port,
                    cfg.auth_method,
                    cfg.username,
                    json_dumps(cfg.extra),
                ),
            )
        routing = device.routing
        connection.execute(
            "INSERT INTO routing_profiles (device_id, preferred_method, fallbacks, jump_host, "
            "gateway, relay, agent_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                device.id,
                routing.preferred_method,
                json_dumps(routing.fallbacks),
                routing.jump_host,
                routing.gateway,
                routing.relay,
                routing.agent_id,
            ),
        )

    def _hydrate(self, rows: list[Any], *, with_connections: bool) -> list[Device]:
        if not rows:
            return []
        ids = [row["id"] for row in rows]
        connections: dict[str, dict[str, ProtocolConfig]] = {device_id: {} for device_id in ids}
        routing: dict[str, RoutingPreferences] = {}
        if with_connections:
            placeholders = ",".join("?" for _ in ids)
            for row in self.db.query(
                f"SELECT * FROM connection_profiles WHERE device_id IN ({placeholders})",
                tuple(ids),
            ):
                connections[row["device_id"]][row["protocol"]] = ProtocolConfig(
                    protocol=row["protocol"],
                    enabled=bool(row["enabled"]),
                    port=row["port"],
                    auth_method=row["auth_method"],
                    username=row["username"],
                    extra=json_loads(row["extra"], {}),
                )
            for row in self.db.query(
                f"SELECT * FROM routing_profiles WHERE device_id IN ({placeholders})",
                tuple(ids),
            ):
                routing[row["device_id"]] = RoutingPreferences(
                    preferred_method=row["preferred_method"],
                    fallbacks=json_loads(row["fallbacks"], []),
                    jump_host=row["jump_host"],
                    gateway=row["gateway"],
                    relay=row["relay"],
                    agent_id=row["agent_id"],
                )
        devices: list[Device] = []
        for row in rows:
            device = Device(
                id=row["id"],
                name=row["name"],
                hostname=row["hostname"],
                operating_system=row["operating_system"],
                environment=row["environment"],
                criticality=row["criticality"],
                tags=list(json_loads(row["tags"], [])),
                description=row["description"],
                owner=row["owner"],
                notes=row["notes"],
                favorite=bool(row["favorite"]),
                group_id=row["group_id"],
                group_name=(row["group_name"] if "group_name" in row.keys() and row["group_name"] else ""),
                connections=connections.get(row["id"], {}) or {},
                routing=routing.get(row["id"], RoutingPreferences()),
                last_connected_at=row["last_connected_at"],
                last_connection_ok=None if row["last_connection_ok"] is None else bool(row["last_connection_ok"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            try:
                device.validate()
            except ValidationError:
                # A profile stored by an older version may not satisfy current
                # rules; keep it visible rather than silently dropping it.
                pass
            devices.append(device)
        return devices

    # -- bulk helpers -------------------------------------------------------
    def import_many(self, devices: Iterable[Device], *, mode: str = "skip") -> dict[str, list[str]]:
        """Import profiles. ``mode`` is ``skip``, ``rename`` or ``overwrite``."""
        result: dict[str, list[str]] = {"added": [], "skipped": [], "renamed": [], "overwritten": []}
        for device in devices:
            existing = self.find_by_name(device.name)
            duplicates = self.find_duplicates(device)
            if (existing or duplicates) and mode == "skip":
                result["skipped"].append(device.name)
                continue
            if existing and mode == "overwrite":
                device.id = existing.id
                self.update(device)
                result["overwritten"].append(device.name)
                continue
            if duplicates and mode in {"rename", "overwrite"}:
                suffix = 2
                base = device.name
                while self.find_by_name(f"{base} ({suffix})"):
                    suffix += 1
                device.name = f"{base} ({suffix})"
                device.id = uuid.uuid4().hex
                self.add(device, allow_duplicate=True)
                result["renamed"].append(device.name)
                continue
            self.add(device, allow_duplicate=True)
            result["added"].append(device.name)
        return result
