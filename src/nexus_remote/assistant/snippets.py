"""Saved command snippets (the user's own command library)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from nexus_remote.assistant.command_history import CommandRecord
from nexus_remote.storage.database import Database
from nexus_remote.util import json_dumps, json_loads, searchable, utcnow_iso


@dataclass
class Snippet:
    id: str
    name: str
    command: str
    environment: str = "unknown"
    description: str = ""
    verified: bool = False
    risk: str = "unknown"
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    @property
    def label(self) -> str:
        state = "verified" if self.verified else "unverified"
        return f"{self.name}  [{self.environment}, {state}]"


class SnippetStore:
    """CRUD for the command library stored in ``command_snippets``."""

    def __init__(self, database: Database) -> None:
        self.db = database

    def add(
        self,
        name: str,
        command: str,
        *,
        environment: str = "unknown",
        description: str = "",
        tags: Iterable[str] = (),
        risk: str = "unknown",
        verified: bool = False,
    ) -> Snippet:
        label = (name or "").strip() or command.strip()[:40]
        text = (command or "").strip()
        if not text:
            raise ValueError("a snippet needs a command")
        snippet = Snippet(
            id=uuid.uuid4().hex,
            name=label,
            command=text,
            environment=environment or "unknown",
            description=description,
            verified=bool(verified),
            risk=risk,
            tags=sorted({tag.strip() for tag in tags if tag and tag.strip()}),
        )
        self.db.execute(
            "INSERT INTO command_snippets (id, name, command, environment, description, verified, "
            "risk, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snippet.id,
                snippet.name,
                snippet.command,
                snippet.environment,
                snippet.description,
                int(snippet.verified),
                snippet.risk,
                json_dumps(snippet.tags),
                snippet.created_at,
                snippet.updated_at,
            ),
        )
        return snippet

    def update(self, snippet: Snippet) -> Snippet:
        snippet.updated_at = utcnow_iso()
        self.db.execute(
            "UPDATE command_snippets SET name = ?, command = ?, environment = ?, description = ?, "
            "verified = ?, risk = ?, tags = ?, updated_at = ? WHERE id = ?",
            (
                snippet.name,
                snippet.command,
                snippet.environment,
                snippet.description,
                int(snippet.verified),
                snippet.risk,
                json_dumps(snippet.tags),
                snippet.updated_at,
                snippet.id,
            ),
        )
        return snippet

    def delete(self, snippet_id: str) -> None:
        self.db.execute("DELETE FROM command_snippets WHERE id = ?", (snippet_id,))

    def get(self, snippet_id: str) -> Snippet | None:
        row = self.db.query_one("SELECT * FROM command_snippets WHERE id = ?", (snippet_id,))
        return self._row(row) if row else None

    def list(self, search: str = "", *, environment: str | None = None) -> list[Snippet]:
        rows = self.db.query("SELECT * FROM command_snippets ORDER BY name COLLATE NOCASE")
        snippets = [self._row(row) for row in rows]
        selected = []
        for snippet in snippets:
            if environment and snippet.environment != environment:
                continue
            if search and not searchable(
                f"{snippet.name} {snippet.command} {snippet.description} {' '.join(snippet.tags)}", search
            ):
                continue
            selected.append(snippet)
        if search:
            # Most relevant first: name matches beat command matches, and
            # prefix matches beat occurrences in the middle of the text.
            selected.sort(key=lambda snippet: (-_search_score(snippet, search), snippet.name.casefold()))
        return selected

    def from_history(
        self, record: CommandRecord, *, name: str | None = None, verified: bool = False
    ) -> Snippet:
        return self.add(
            name or f"{record.environment}: {record.command[:32]}",
            record.command,
            environment=record.environment,
            description=f"Saved from history ({record.ts})",
            risk=record.risk,
            verified=verified or record.confirmed,
        )

    @staticmethod
    def _row(row: Any) -> Snippet:
        return Snippet(
            id=row["id"],
            name=row["name"],
            command=row["command"],
            environment=row["environment"],
            description=row["description"],
            verified=bool(row["verified"]),
            risk=row["risk"],
            tags=list(json_loads(row["tags"], [])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _search_score(snippet: Snippet, search: str) -> int:
    """Rank a snippet for the search box: name first, then command."""
    needle = search.strip().casefold()
    if not needle:
        return 0
    name = snippet.name.casefold()
    command = snippet.command.casefold()
    score = 0
    if name == needle:
        score += 100
    elif name.startswith(needle):
        score += 80
    elif any(word.startswith(needle) for word in name.split()):
        score += 60
    if command.startswith(needle):
        score += 40
    elif any(word.startswith(needle) for word in command.split()):
        score += 20
    if needle in name or needle in command:
        score += 5
    return score
