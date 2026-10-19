"""Small shared helpers with no application dependencies."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, TypeVar

T = TypeVar("T")

#: Default I/O chunk size for streaming file transfers (bounded memory).
CHUNK_SIZE = 512 * 1024


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: T) -> T:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def coalesce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def chunked(path: str | os.PathLike[str], chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    """Yield a file's bytes without loading it into memory."""
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            yield block


def sha256_file(path: str | os.PathLike[str], chunk_size: int = CHUNK_SIZE) -> str:
    digest = hashlib.sha256()
    for block in chunked(path, chunk_size):
        digest.update(block)
    return digest.hexdigest()


def human_size(num_bytes: int | float | None) -> str:
    if num_bytes is None:
        return "-"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def searchable(haystack: str, needle: str) -> bool:
    """Case-insensitive, whitespace-tolerant substring match."""
    if not needle:
        return True
    hay = haystack.casefold()
    for term in needle.casefold().split():
        if term not in hay:
            return False
    return True


def first_of(values: Iterable[T | None]) -> T | None:
    for value in values:
        if value is not None:
            return value
    return None


def data_root_default() -> Path:
    from nexus_remote.config import app_data_dir

    return app_data_dir()
