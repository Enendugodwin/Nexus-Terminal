"""Shell-aware autocomplete: isolation, provenance and performance."""

from __future__ import annotations

import time

from nexus_remote.assistant.autocomplete import (
    CompletionEngine,
    Suggestion,
    SuggestionSource,
    environment_for_os,
    normalize_environment,
)


def test_environment_normalisation():
    assert normalize_environment("linux") == "bash"
    assert normalize_environment("PowerShell") == "powershell"
    assert normalize_environment("pan-os") == "panos"
    assert normalize_environment(None) == "unknown"
    assert normalize_environment("amiga") == "unknown"


def test_environment_for_os():
    assert environment_for_os("linux") == "bash"
    assert environment_for_os("windows") == "powershell"
    assert environment_for_os("network") == "unknown"
    assert environment_for_os("macos") == "unknown"


def test_bash_suggestions_include_verified_commands():
    engine = CompletionEngine()
    suggestions = engine.suggest("l", "bash")
    texts = [suggestion.text for suggestion in suggestions]
    assert "ls -la" in texts
    assert all(suggestion.source is SuggestionSource.VERIFIED for suggestion in suggestions)


def test_cisco_commands_are_not_offered_in_bash():
    engine = CompletionEngine()
    bash = {suggestion.text for suggestion in engine.suggest("show", "bash", limit=50)}
    cisco = {suggestion.text for suggestion in engine.suggest("show", "cisco", limit=50)}
    assert "show version" in cisco
    assert "show version" not in bash


def test_powershell_commands_are_not_offered_in_bash():
    engine = CompletionEngine()
    bash = {suggestion.text for suggestion in engine.suggest("Get-", "bash", limit=50)}
    pwsh = {suggestion.text for suggestion in engine.suggest("Get-", "powershell", limit=50)}
    assert not bash
    assert "Get-Process" in pwsh


def test_prefix_matches_rank_first():
    engine = CompletionEngine()
    suggestions = engine.suggest("show ip", "cisco")
    assert suggestions[0].text.startswith("show ip")


def test_history_only_command_gets_history_label():
    engine = CompletionEngine()
    suggestions = engine.suggest("deploy", "bash", history=["deploy.sh --production"])
    assert suggestions
    assert suggestions[0].text == "deploy.sh --production"
    assert suggestions[0].source is SuggestionSource.HISTORY
    assert suggestions[0].label == "History"


def test_history_boosts_catalog_entry():
    engine = CompletionEngine()
    without = engine.suggest("ls -la", "bash")[0]
    with_history = engine.suggest("ls -la", "bash", history=["ls -la"] * 5)[0]
    assert with_history.text == without.text
    assert with_history.score > without.score


def test_unknown_environment_returns_nothing():
    engine = CompletionEngine()
    assert engine.suggest("ls", "unknown") == []
    assert engine.suggest("ls", "amiga") == []


def test_empty_prefix_returns_suggestions():
    engine = CompletionEngine()
    assert engine.suggest("", "bash")


def test_limit_is_respected():
    engine = CompletionEngine()
    assert len(engine.suggest("", "bash", limit=3)) == 3


def test_explain_describes_known_command():
    engine = CompletionEngine()
    explanation = engine.explain("df -h", "bash")
    assert "filesystem usage" in explanation
    assert "Verified" in explanation


def test_explain_flags_unknown_command():
    engine = CompletionEngine()
    assert "unverified" in engine.explain("frobnicate --all", "bash").lower()


def test_ai_suggestions_are_labelled_unverified():
    engine = CompletionEngine()
    extra = [
        Suggestion(
            text="Get-CimInstance Win32_Service",
            source=SuggestionSource.AI_GENERATED,
            environment="powershell",
            summary="list services",
        )
    ]
    suggestions = engine.suggest("Get-Cim", "powershell", include_ai=extra)
    match = next(item for item in suggestions if item.text.startswith("Get-CimInstance Win32_Service"))
    assert match.source is SuggestionSource.AI_GENERATED
    assert match.source.trusted is False
    assert "unverified" in match.source.label.lower()


def test_suggestions_are_fast():
    engine = CompletionEngine()
    started = time.perf_counter()
    for _ in range(50):
        engine.suggest("s", "bash")
    elapsed_ms = (time.perf_counter() - started) * 1000 / 50
    assert elapsed_ms < 100, f"autocomplete took {elapsed_ms:.1f} ms per call"
