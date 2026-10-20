"""SSH host-key verification and the pending-trust workflow."""

from __future__ import annotations

import pytest

from nexus_remote.security.host_keys import (
    HostKeyDecision,
    UntrustedHostKeyError,
    fingerprint_of,
    load_known_hosts_file,
)

paramiko = pytest.importorskip("paramiko")


@pytest.fixture(scope="module")
def key_a():
    return paramiko.RSAKey.generate(2048)


@pytest.fixture(scope="module")
def key_b():
    return paramiko.RSAKey.generate(2048)


def test_fingerprint_format(key_a):
    fingerprint = fingerprint_of(key_a)
    assert fingerprint.startswith("SHA256:")
    assert "=" not in fingerprint
    assert fingerprint == fingerprint_of(key_a.asbytes())


def test_unknown_key_is_not_trusted(host_keys, key_a):
    verdict = host_keys.verify("10.0.0.5", "ssh-rsa", key_a)
    assert verdict.decision is HostKeyDecision.UNKNOWN
    assert verdict.trusted is False
    assert "not been verified" in verdict.message
    assert verdict.fingerprint == fingerprint_of(key_a)


def test_trust_then_verify(host_keys, key_a):
    host_keys.trust("10.0.0.5", "ssh-rsa", key_a)
    verdict = host_keys.verify("10.0.0.5", "ssh-rsa", key_a)
    assert verdict.decision is HostKeyDecision.TRUSTED
    assert verdict.trusted is True


def test_changed_key_is_flagged(host_keys, key_a, key_b):
    host_keys.trust("10.0.0.5", "ssh-rsa", key_a)
    verdict = host_keys.verify("10.0.0.5", "ssh-rsa", key_b)
    assert verdict.decision is HostKeyDecision.CHANGED
    assert verdict.known_fingerprint == fingerprint_of(key_a)
    assert "HOST KEY CHANGED" in verdict.message


def test_revoked_key_is_not_trusted(host_keys, key_a):
    host_keys.trust("10.0.0.5", "ssh-rsa", key_a)
    assert host_keys.revoke("10.0.0.5") == 1
    verdict = host_keys.verify("10.0.0.5", "ssh-rsa", key_a)
    assert verdict.decision is HostKeyDecision.REVOKED


def test_pending_workflow(host_keys, key_a):
    verdict = host_keys.verify("new-host", "ssh-rsa", key_a)
    host_keys.record_pending(verdict, key_a)
    pending = host_keys.pending()
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"

    assert host_keys.verify("new-host", "ssh-rsa", key_a).trusted is False
    assert host_keys.trust_pending("new-host", "ssh-rsa") is True
    assert host_keys.verify("new-host", "ssh-rsa", key_a).trusted is True
    assert host_keys.pending() == []


def test_trust_pending_without_entry_returns_false(host_keys):
    assert host_keys.trust_pending("nothing", "ssh-rsa") is False


def test_forget_removes_entry(host_keys, key_a):
    host_keys.trust("10.0.0.5", "ssh-rsa", key_a)
    assert host_keys.forget("10.0.0.5", "ssh-rsa") == 1
    assert host_keys.entries() == []
    assert host_keys.verify("10.0.0.5", "ssh-rsa", key_a).decision is HostKeyDecision.UNKNOWN


def test_known_hosts_file_mirrors_trusted_keys(host_keys, key_a, tmp_path):
    host_keys.trust("10.0.0.5", "ssh-rsa", key_a)
    parsed = load_known_hosts_file(tmp_path / "known_hosts")
    assert len(parsed) == 1
    host, key_type, _blob = parsed[0]
    assert host == "10.0.0.5"
    assert key_type == "ssh-rsa"


def test_paramiko_policy_rejects_unknown_key(host_keys, key_a):
    policy = host_keys.paramiko_policy()
    with pytest.raises(UntrustedHostKeyError) as excinfo:
        policy.missing_host_key(None, "10.0.0.5", key_a)
    assert excinfo.value.verdict.decision is HostKeyDecision.UNKNOWN
    assert excinfo.value.key is key_a


def test_paramiko_policy_accepts_trusted_key(host_keys, key_a):
    host_keys.trust("10.0.0.5", "ssh-rsa", key_a)
    policy = host_keys.paramiko_policy()
    policy.missing_host_key(None, "10.0.0.5", key_a)  # must not raise


def test_entries_can_be_filtered_by_status(host_keys, key_a, key_b):
    host_keys.trust("a", "ssh-rsa", key_a)
    host_keys.record_pending(host_keys.verify("b", "ssh-rsa", key_b), key_b)
    assert len(host_keys.entries(status="trusted")) == 1
    assert len(host_keys.entries(status="pending")) == 1
    assert len(host_keys.entries()) == 2


def test_known_hosts_parser_skips_comments(tmp_path):
    path = tmp_path / "known_hosts"
    path.write_text("# comment\n\nhost ssh-rsa AAAA\n", encoding="utf-8")
    entries = load_known_hosts_file(path)
    assert entries == [("host", "ssh-rsa", "AAAA")]
