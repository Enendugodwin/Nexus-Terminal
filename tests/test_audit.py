"""Audit trail: ordering, filters, redaction and file mirroring."""

from __future__ import annotations

import json

from nexus_remote.security.redact import MASK


def test_record_and_list(audit):
    audit.record("session", "session.connected", subject="ssh 10.0.0.5:22", device_id="dev1")
    events = audit.events()
    assert len(events) == 1
    assert events[0].action == "session.connected"
    assert events[0].device_id == "dev1"
    assert events[0].id is not None


def test_events_are_newest_first(audit):
    audit.record("device", "device.created", subject="first")
    audit.record("device", "device.updated", subject="second")
    subjects = [event.subject for event in audit.events()]
    assert subjects == ["second", "first"]


def test_filters_by_category_severity_and_device(audit):
    audit.record("session", "session.failed", severity="warning", device_id="a")
    audit.record("policy", "command.denied", severity="critical", device_id="b")
    audit.record("device", "device.deleted", severity="notice", device_id="a")
    assert len(audit.events(category="session")) == 1
    assert len(audit.events(severity="critical")) == 1
    assert len(audit.events(device_id="a")) == 2


def test_secret_in_detail_is_redacted_in_database(audit, database):
    audit.record("credential", "credential.stored", detail={"password": "hunter2", "username": "deploy"})
    row = database.query_one("SELECT detail FROM audit_events")
    assert "hunter2" not in row["detail"]
    assert MASK in row["detail"]
    assert "deploy" in row["detail"]


def test_secret_in_subject_is_redacted(audit):
    event = audit.record("session", "session.connected", subject="login with password=hunter2")
    assert "hunter2" not in event.subject


def test_audit_file_is_redacted(audit, tmp_path):
    audit.record("credential", "credential.stored", detail={"secret": "hunter2"})
    content = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "hunter2" not in content
    payload = json.loads(content.strip().splitlines()[0])
    assert payload["detail"]["secret"] == MASK


def test_nested_detail_is_sanitised(audit):
    event = audit.record(
        "session",
        "session.connected",
        detail={"options": {"password": "hunter2"}, "items": [{"token": "abc123"}]},
    )
    assert event.detail["options"]["password"] == MASK
    assert event.detail["items"][0]["token"] == MASK


def test_unknown_category_falls_back_to_system(audit):
    event = audit.record("nonsense", "something.happened")
    assert event.category == "system"


def test_counts_and_jsonl(audit):
    audit.record("session", "session.connected", severity="info")
    audit.record("policy", "command.denied", severity="warning")
    assert audit.count() == 2
    assert audit.count_by_severity() == {"info": 1, "warning": 1}
    lines = audit.as_jsonl().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["action"] == "command.denied"


def test_summary_is_readable(audit):
    event = audit.record("host_key", "host_key.trusted", severity="notice", subject="10.0.0.5 ssh-ed25519")
    assert "host_key" in event.summary()
    assert "10.0.0.5" in event.summary()
