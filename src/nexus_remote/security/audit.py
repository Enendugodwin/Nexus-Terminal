"""Audit trail for security-relevant activity.

Events are written to SQLite (for the Security Center view) and appended to a
JSON Lines file. Every field passes through :func:`nexus_remote.security.redact.redact`
first, so secrets cannot reach either destination.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus_remote.security.redact import MASK, SENSITIVE_KEY_NAMES, redact
from nexus_remote.storage.database import Database
from nexus_remote.util import json_dumps, json_loads, utcnow_iso

AUDIT_CATEGORIES = (
    "credential",
    "session",
    "device",
    "policy",
    "host_key",
    "transfer",
    "assistant",
    "routing",
    "system",
)

SEVERITIES = ("info", "notice", "warning", "critical")

OUTCOMES = ("success", "failure", "denied", "info")


@dataclass(frozen=True)
class AuditEvent:
    id: int | None
    ts: str
    category: str
    action: str
    severity: str = "info"
    outcome: str = "info"
    actor: str = "local-user"
    device_id: str | None = None
    subject: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        parts = [f"[{self.severity}]", self.category, self.action]
        if self.subject:
            parts.append(f"- {self.subject}")
        return " ".join(parts)


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            label = str(key)
            if label.casefold() in SENSITIVE_KEY_NAMES:
                cleaned[label] = MASK
            else:
                cleaned[label] = _sanitize(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


class AuditLog:
    """Append-only record of security-relevant events."""

    def __init__(self, database: Database, log_file: str | Path | None = None) -> None:
        self.db = database
        self.log_file = Path(log_file) if log_file else None
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        category: str,
        action: str,
        *,
        severity: str = "info",
        outcome: str = "info",
        device_id: str | None = None,
        subject: str = "",
        detail: dict[str, Any] | None = None,
        actor: str = "local-user",
    ) -> AuditEvent:
        if category not in AUDIT_CATEGORIES:
            category = "system"
        if severity not in SEVERITIES:
            severity = "info"
        if outcome not in OUTCOMES:
            outcome = "info"
        payload = _sanitize(json_loads(json_dumps(detail or {}), {}))
        event = AuditEvent(
            id=None,
            ts=utcnow_iso(),
            category=category,
            action=redact(action),
            severity=severity,
            outcome=outcome,
            actor=actor,
            device_id=device_id,
            subject=redact(subject),
            detail=payload,
        )
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO audit_events (ts, category, action, severity, outcome, actor, "
                "device_id, subject, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.ts,
                    event.category,
                    event.action,
                    event.severity,
                    event.outcome,
                    event.actor,
                    event.device_id,
                    event.subject,
                    json_dumps(payload),
                ),
            )
            event_id = cursor.lastrowid
        recorded = AuditEvent(**{**event.__dict__, "id": event_id})
        self._append_file(recorded)
        return recorded

    def _append_file(self, event: AuditEvent) -> None:
        if self.log_file is None:
            return
        line = json_dumps(
            {
                "id": event.id,
                "ts": event.ts,
                "category": event.category,
                "action": event.action,
                "severity": event.severity,
                "outcome": event.outcome,
                "actor": event.actor,
                "device_id": event.device_id,
                "subject": event.subject,
                "detail": event.detail,
            }
        )
        with open(self.log_file, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    # -- reads --------------------------------------------------------------
    def events(
        self,
        *,
        limit: int = 200,
        category: str | None = None,
        severity: str | None = None,
        device_id: str | None = None,
    ) -> list[AuditEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if device_id:
            clauses.append("device_id = ?")
            params.append(device_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        rows = self.db.query(
            f"SELECT * FROM audit_events {where} ORDER BY id DESC LIMIT ?", tuple(params)
        )
        return [
            AuditEvent(
                id=row["id"],
                ts=row["ts"],
                category=row["category"],
                action=row["action"],
                severity=row["severity"],
                outcome=row["outcome"],
                actor=row["actor"],
                device_id=row["device_id"],
                subject=row["subject"],
                detail=json_loads(row["detail"], {}),
            )
            for row in rows
        ]

    def count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM audit_events")
        return int(row["n"]) if row else 0

    def count_by_severity(self) -> dict[str, int]:
        rows = self.db.query(
            "SELECT severity, COUNT(*) AS n FROM audit_events GROUP BY severity ORDER BY severity"
        )
        return {row["severity"]: int(row["n"]) for row in rows}

    def as_jsonl(self, **kwargs: Any) -> str:
        return "\n".join(
            json.dumps(
                {
                    "id": event.id,
                    "ts": event.ts,
                    "category": event.category,
                    "action": event.action,
                    "severity": event.severity,
                    "outcome": event.outcome,
                    "subject": event.subject,
                    "detail": event.detail,
                },
                ensure_ascii=False,
            )
            for event in self.events(**kwargs)
        )
