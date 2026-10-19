"""Diagnostic result types shared by every check."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_PARTIAL = "partial"
STATUS_SKIPPED = "skipped"


@dataclass
class DiagnosticResult:
    """One test outcome.

    ``interpretation`` states only what the test actually establishes, and
    ``next_step`` suggests the next check rather than asserting a cause.
    """

    test: str
    destination: str
    result: str = STATUS_SKIPPED
    detail: str = ""
    error: str = ""
    interpretation: str = ""
    next_step: str = ""
    duration_ms: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result == STATUS_OK

    @property
    def failed(self) -> bool:
        return self.result == STATUS_FAILED

    def as_row(self) -> dict[str, str]:
        return {
            "test": self.test,
            "destination": self.destination,
            "result": self.result,
            "detail": self.detail or self.error,
            "interpretation": self.interpretation,
            "next_step": self.next_step,
        }

    def render(self) -> str:
        lines = [
            f"TEST:           {self.test}",
            f"DESTINATION:    {self.destination}",
            f"RESULT:         {self.result}",
        ]
        if self.detail:
            lines.append(f"DETAIL:         {self.detail}")
        if self.error:
            lines.append(f"ERROR:          {self.error}")
        if self.interpretation:
            lines.append(f"INTERPRETATION: {self.interpretation}")
        if self.next_step:
            lines.append(f"NEXT STEP:      {self.next_step}")
        return "\n".join(lines)
