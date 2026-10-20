"""Vendor catalog feed: schema validation, local storage and metadata.

A feed is a small JSON document served over HTTPS by the vendor:

.. code-block:: json

    {
      "schema": 1,
      "version": "2026.10.20",
      "vendor": "Example Corp",
      "environments": {
        "bash": [{"command": "apt list --upgradable", "summary": "..."}],
        "powershell": [{"command": "Get-MpComputerStatus", "summary": "..."}]
      }
    }

Everything is validated before it can influence suggestions: unknown keys are
ignored, unknown environments and malformed entries reject the whole document,
and there are hard limits on size and count. A rejected feed never replaces the
last good file.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Only these environments may appear in a feed.
CATALOG_ENVIRONMENTS = ("bash", "powershell", "cmd", "cisco", "panos")

SCHEMA_VERSION = 1
MAX_ENVIRONMENTS = 12
MAX_ENTRIES_PER_ENVIRONMENT = 2000
MAX_TOTAL_ENTRIES = 10000
MAX_COMMAND_LENGTH = 240
MAX_SUMMARY_LENGTH = 240
MAX_VERSION_LENGTH = 64
MAX_VENDOR_LENGTH = 120


class CatalogFormatError(ValueError):
    """The feed is not a valid catalog document."""


@dataclass
class Catalog:
    """A validated catalog document plus where it came from."""

    version: str
    vendor: str
    environments: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    source: str = ""
    fetched_at: str = ""
    etag: str = ""

    @property
    def total(self) -> int:
        return sum(len(entries) for entries in self.environments.values())

    def counts(self) -> dict[str, int]:
        return {environment: len(entries) for environment, entries in self.environments.items()}


def parse_catalog(payload: object, *, source: str = "") -> Catalog:
    """Validate a decoded JSON document and return a :class:`Catalog`."""
    if not isinstance(payload, dict):
        raise CatalogFormatError("the catalog must be a JSON object")
    schema = payload.get("schema")
    if schema != SCHEMA_VERSION:
        raise CatalogFormatError(
            f"unsupported catalog schema {schema!r} (expected {SCHEMA_VERSION})"
        )
    environments = payload.get("environments")
    if not isinstance(environments, dict) or not environments:
        raise CatalogFormatError("the catalog needs a non-empty 'environments' object")
    if len(environments) > MAX_ENVIRONMENTS:
        raise CatalogFormatError("too many environments in the catalog")

    parsed: dict[str, list[tuple[str, str]]] = {}
    seen: set[tuple[str, str]] = set()
    total = 0
    for name, raw_entries in environments.items():
        environment = str(name).strip().lower()
        if environment not in CATALOG_ENVIRONMENTS:
            raise CatalogFormatError(f"unknown environment {name!r}")
        if not isinstance(raw_entries, list):
            raise CatalogFormatError(f"environment {environment!r} must be a list")
        if len(raw_entries) > MAX_ENTRIES_PER_ENVIRONMENT:
            raise CatalogFormatError(f"too many entries for {environment!r}")
        entries: list[tuple[str, str]] = []
        for index, raw in enumerate(raw_entries):
            command, summary = _parse_entry(raw, environment, index)
            total += 1
            if total > MAX_TOTAL_ENTRIES:
                raise CatalogFormatError("the catalog has too many entries")
            key = (environment, command.casefold())
            if key in seen:
                continue
            seen.add(key)
            entries.append((command, summary))
        parsed[environment] = entries

    version = _short_string(payload.get("version"), "version", MAX_VERSION_LENGTH)
    vendor = _short_string(payload.get("vendor"), "vendor", MAX_VENDOR_LENGTH)
    return Catalog(
        version=version or "unversioned",
        vendor=vendor,
        environments=parsed,
        source=source,
        etag=str(payload.get("etag") or ""),
        fetched_at=str(payload.get("fetched_at") or ""),
    )


def _parse_entry(raw: object, environment: str, index: int) -> tuple[str, str]:
    if not isinstance(raw, dict):
        raise CatalogFormatError(f"{environment}[{index}] must be an object")
    command = raw.get("command")
    summary = raw.get("summary", "")
    if not isinstance(command, str) or not command.strip():
        raise CatalogFormatError(f"{environment}[{index}] needs a non-empty 'command'")
    command = command.strip()
    if len(command) > MAX_COMMAND_LENGTH:
        raise CatalogFormatError(f"{environment}[{index}] command is too long")
    if any(character in command for character in "\r\n\t\x00"):
        raise CatalogFormatError(f"{environment}[{index}] command must be a single line")
    if not isinstance(summary, str) or len(summary) > MAX_SUMMARY_LENGTH:
        raise CatalogFormatError(f"{environment}[{index}] summary is invalid")
    if any(character in summary for character in "\r\n\x00"):
        raise CatalogFormatError(f"{environment}[{index}] summary must be a single line")
    return command, summary.strip()


def _short_string(value: object, label: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CatalogFormatError(f"'{label}' must be a string")
    value = value.strip()
    if len(value) > limit:
        raise CatalogFormatError(f"'{label}' is too long")
    return value


def read_catalog(path: Path) -> Catalog | None:
    """Read a stored catalog; ``None`` when the file is absent or unreadable.

    A corrupt download is ignored rather than fatal: the built-in catalog still
    works and the next update can replace the bad file.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
        payload = json.loads(raw)
        catalog = parse_catalog(payload)
        catalog.source = str(payload.get("source") or "")
        return catalog
    except (OSError, ValueError, CatalogFormatError):
        return None


