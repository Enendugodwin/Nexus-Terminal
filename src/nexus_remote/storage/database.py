"""SQLite access for Nexus Remote.

The database is the system of record for device inventory, command history,
known errors, audit events and settings. It deliberately stores *references* to
credentials, never secret material (see ``nexus_remote.security.credentials``).
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def supports_fts5(connection: sqlite3.Connection) -> bool:
    """Return True when this SQLite build has the FTS5 module compiled in."""
    try:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.__fts5_probe USING fts5(x)")
    except sqlite3.OperationalError:
        return False
    finally:
        try:
            connection.execute("DROP TABLE IF EXISTS temp.__fts5_probe")
        except sqlite3.OperationalError:
            pass
    return True


def _read_migrations(directory: Path) -> Sequence[tuple[int, Path]]:
    migrations: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        prefix = path.name.split("_", 1)[0]
        if not prefix.isdigit():
            continue
        migrations.append((int(prefix), path))
    migrations.sort(key=lambda item: item[0])
    return migrations


class Database:
    """Owns the SQLite connection and applies forward-only migrations.

    ``migrations/001_*.sql`` is applied unconditionally. Migrations whose name
    contains ``fts`` are applied only when the SQLite build supports FTS5; when
    it does not, text search falls back to ``LIKE`` scans (see
    ``Database.fts_enabled``).
    """

    def __init__(self, path: str | Path, migrations_dir: Path | None = None) -> None:
        self.path = Path(path)
        self.migrations_dir = Path(migrations_dir or MIGRATIONS_DIR)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self.fts_enabled = False

    # -- connection handling ------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Database.initialize() must be called before use")
        return self._connection

    def initialize(self) -> "Database":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        self._connection = connection
        self.fts_enabled = supports_fts5(connection)
        self.migrate()
        return self

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "Database":
        return self.initialize() if self._connection is None else self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- migrations ---------------------------------------------------------
    def migrate(self) -> list[str]:
        """Apply pending migrations; returns the names of applied files."""
        connection = self.connection
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        applied: list[str] = []
        for version, path in _read_migrations(self.migrations_dir):
            if version <= current:
                continue
            if "fts" in path.name and not self.fts_enabled:
                # Remember the version so it is not retried on every start.
                connection.execute(f"PRAGMA user_version = {version}")
                continue
            script = path.read_text(encoding="utf-8")
            with self.transaction():
                connection.executescript(script)
                connection.execute(f"PRAGMA user_version = {version}")
            applied.append(path.name)
        return applied

    # -- helpers ------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connection
        with self._lock:
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def execute(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self.connection.execute(sql, params)
            self.connection.commit()
            return cursor

    def query(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.connection.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- settings -----------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
