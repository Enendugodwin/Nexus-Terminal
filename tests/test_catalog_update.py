"""Catalog size, feed validation, storage and vendor updates."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from nexus_remote.assistant.autocomplete import CompletionEngine, SuggestionSource
from nexus_remote.assistant.builtin_catalog import CATALOG, CATALOG_VERSION
from nexus_remote.assistant.catalog import (
    CatalogFormatError,
    count_new_entries,
    load_environment_map,
    merge_catalogs,
    metadata,
    parse_catalog,
    read_catalog,
    write_catalog,
)
from nexus_remote.assistant.catalog_update import CatalogUpdater

FEED_URL = "https://vendor.example/catalog.json"


def _feed(**overrides) -> dict:
    document = {
        "schema": 1,
        "version": "2026.10.20",
        "vendor": "Example Vendor",
        "environments": {
            "bash": [
                {"command": "apt list --upgradable", "summary": "list pending upgrades"},
                {"command": "systemd-analyze blame", "summary": "slow boot units"},
            ]
        },
    }
    document.update(overrides)
    return document


class _Response:
    def __init__(self, body: bytes = b"", *, status: int = 200, etag: str = "") -> None:
        self._body = body
        self.status = status
        self.headers = {"ETag": etag} if etag else {}

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._body)
        chunk = self._body[:size]
        self._body = self._body[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _opener(body: bytes, *, status: int = 200, etag: str = "", seen: list[Request] | None = None):
    def open_url(request, timeout=None):
        if seen is not None:
            seen.append(request)
        return _Response(body, status=status, etag=etag)

    return open_url


# -- bundled catalog --------------------------------------------------------
def test_bundled_catalog_is_substantially_larger():
    total = sum(len(entries) for entries in CATALOG.values())
    assert total >= 350
    assert set(CATALOG) == {"bash", "powershell", "cmd", "cisco", "panos"}
    for environment, entries in CATALOG.items():
        assert len(entries) >= 30, environment


def test_bundled_catalog_has_no_duplicate_commands():
    for environment, entries in CATALOG.items():
        names = [command.casefold() for command, _summary in entries]
        assert len(names) == len(set(names)), environment


def test_every_bundled_entry_is_a_single_line_with_a_summary():
    for environment, entries in CATALOG.items():
        for command, summary in entries:
            assert command.strip(), environment
            assert "\n" not in command and "\r" not in command
            assert summary and "\n" not in summary


def test_version_is_declared():
    assert CATALOG_VERSION


# -- validation -------------------------------------------------------------
def test_valid_feed_is_parsed_and_deduplicated():
    document = _feed()
    document["environments"]["bash"].append(
        {"command": "APT list --upgradable", "summary": "duplicate in other case"}
    )

    catalog = parse_catalog(document, source=FEED_URL)

    assert catalog.version == "2026.10.20"
    assert catalog.vendor == "Example Vendor"
    assert catalog.total == 2
    assert catalog.environments["bash"][0] == ("apt list --upgradable", "list pending upgrades")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schema": 2, "environments": {"bash": []}},
        {"schema": 1},
        {"schema": 1, "environments": {}},
        {"schema": 1, "environments": {"zsh": [{"command": "ls"}]}},
        {"schema": 1, "environments": {"bash": "ls"}},
        {"schema": 1, "environments": {"bash": [{"summary": "no command"}]}},
        {"schema": 1, "environments": {"bash": [{"command": ""}]}},
        {"schema": 1, "environments": {"bash": [{"command": "ls\nrm -rf /"}]}},
        {"schema": 1, "environments": {"bash": [{"command": "ls", "summary": "a\nb"}]}},
        {"schema": 1, "environments": {"bash": [{"command": "ls", "summary": 3}]}},
        {"schema": 1, "environments": {}, "version": 3},
        {"schema": 1, "environments": {"bash": []}, "vendor": "x" * 500},
    ],
)
def test_invalid_documents_are_rejected(payload):
    with pytest.raises(CatalogFormatError):
        parse_catalog(payload)


# -- storage ----------------------------------------------------------------
def test_round_trip_and_metadata(tmp_path):
    path = tmp_path / "catalog.json"
    catalog = parse_catalog(_feed(), source=FEED_URL)

    write_catalog(path, catalog, source=FEED_URL, etag='"abc"')

    stored = read_catalog(path)
    assert stored is not None
    assert stored.version == "2026.10.20"
    assert stored.source == FEED_URL
    assert stored.etag == '"abc"'
    assert stored.fetched_at
    info = metadata(path)
    assert info["total"] == 2
    assert info["counts"] == {"bash": 2}
    assert info["fetched_at"] == stored.fetched_at


def test_write_is_atomic_and_leaves_no_temporary_files(tmp_path):
    path = tmp_path / "catalog.json"

    write_catalog(path, parse_catalog(_feed()), source=FEED_URL)

    assert path.exists()
    assert list(tmp_path.glob("catalog-*.tmp")) == []


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("{not json", encoding="utf-8")

    assert read_catalog(path) is None
    assert load_environment_map(path) == {}
    assert metadata(path) == {}


def test_missing_file_is_ignored(tmp_path):
    assert read_catalog(tmp_path / "absent.json") is None
    assert load_environment_map(tmp_path / "absent.json") == {}


# -- merging ----------------------------------------------------------------
def test_builtin_entries_win_and_downloads_only_add():
    builtin = {"bash": [("uptime", "system load and uptime")]}
    downloaded = {
        "bash": [
            ("uptime", "vendor wording that must not replace the built-in"),
            ("vmstat 1 5", "vendor addition"),
        ],
        "powershell": [("Get-PSDrive", "vendor addition")],
    }

    merged = merge_catalogs(builtin, downloaded)

    assert ("uptime", "system load and uptime") in merged["bash"]
    assert ("vmstat 1 5", "vendor addition") in merged["bash"]
    assert merged["powershell"] == [("Get-PSDrive", "vendor addition")]
    assert count_new_entries(builtin, downloaded) == 2


def test_engine_labels_downloaded_entries_as_catalog():
    engine = CompletionEngine(
        downloaded={
            "bash": [
                ("uptime", "vendor duplicate"),
                ("apt list --upgradable", "vendor addition"),
            ]
        }
    )

    definitions = {definition.name: definition for definition in engine.definitions("bash")}
    assert definitions["uptime"].source is SuggestionSource.VERIFIED
    assert definitions["apt list --upgradable"].source is SuggestionSource.CATALOG

    suggestions = engine.suggest("apt list", "bash")
    assert suggestions[0].text == "apt list --upgradable"
    assert suggestions[0].label == "Catalog (downloaded)"


def test_engine_reports_counts():
    engine = CompletionEngine()

    counts = engine.counts()

    assert counts["bash"] == len(CATALOG["bash"])
    assert engine.total() == sum(len(entries) for entries in CATALOG.values())


# -- updater ----------------------------------------------------------------
def test_update_requires_https(tmp_path):
    result = CatalogUpdater(tmp_path / "catalog.json").update("http://vendor.example/catalog.json")

    assert result.status == "error"
    assert "https" in result.message


def test_update_without_a_feed_is_not_an_error(tmp_path):
    result = CatalogUpdater(tmp_path / "catalog.json").update("")

    assert result.status == "not-configured"
    assert result.ok


def test_successful_update_is_stored(tmp_path):
    path = tmp_path / "catalog.json"
    updater = CatalogUpdater(path, opener=_opener(json.dumps(_feed()).encode(), etag='"v1"'))

    result = updater.update(FEED_URL)

    assert result.status == "updated"
    assert result.version == "2026.10.20"
    assert result.total == 2
    assert result.added == 2  # neither command exists in the bundled catalog
    stored = read_catalog(path)
    assert stored is not None and stored.source == FEED_URL and stored.etag == '"v1"'


def test_added_count_ignores_bundled_duplicates(tmp_path):
    document = _feed(environments={"bash": [{"command": "uptime", "summary": "dupe"}]})
    updater = CatalogUpdater(
        tmp_path / "catalog.json", opener=_opener(json.dumps(document).encode())
    )

    result = updater.update(FEED_URL)

    assert result.status == "updated"
    assert result.added == 0


def test_second_check_sends_the_etag(tmp_path):
    path = tmp_path / "catalog.json"
    first = CatalogUpdater(path, opener=_opener(json.dumps(_feed()).encode(), etag='"v1"'))
    first.update(FEED_URL)

    seen: list[Request] = []
    second = CatalogUpdater(
        path, opener=_opener(b"", status=304, seen=seen)
    )
    result = second.update(FEED_URL)

    assert result.status == "unchanged"
    assert result.total == 2
    headers = {key.lower(): value for key, value in seen[0].headers.items()}
    assert headers.get("if-none-match") == '"v1"'


def test_http_error_keeps_the_previous_catalog(tmp_path):
    path = tmp_path / "catalog.json"
    write_catalog(path, parse_catalog(_feed()), source=FEED_URL)
    before = path.read_bytes()

    def failing(request, timeout=None):
        raise HTTPError(request.full_url, 500, "boom", None, None)

    result = CatalogUpdater(path, opener=failing).update(FEED_URL)

    assert result.status == "error"
    assert "500" in result.message
    assert path.read_bytes() == before


def test_network_error_is_reported_plainly(tmp_path):
    def offline(request, timeout=None):
        raise URLError("no route to host")

    result = CatalogUpdater(tmp_path / "catalog.json", opener=offline).update(FEED_URL)

    assert result.status == "error"
    assert "Could not reach" in result.message


def test_invalid_feed_keeps_the_previous_catalog(tmp_path):
    path = tmp_path / "catalog.json"
    write_catalog(path, parse_catalog(_feed()), source=FEED_URL)
    before = path.read_bytes()

    result = CatalogUpdater(
        path, opener=_opener(json.dumps({"schema": 99}).encode())
    ).update(FEED_URL)

    assert result.status == "error"
    assert "rejected" in result.message
    assert path.read_bytes() == before


def test_oversized_feed_is_rejected(tmp_path):
    updater = CatalogUpdater(
        tmp_path / "catalog.json",
        max_bytes=100,
        opener=_opener(json.dumps(_feed()).encode()),
    )

    result = updater.update(FEED_URL)

    assert result.status == "error"
    assert "size limit" in result.message
    assert not (tmp_path / "catalog.json").exists()


def test_unsupported_content_is_rejected(tmp_path):
    result = CatalogUpdater(
        tmp_path / "catalog.json", opener=_opener(b"\xff\xfe not json")
    ).update(FEED_URL)

    assert result.status == "error"


# -- services integration ---------------------------------------------------
def test_services_save_and_expose_feed_settings(services):
    assert services.catalog_feed_url == ""
    assert services.catalog_auto_check is True

    services.save_catalog_feed_url("  https://vendor.example/catalog.json ")
    services.save_catalog_auto_check(False)

    assert services.catalog_feed_url == FEED_URL
    assert services.catalog_auto_check is False


def test_services_reload_catalog_from_disk(services):
    write_catalog(
        services.paths.catalog,
        parse_catalog(_feed()),
        source=FEED_URL,
    )
    before = services.autocomplete.total()

    services.reload_catalog()

    assert services.autocomplete.total() == before + 2
    assert any(
        definition.name == "apt list --upgradable"
        for definition in services.autocomplete.definitions("bash")
    )


def test_services_ignore_a_corrupt_downloaded_catalog(services):
    Path(services.paths.catalog).write_text("{broken", encoding="utf-8")

    services.reload_catalog()

    assert services.autocomplete.total() == sum(len(entries) for entries in CATALOG.values())
