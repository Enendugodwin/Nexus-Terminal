"""Command history and memory.

The application distinguishes three states, which is what stops the assistant
from treating a failed suggestion as a proven fix:

* ``suggested`` - offered to the user, never run.
* ``executed``  - the user ran it; the outcome is not yet known.
* ``confirmed`` - the user (or a later check) confirmed it worked.
* ``failed``    - it ran and did not work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from nexus_remote.storage.database import Database
from nexus_remote.util import utcnow_iso


class CommandStatus(str, Enum):
    SUGGESTED = "suggested"
    EXECUTED = "executed"
    CONFIRMED = "confirmed"
    FAILED = "failed"


@dataclass(frozen=True)
class CommandRecord:
    id: int
    ts: str
    command: str
    environment: str
    status: str
    source: str
    device_id: str | None = None
    exit_code: int | None = None
    risk: str = "unknown"
    notes: str = ""

    @property
    def confirmed(self) -> bool:
        return self.status == CommandStatus.CONFIRMED.value


class CommandHistory:
    """SQLite-backed command memory with FTS5 search when available."""

    def __init__(self, database: Database) -> None:
        self.db = database

    # -- writes -------------------------------------------------------------
    def record(
        self,
        command: str,
        *,
        device_id: str | None = None,
        environment: str = "unknown",
        status: CommandStatus | str = CommandStatus.EXECUTED,
        source: str = "history",
        exit_code: int | None = None,
        risk: str = "unknown",
        notes: str = "",
    ) -> int:
        text = (command or "").strip()
        if not text:
            raise ValueError("cannot record an empty command")
        value = status.value if isinstance(status, CommandStatus) else str(status)
        if value not in {item.value for item in CommandStatus}:
            raise ValueError(f"unknown command status {value!r}")
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO command_history (ts, device_id, environment, command, status, source, "
                "exit_code, risk, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    utcnow_iso(),
                    device_id,
                    environment,
                    text,
                    value,
                    source,
                    exit_code,
                    risk,
                    notes,
                ),
            )
            return int(cursor.lastrowid)

    def mark(
        self,
        record_id: int,
        status: CommandStatus | str,
        *,
        exit_code: int | None = None,
        notes: str | None = None,
    ) -> None:
        value = status.value if isinstance(status, CommandStatus) else str(status)
        if value not in {item.value for item in CommandStatus}:
            raise ValueError(f"unknown command status {value!r}")
        if notes is None:
            self.db.execute(
                "UPDATE command_history SET status = ?, exit_code = COALESCE(?, exit_code) WHERE id = ?",
                (value, exit_code, record_id),
            )
        else:
            self.db.execute(
                "UPDATE command_history SET status = ?, exit_code = COALESCE(?, exit_code), "
                "notes = ? WHERE id = ?",
                (value, exit_code, notes, record_id),
            )

    def delete(self, record_id: int) -> None:
        self.db.execute("DELETE FROM command_history WHERE id = ?", (record_id,))

    def clear(self, *, device_id: str | None = None) -> int:
        if device_id:
            cursor = self.db.execute("DELETE FROM command_history WHERE device_id = ?", (device_id,))
        else:
            cursor = self.db.execute("DELETE FROM command_history")
        return cursor.rowcount

    # -- reads --------------------------------------------------------------
    def recent(
        self,
        limit: int = 50,
        *,
        device_id: str | None = None,
        environment: str | None = None,
    ) -> list[CommandRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if device_id:
            clauses.append("device_id = ?")
            params.append(device_id)
        if environment:
            clauses.append("environment = ?")
            params.append(environment)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        rows = self.db.query(
            f"SELECT * FROM command_history {where} ORDER BY id DESC LIMIT ?", tuple(params)
        )
        return [self._row_to_record(row) for row in rows]

    def search(self, text: str, limit: int = 25) -> list[CommandRecord]:
        query = (text or "").strip()
        if not query:
            return self.recent(limit)
        if self.db.fts_enabled:
            try:
                rows = self.db.query(
                    "SELECT h.* FROM command_history_fts f JOIN command_history h ON h.id = f.rowid "
                    "WHERE command_history_fts MATCH ? ORDER BY h.id DESC LIMIT ?",
                    (_fts_query(query), int(limit)),
                )
                if rows:
                    return [self._row_to_record(row) for row in rows]
            except Exception:
                pass  # fall through to LIKE for odd FTS syntax
        rows = self.db.query(
            "SELECT * FROM command_history WHERE command LIKE ? OR notes LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", int(limit)),
        )
        return [self._row_to_record(row) for row in rows]

    def frequent(
        self, limit: int = 10, *, environment: str | None = None
    ) -> list[tuple[str, int]]:
        if environment:
            rows = self.db.query(
                "SELECT command, COUNT(*) AS n FROM command_history WHERE environment = ? "
                "GROUP BY command ORDER BY n DESC, MAX(id) DESC LIMIT ?",
                (environment, int(limit)),
            )
        else:
            rows = self.db.query(
                "SELECT command, COUNT(*) AS n FROM command_history "
                "GROUP BY command ORDER BY n DESC, MAX(id) DESC LIMIT ?",
                (int(limit),),
            )
        return [(row["command"], int(row["n"])) for row in rows]

    def confirmed(self, *, environment: str | None = None, limit: int = 50) -> list[CommandRecord]:
        if environment:
            rows = self.db.query(
                "SELECT * FROM command_history WHERE status = 'confirmed' AND environment = ? "
                "ORDER BY id DESC LIMIT ?",
                (environment, int(limit)),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM command_history WHERE status = 'confirmed' ORDER BY id DESC LIMIT ?",
                (int(limit),),
            )
        return [self._row_to_record(row) for row in rows]

    def stats(self) -> dict[str, int]:
        rows = self.db.query(
            "SELECT status, COUNT(*) AS n FROM command_history GROUP BY status"
        )
        stats = {row["status"]: int(row["n"]) for row in rows}
        stats["total"] = sum(stats.values())
        return stats

    # -- internals ----------------------------------------------------------
    @staticmethod
    def _row_to_record(row: Any) -> CommandRecord:
        return CommandRecord(
            id=row["id"],
            ts=row["ts"],
            command=row["command"],
            environment=row["environment"],
            status=row["status"],
            source=row["source"],
            device_id=row["device_id"],
            exit_code=row["exit_code"],
            risk=row["risk"],
            notes=row["notes"],
        )


def _fts_query(text: str) -> str:
    """Turn user input into a conservative FTS5 prefix query."""
    terms = [term for term in text.replace('"', " ").split() if term]
    if not terms:
        return '""'
    return " ".join(f'"{term}"*' for term in terms)
