"""Deterministic policy: command risk, approvals, routes, relay restrictions."""

from __future__ import annotations

import pytest

from nexus_remote.devices.models import Device, RoutingPreferences
from nexus_remote.security.policies import PolicyEngine, RiskLevel, summarize_routes


@pytest.mark.parametrize(
    "command,expected",
    [
        ("ls -la /var/log", RiskLevel.READ_ONLY),
        ("cat /etc/hosts", RiskLevel.READ_ONLY),
        ("Get-Process", RiskLevel.READ_ONLY),
        ("show ip route", RiskLevel.READ_ONLY),
        ("df -h", RiskLevel.READ_ONLY),
        ("rm -rf /", RiskLevel.DESTRUCTIVE),
        ("mkfs.ext4 /dev/sdb1", RiskLevel.DESTRUCTIVE),
        ("dd if=/dev/zero of=/dev/sda", RiskLevel.DESTRUCTIVE),
        ("shutdown -h now", RiskLevel.DESTRUCTIVE),
        ("DROP TABLE users", RiskLevel.DESTRUCTIVE),
        ("systemctl restart nginx", RiskLevel.MODIFIES_STATE),
        ("apt-get install curl", RiskLevel.MODIFIES_STATE),
        ("git push origin main", RiskLevel.MODIFIES_STATE),
        ("", RiskLevel.UNKNOWN),
        ("some-vendor-command --flag", RiskLevel.UNKNOWN),
    ],
)
def test_classification(command, expected):
    assert PolicyEngine.classify(command) == expected


def test_read_only_command_is_allowed():
    decision = PolicyEngine().evaluate("ls -la")
    assert decision.allowed is True
    assert decision.requires_confirmation is False


def test_destructive_command_is_blocked_by_default():
    decision = PolicyEngine().evaluate("rm -rf /")
    assert decision.allowed is False
    assert decision.requires_confirmation is True
    assert "destructive" in decision.reason


def test_destructive_command_allowed_when_enabled_and_approved():
    policy = PolicyEngine(allow_destructive_commands=True)
    assert policy.evaluate("rm -rf /tmp/x", approved=False).allowed is False
    assert policy.evaluate("rm -rf /tmp/x", approved=True).allowed is True


def test_state_changing_command_requires_confirmation():
    decision = PolicyEngine().evaluate("systemctl restart nginx")
    assert decision.allowed is False
    assert decision.requires_confirmation is True
    assert PolicyEngine().evaluate("systemctl restart nginx", approved=True).allowed is True


def test_state_changes_can_be_confirmed_via_policy_setting():
    policy = PolicyEngine(require_confirmation_for_state_changes=False)
    assert policy.evaluate("systemctl restart nginx").allowed is True


def test_production_flag_is_carried_in_decision():
    decision = PolicyEngine().evaluate("apt-get upgrade", production=True)
    assert decision.environment == "unknown"
    assert decision.risk is RiskLevel.MODIFIES_STATE


def test_risk_ranking_is_ordered():
    assert RiskLevel.DESTRUCTIVE.rank > RiskLevel.MODIFIES_STATE.rank > RiskLevel.READ_ONLY.rank


def test_direct_route_is_always_first(device):
    options = PolicyEngine.allowed_routes(device)
    assert options[0].method == "direct"
    assert options[0].allowed is True
    assert options[0].target == device.hostname


def test_jump_host_route_requires_configuration(device):
    methods = {option.method: option for option in PolicyEngine.allowed_routes(device)}
    assert methods["jump_host"].allowed is False
    device.routing.jump_host = "bastion.example.com:22"
    methods = {option.method: option for option in PolicyEngine.allowed_routes(device)}
    assert methods["jump_host"].allowed is True


def test_configured_fallbacks_are_ordered(device):
    device.routing = RoutingPreferences(preferred_method="vpn", fallbacks=["jump_host", "relay"], jump_host="bastion")
    options = PolicyEngine.allowed_routes(device)
    methods = [option.method for option in options]
    assert methods[0] == "direct"
    assert methods[1] == "vpn"
    assert "jump_host" in methods
    assert "relay" in methods


def test_unconfigured_routes_are_denied(device):
    device.routing = RoutingPreferences(preferred_method="direct", fallbacks=["relay", "agent"])
    options = {option.method: option for option in PolicyEngine.allowed_routes(device)}
    assert options["relay"].allowed is False
    assert options["agent"].allowed is False


def test_relay_destinations_deny_by_default():
    policy = PolicyEngine()
    allowed, reason = policy.destination_allowed("10.0.0.5", 22)
    assert allowed is False
    assert "denied by default" in reason


def test_relay_allowlist_supports_cidr_host_port_and_domain():
    policy = PolicyEngine(relay_allowed_destinations=["10.0.0.0/24", "gateway.example.com:443", "*.corp.example.com"])
    assert policy.destination_allowed("10.0.0.7", 22)[0] is True
    assert policy.destination_allowed("10.0.1.7", 22)[0] is False
    assert policy.destination_allowed("gateway.example.com", 443)[0] is True
    assert policy.destination_allowed("gateway.example.com", 22)[0] is False
    assert policy.destination_allowed("db.corp.example.com", 5432)[0] is True
    assert policy.destination_allowed("evil.example.net", 22)[0] is False


def test_intrusive_tools_are_opt_in():
    policy = PolicyEngine()
    assert policy.intrusive_tools_allowed(True)[0] is False
    assert policy.intrusive_tools_allowed(False)[0] is True
    assert PolicyEngine(allow_intrusive_tools=True).intrusive_tools_allowed(True)[0] is True


def test_summarize_routes_is_human_readable(device):
    text = summarize_routes(PolicyEngine.allowed_routes(device))
    assert "direct: configured" in text
    assert "jump_host: unavailable" in text
