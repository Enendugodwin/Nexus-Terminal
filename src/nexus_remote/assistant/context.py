"""Bounded snapshots of the open terminal, used as optional AI context.

Nothing here touches the network. The text comes from the terminal buffer,
which is untrusted: the prompt builder fences it and the model is told to treat
it as data rather than instructions. Snapshots are capped in lines and
characters so a question never turns into a large upload.
"""

from __future__ import annotations

from collections.abc import Iterable

#: How many trailing output lines a snapshot may carry.
MAX_LINES = 20

#: Hard cap on the output part of a snapshot, in characters.
MAX_CHARS = 1500

#: How many trailing rows a caller should read from the terminal buffer. The
#: snapshot keeps 20 lines and the error tab 80, so this covers both without
#: converting the whole scrollback on every question.
TERMINAL_TAIL_ROWS = 200


def last_lines(text: str, *, limit: int = MAX_LINES, max_chars: int = MAX_CHARS) -> str:
    """Return the last non-blank ``limit`` lines, capped at ``max_chars``."""

    lines = [line.rstrip() for line in (text or "").splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    lines = [line for line in lines if line.strip()][-max(1, limit) :]
    snapshot = "\n".join(lines)
    if len(snapshot) > max_chars:
        snapshot = snapshot[-max_chars:]
    return snapshot


def terminal_snapshot(
    *,
    shell: str,
    details: Iterable[tuple[str, str]] = (),
    output: str = "",
    limit: int = MAX_LINES,
    max_chars: int = MAX_CHARS,
) -> str:
    """Build a short, deterministic description of the open console.

    ``details`` are label/value pairs such as ``("Working directory", cwd)``;
    empty values are skipped, so a snapshot only says what is known.
    """

    lines: list[str] = [f"Shell: {shell}"] if shell else []
    for label, value in details:
        text = (value or "").strip()
        if text:
            lines.append(f"{label}: {text}")
    tail = last_lines(output, limit=limit, max_chars=max_chars)
    if tail:
        lines.append("Recent terminal output:")
        lines.append(tail)
    return "\n".join(lines)


def summarise(snapshot: str) -> str:
    """One line for the UI: what was attached, and how much of it."""

    lines = [line for line in (snapshot or "").strip().splitlines() if line.strip()]
    if not lines:
        return ""
    head = lines[0][:100]
    return f"{head} - {len(lines)} line{'s' if len(lines) != 1 else ''}"
