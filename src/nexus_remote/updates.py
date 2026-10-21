"""Application update checks and installer download.

The design mirrors the vendor catalog updater:

* HTTPS only, one small manifest request, bounded time and size.
* A candidate is only offered when its version is newer than the running build.
* The installer is downloaded on explicit request, checked against the SHA-256
  in the manifest, and then handed to Windows - never launched automatically.
* Every failure is returned as a result object; being offline is normal.

The manifest is a JSON document:

    {
      "schema": 1,
      "version": "0.3.0",
      "url": "https://downloads.example.com/NexusRemoteSetup-0.3.0.exe",
      "sha256": "<64 hex characters>",
      "size": 48231424,
      "notes": "What changed...",
      "published": "2026-11-01"
    }
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from nexus_remote import config

MAX_MANIFEST_BYTES = 128_000
MAX_INSTALLER_BYTES = 300 * 1024 * 1024
DEFAULT_TIMEOUT = 15.0
CHUNK = 256 * 1024
_SCHEMA = 1
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,3}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    sha256: str
    size: int = 0
    notes: str = ""
    published: str = ""


@dataclass(frozen=True)
class UpdateCheckResult:
    """Outcome of a manifest check (always returned, never raised)."""

    status: str  # available | current | not-configured | error
    message: str = ""
    info: UpdateInfo | None = None

    @property
    def available(self) -> bool:
        return self.status == "available" and self.info is not None


@dataclass(frozen=True)
class DownloadResult:
    status: str  # downloaded | error
    message: str = ""
    path: Path | None = None
    info: UpdateInfo | None = None

    @property
    def ok(self) -> bool:
        return self.status == "downloaded" and self.path is not None


def version_tuple(value: str) -> tuple[int, ...]:
    """Parse dotted numeric versions; non-numeric input sorts as zero."""
    text = str(value or "").strip().lstrip("vV")
    if not _VERSION_RE.match(text):
        return (0,)
    return tuple(int(part) for part in text.split("."))


def is_newer(candidate: str, current: str) -> bool:
    left, right = version_tuple(candidate), version_tuple(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _https(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def _read_bounded(response: object, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(CHUNK, limit - total + 1))  # type: ignore[attr-defined]
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError("the update manifest is larger than the allowed limit")
        chunks.append(chunk)
    return b"".join(chunks)


class UpdateChecker:
    """Reads a manifest and (on request) downloads a verified installer."""

    def __init__(
        self,
        feed_url: str,
        *,
        current_version: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self.feed_url = (feed_url or "").strip()
        self.current_version = current_version or config.APP_VERSION
        self.timeout = timeout
        self._opener = opener

    # -- manifest -----------------------------------------------------------
    def check(self) -> UpdateCheckResult:
        if not self.feed_url:
            return UpdateCheckResult(
                status="not-configured",
                message="No update feed is configured. Set one in Settings to enable update checks.",
            )
        if not _https(self.feed_url):
            return UpdateCheckResult(
                status="error",
                message="The update feed must be an https:// URL.",
            )

        request = Request(
            self.feed_url,
            headers={
                "User-Agent": f"{config.APP_NAME}/{self.current_version} updater",
                "Accept": "application/json",
                "Cache-Control": "no-cache",
            },
        )
        try:
            response = self._opener(request, timeout=self.timeout)
            with response:
                status = getattr(response, "status", 200)
                if status != 200:
                    return UpdateCheckResult(
                        status="error",
                        message=f"The update server answered HTTP {status}.",
                    )
                body = _read_bounded(response, MAX_MANIFEST_BYTES)
        except HTTPError as exc:
            return UpdateCheckResult(
                status="error",
                message=f"The update server answered HTTP {exc.code}.",
            )
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            return UpdateCheckResult(
                status="error",
                message=f"Could not reach the update server: {exc}",
            )
        except ValueError as exc:
            return UpdateCheckResult(status="error", message=str(exc))

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return UpdateCheckResult(
                status="error", message="The update manifest is not valid JSON."
            )
        info, error = _parse_manifest(payload)
        if error:
            return UpdateCheckResult(status="error", message=error)
        assert info is not None
        if not is_newer(info.version, self.current_version):
            return UpdateCheckResult(
                status="current",
                message=f"You are running the latest version ({self.current_version}).",
                info=info,
            )
        return UpdateCheckResult(
            status="available",
            message=f"Version {info.version} is available (you have {self.current_version}).",
            info=info,
        )

    # -- installer ----------------------------------------------------------
    def download(
        self,
        info: UpdateInfo,
        destination_dir: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> DownloadResult:
        if not _https(info.url):
            return DownloadResult(status="error", message="The installer URL must use https://.")
        if not _SHA256_RE.match(info.sha256):
            return DownloadResult(status="error", message="The manifest has no valid SHA-256 digest.")
        name = Path(urlparse(info.url).path).name or f"NexusRemoteSetup-{info.version}.exe"
        if not name.lower().endswith(".exe"):
            return DownloadResult(
                status="error",
                message="The update asset must be a Windows .exe installer.",
            )
        destination_dir.mkdir(parents=True, exist_ok=True)
        final_path = destination_dir / name
        part_path = final_path.with_suffix(final_path.suffix + ".part")
        request = Request(
            info.url,
            headers={
                "User-Agent": f"{config.APP_NAME}/{self.current_version} updater",
                "Accept": "application/octet-stream",
            },
        )
        digest = hashlib.sha256()
        total = 0
        try:
            response = self._opener(request, timeout=self.timeout)
            with response, part_path.open("wb") as handle:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_INSTALLER_BYTES:
                        raise ValueError("the installer is larger than the allowed limit")
                    digest.update(chunk)
                    handle.write(chunk)
                    if progress is not None:
                        progress(total, info.size or 0)
        except HTTPError as exc:
            part_path.unlink(missing_ok=True)
            return DownloadResult(
                status="error", message=f"The download server answered HTTP {exc.code}."
            )
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            part_path.unlink(missing_ok=True)
            return DownloadResult(status="error", message=f"Could not download the installer: {exc}")
        except ValueError as exc:
            part_path.unlink(missing_ok=True)
            return DownloadResult(status="error", message=str(exc))

        if info.size and total != info.size:
            part_path.unlink(missing_ok=True)
            return DownloadResult(
                status="error",
                message=f"The installer size did not match the manifest ({total} != {info.size}).",
            )
        actual = digest.hexdigest()
        if actual.lower() != info.sha256.lower():
            part_path.unlink(missing_ok=True)
            return DownloadResult(
                status="error",
                message="The installer failed its SHA-256 check and was discarded.",
            )
        try:
            os.replace(part_path, final_path)
        except OSError as exc:
            part_path.unlink(missing_ok=True)
            return DownloadResult(status="error", message=f"Could not save the installer: {exc}")
        return DownloadResult(
            status="downloaded",
            path=final_path,
            info=info,
            message=f"Downloaded and verified {final_path.name}.",
        )


def _parse_manifest(payload: object) -> tuple[UpdateInfo | None, str]:
    if not isinstance(payload, dict):
        return None, "The update manifest must be a JSON object."
    if payload.get("schema") != _SCHEMA:
        return None, "The update manifest uses an unsupported schema version."
    version = str(payload.get("version", "")).strip()
    if not _VERSION_RE.match(version):
        return None, "The update manifest has an invalid version."
    url = str(payload.get("url", "")).strip()
    if not _https(url):
        return None, "The update manifest has an invalid installer URL (https required)."
    if not urlparse(url).path.lower().endswith(".exe"):
        return None, "The update manifest installer URL must point to a Windows .exe file."
    sha256 = str(payload.get("sha256", "")).strip()
    if not _SHA256_RE.match(sha256):
        return None, "The update manifest has an invalid SHA-256 digest."
    size = payload.get("size", 0)
    try:
        size = int(size or 0)
    except (TypeError, ValueError):
        return None, "The update manifest has an invalid installer size."
    if size < 0 or size > MAX_INSTALLER_BYTES:
        return None, "The update manifest installer size is out of range."
    return (
        UpdateInfo(
            version=version,
            url=url,
            sha256=sha256.lower(),
            size=size,
            notes=str(payload.get("notes", ""))[:4000],
            published=str(payload.get("published", ""))[:40],
        ),
        "",
    )


def launch_installer(path: Path) -> bool:
    """Hand a verified installer to the OS. Returns False when it cannot start."""
    target = Path(path)
    if not target.is_file():
        return False
    if sys.platform == "win32":
        if target.suffix.lower() != ".exe":
            return False
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    try:
        subprocess.Popen([str(target)], close_fds=True)
        return True
    except OSError:
        return False