def load_environment_map(path: Path) -> dict[str, list[tuple[str, str]]]:
    """The stored catalog as a plain environment map (empty when unusable)."""
    catalog = read_catalog(path)
    return catalog.environments if catalog else {}


def metadata(path: Path) -> dict[str, Any]:
    """Small JSON-friendly summary for the UI (empty when there is no catalog)."""
    catalog = read_catalog(path)
    if catalog is None:
        return {}
    return {
        "version": catalog.version,
        "vendor": catalog.vendor,
        "source": catalog.source,
        "fetched_at": catalog.fetched_at,
        "total": catalog.total,
        "counts": catalog.counts(),
    }


def write_catalog(
    path: Path,
    catalog: Catalog,
    *,
    source: str,
    etag: str = "",
    fetched_at: str | None = None,
) -> None:
    """Atomically persist a validated catalog.

    The file is written next to its destination and moved into place, so a crash
    mid-write cannot leave a half-parsed catalog behind.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA_VERSION,
        "version": catalog.version,
        "vendor": catalog.vendor,
        "source": source,
        "etag": etag,
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environments": {
            environment: [
                {"command": command, "summary": summary} for command, summary in entries
            ]
            for environment, entries in catalog.environments.items()
        },
    }
    handle, temporary = tempfile.mkstemp(
        prefix="catalog-", suffix=".json.tmp", dir=str(destination.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def merge_catalogs(
    builtin: dict[str, list[tuple[str, str]]],
    extra: dict[str, list[tuple[str, str]]],
) -> dict[str, list[tuple[str, str]]]:
    """Bundled entries win; ``extra`` only contributes names not already present."""
    merged = {environment: list(entries) for environment, entries in builtin.items()}
    for environment, entries in extra.items():
        existing = merged.setdefault(environment, [])
        known = {command.casefold() for command, _summary in existing}
        for command, summary in entries:
            if command.casefold() in known:
                continue
            known.add(command.casefold())
            existing.append((command, summary))
    return merged


def count_new_entries(
    builtin: dict[str, list[tuple[str, str]]],
    extra: dict[str, list[tuple[str, str]]],
) -> int:
    """How many feed entries would actually be added to the suggestion set."""
    added = 0
    for environment, entries in extra.items():
        known = {command.casefold() for command, _summary in builtin.get(environment, [])}
        added += sum(1 for command, _summary in entries if command.casefold() not in known)
    return added
