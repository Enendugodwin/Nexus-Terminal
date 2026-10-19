"""Command snippet library."""

from __future__ import annotations

import pytest

from nexus_remote.assistant.command_history import CommandStatus


def test_add_and_list(snippets):
    snippet = snippets.add("list logs", "tail -f /var/log/syslog", environment="bash", tags=["logs"])
    assert snippet.verified is False
    listed = snippets.list()
    assert len(listed) == 1
    assert listed[0].name == "list logs"
    assert listed[0].tags == ["logs"]


def test_add_requires_command(snippets):
    with pytest.raises(ValueError):
        snippets.add("empty", "   ")


def test_name_defaults_to_command(snippets):
    snippet = snippets.add("", "uptime")
    assert snippet.name == "uptime"


def test_environment_filter_and_search(snippets):
    snippets.add("ps", "Get-Process", environment="powershell")
    snippets.add("ps", "ps aux", environment="bash")
    assert len(snippets.list()) == 2
    assert [snippet.command for snippet in snippets.list(environment="bash")] == ["ps aux"]
    assert [snippet.command for snippet in snippets.list("Get-")] == ["Get-Process"]
    assert [snippet.command for snippet in snippets.list("ps")] == ["ps aux", "Get-Process"]


def test_update_and_delete(snippets):
    snippet = snippets.add("check", "uptime", environment="bash")
    snippet.description = "load average"
    snippet.verified = True
    snippets.update(snippet)
    reloaded = snippets.get(snippet.id)
    assert reloaded.description == "load average"
    assert reloaded.verified is True
    snippets.delete(snippet.id)
    assert snippets.get(snippet.id) is None


def test_from_confirmed_history_is_marked_verified(snippets, history):
    record_id = history.record("systemctl restart nginx", environment="bash")
    history.mark(record_id, CommandStatus.CONFIRMED, exit_code=0)
    record = history.recent()[0]
    snippet = snippets.from_history(record)
    assert snippet.verified is True
    assert snippet.command == "systemctl restart nginx"
    assert snippet.environment == "bash"


def test_from_suggested_history_is_unverified(snippets, history):
    history.record("rm -rf /tmp/x", environment="bash", status=CommandStatus.SUGGESTED)
    snippet = snippets.from_history(history.recent()[0], name="cleanup")
    assert snippet.verified is False
    assert snippet.name == "cleanup"
