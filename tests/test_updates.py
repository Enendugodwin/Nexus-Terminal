"""Update manifest validation, verified downloads and installer hand-off."""

from __future__ import annotations

import hashlib
import io
import json

import pytest

from nexus_remote.updates import (
    UpdateChecker,
    is_newer,
    launch_installer,
    version_tuple,
)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = {}

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _manifest(**overrides) -> dict:
    payload = {
        "schema": 1,
        "version": "0.3.0",
        "url": "https://downloads.example.com/NexusRemoteSetup-0.3.0.exe",
        "sha256": "a" * 64,
        "size": 1234,
        "notes": "Fixes and themes",
        "published": "2026-11-01",
    }
    payload.update(overrides)
    return payload


def _opener_for(payload, requests=None):
    body = json.dumps(payload).encode("utf-8")

    def opener(request, timeout=None):
        if requests is not None:
            requests.append((request, timeout))
        return FakeResponse(body)

    return opener


def test_version_comparison():
    assert version_tuple("v0.2.0") == (0, 2, 0)
    assert version_tuple("garbage") == (0,)
    assert is_newer("0.3.0", "0.2.0")
    assert is_newer("1.0", "0.9.9")
    assert not is_newer("0.2.0", "0.2.0")
    assert not is_newer("0.2.0", "0.2.1")


def test_check_reports_an_available_update():
    checker = UpdateChecker("https://updates.example.com/latest.json", current_version="0.2.0")
    checker._opener = _opener_for(_manifest())

    result = checker.check()

    assert result.status == "available"
    assert result.available
    assert result.info is not None
    assert result.info.version == "0.3.0"
    assert result.info.notes == "Fixes and themes"


def test_check_reports_current_when_not_newer():
    checker = UpdateChecker("https://updates.example.com/latest.json", current_version="0.3.0")
    checker._opener = _opener_for(_manifest())

    result = checker.check()

    assert result.status == "current"
    assert not result.available


def test_check_requires_https_and_configuration():
    assert UpdateChecker("", current_version="0.2.0").check().status == "not-configured"
    assert (
        UpdateChecker("http://updates.example.com/latest.json", current_version="0.2.0").check().status
        == "error"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": 99},
        {"version": "not-a-version"},
        {"url": "http://downloads.example.com/x.exe"},
        {"url": "https://downloads.example.com/x.zip"},
        {"sha256": "short"},
        {"size": -1},
    ],
)
def test_invalid_manifests_are_rejected(overrides):
    checker = UpdateChecker("https://updates.example.com/latest.json", current_version="0.2.0")
    checker._opener = _opener_for(_manifest(**overrides))

    result = checker.check()

    assert result.status == "error"
    assert result.info is None


def test_network_failures_are_results_not_exceptions():
    def opener(_request, timeout=None):
        raise OSError("no route to host")

    checker = UpdateChecker("https://updates.example.com/latest.json", current_version="0.2.0")
    checker._opener = opener

    result = checker.check()

    assert result.status == "error"
    assert "no route" in result.message


def test_download_verifies_hash_and_writes_the_file(tmp_path):
    body = b"MZ" + b"installer-bytes" * 100
    digest = hashlib.sha256(body).hexdigest()
    manifest = _manifest(size=len(body), sha256=digest)
    checker = UpdateChecker("https://updates.example.com/latest.json", current_version="0.2.0")
    checker._opener = _opener_for(manifest)
    info = checker.check().info
    assert info is not None

    def download_opener(_request, timeout=None):
        return FakeResponse(body)

    checker._opener = download_opener
    progress: list[tuple[int, int]] = []
    result = checker.download(info, tmp_path, progress=lambda done, total: progress.append((done, total)))

    assert result.ok
    assert result.path is not None and result.path.is_file()
    assert result.path.read_bytes() == body
    assert not result.path.with_suffix(".exe.part").exists()
    assert progress and progress[-1][0] == len(body)


def test_download_discards_a_hash_mismatch(tmp_path):
    body = b"MZ tampered installer"
    checker = UpdateChecker("https://updates.example.com/latest.json", current_version="0.2.0")
    checker._opener = _opener_for(_manifest(size=len(body), sha256="b" * 64))
    info = checker.check().info
    assert info is not None
    checker._opener = lambda _request, timeout=None: FakeResponse(body)

    result = checker.download(info, tmp_path)

    assert result.status == "error"
    assert "SHA-256" in result.message
    assert not any(tmp_path.iterdir())


def test_download_rejects_a_size_mismatch(tmp_path):
    body = b"MZ short"
    digest = hashlib.sha256(body).hexdigest()
    checker = UpdateChecker("https://updates.example.com/latest.json", current_version="0.2.0")
    checker._opener = _opener_for(_manifest(size=9999, sha256=digest))
    info = checker.check().info
    assert info is not None
    checker._opener = lambda _request, timeout=None: FakeResponse(body)

    result = checker.download(info, tmp_path)

    assert result.status == "error"
    assert "size" in result.message


def test_launch_installer_rejects_missing_or_non_exe_files(tmp_path):
    missing = tmp_path / "missing.exe"
    text_file = tmp_path / "notes.txt"
    text_file.write_text("not an installer", encoding="utf-8")

    assert launch_installer(missing) is False
    if __import__("sys").platform == "win32":
        assert launch_installer(text_file) is False
