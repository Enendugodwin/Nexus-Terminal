"""Command memory: status transitions, search and statistics."""

from __future__ import annotations

import pytest

from nexus_remote.assistant.command_history import CommandHistory, CommandStatus


def test_record_and_recent(history):
    record_id = history.record("ls -la", environment="bash", device_id="dev1", source="user")
    assert record_id > 0
    records = history.recent()
    assert len(records) == 1
    assert records[0].command == "ls -la"
    assert records[0].status == CommandStatus.EXECUTED.value
    assert records[0].device_id == "dev1"


def test_empty_command_is_rejected(history):
    with pytest.raises(ValueError):
        history.record("   ")


def test_invalid_status_is_rejected(history):
    with pytest.raises(ValueError):
        history.record("ls", status="maybe")


def test_mark_confirmed_and_failed(history):
    confirmed = history.record("df -h")
    failed = history.record("broken-command")
    history.mark(confirmed, CommandStatus.CONFIRMED, exit_code=0)
    history.mark(failed, CommandStatus.FAILED, exit_code=127)
    records = {record.command: record for record in history.recent()}
    assert records["df -h"].status == "confirmed"
    assert records["df -h"].confirmed is True
    assert records["broken-command"].status == "failed"


def test_mark_with_notes(history):
    record_id = history.record("systemctl status nginx")
    history.mark(record_id, CommandStatus.CONFIRMED, notes="service healthy")
    assert history.recent()[0].notes == "service healthy"


def test_status_summary_distinguishes_suggestion_from_confirmation(history):
    history.record("suggested-only", status=CommandStatus.SUGGESTED)
    history.record("executed-only", status=CommandStatus.EXECUTED)
    history.record("proven", status=CommandStatus.CONFIRMED)
    stats = history.stats()
    assert stats["suggested"] == 1
    assert stats["executed"] == 1
    assert stats["confirmed"] == 1
    assert stats["total"] == 3


def test_search_matches_command_text(history):
    history.record("systemctl restart nginx", environment="bash")
    history.record("Get-Service", environment="powershell")
    results = history.search("nginx")
    assert len(results) == 1
    assert results[0].command == "systemctl restart nginx"


def test_search_falls_back_to_like_on_empty_result(history):
    history.record("apt-get update")
    assert history.search("zzz-not-present") == []
    assert len(history.search("apt-get")) == 1


def test_recent_filters_by_device_and_environment(history):
    history.record("ls", environment="bash", device_id="a")
    history.record("dir", environment="cmd", device_id="b")
    assert [record.command for record in history.recent(device_id="a")] == ["ls"]
    assert [record.command for record in history.recent(environment="cmd")] == ["dir"]


def test_frequent_and_confirmed(history):
    for _ in range(3):
        history.record("ls -la", environment="bash")
    record_id = history.record("df -h", environment="bash")
    history.mark(record_id, CommandStatus.CONFIRMED)
    frequent = dict(history.frequent(environment="bash"))
    assert frequent["ls -la"] == 3
    confirmed = history.confirmed(environment="bash")
    assert [record.command for record in confirmed] == ["df -h"]


def test_delete_and_clear(history):
    first = history.record("one")
    history.record("two", device_id="dev")
    history.delete(first)
    assert [record.command for record in history.recent()] == ["two"]
    assert history.clear(device_id="dev") == 1
    assert history.recent() == []


def test_history_survives_repository_reopen(tmp_path):
    from nexus_remote.storage.database import Database

    path = tmp_path / "history.db"
    first = Database(path).initialize()
    CommandHistory(first).record("uptime", environment="bash")
    first.close()

    second = Database(path).initialize()
    records = CommandHistory(second).recent()
    assert [record.command for record in records] == ["uptime"]
    second.close()
