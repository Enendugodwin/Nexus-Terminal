"""Connection routing: direct first, then only explicitly configured paths."""

from __future__ import annotations

from dataclasses import dataclass

from nexus_remote.connections.interfaces import RouteKind
from nexus_remote.devices.models import Device
from nexus_remote.security.policies import PolicyEngine, RouteOption


@dataclass(frozen=True)
class RouteAttempt:
    method: str
    target: str
    allowed: bool
    reason: str = ""

    @property
    def kind(self) -> RouteKind:
        try:
            return RouteKind(self.method)
        except ValueError:  # pragma: no cover - defensive
            return RouteKind.DIRECT

    def describe(self) -> str:
        state = "try" if self.allowed else "skip"
        return f"[{state}] {self.method} -> {self.target or '-'} ({self.reason})"


class RoutingEngine:
    """Builds the ordered list of routes to attempt for a device.

    A route is attempted only when it is both configured on the device and
    permitted by policy. Nothing is discovered or guessed at runtime.
    """

    def __init__(self, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()

    def plan(self, device: Device) -> list[RouteAttempt]:
        options: list[RouteOption] = self.policy.allowed_routes(device)
        attempts = [
            RouteAttempt(method=option.method, target=option.target, allowed=option.allowed, reason=option.reason)
            for option in options
        ]
        # Direct is always first; the rest keep the operator's preference order.
        attempts.sort(key=lambda attempt: 0 if attempt.method == "direct" else 1)
        return attempts

    def next_route(self, device: Device, *, attempted: set[str] | None = None) -> RouteAttempt | None:
        attempted = attempted or set()
        for attempt in self.plan(device):
            if attempt.allowed and attempt.method not in attempted:
                return attempt
        return None

    def describe(self, device: Device) -> str:
        return "\n".join(attempt.describe() for attempt in self.plan(device))

    def failure_summary(self, device: Device, errors: dict[str, str]) -> str:
        """Explain every route that was tried and why it did not work."""
        lines = [f"Could not reach {device.name} ({device.hostname})."]
        attempts = self.plan(device)
        for attempt in attempts:
            if attempt.method in errors:
                lines.append(f"  {attempt.method}: failed - {errors[attempt.method]}")
            elif attempt.allowed:
                lines.append(f"  {attempt.method}: not attempted")
            else:
                lines.append(f"  {attempt.method}: unavailable - {attempt.reason}")
        lines.append(
            "No further routes are configured. Add a jump host, gateway or registered agent "
            "for this device if it must be reached from another network."
        )
        return "\n".join(lines)
