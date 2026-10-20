"""Fetch and apply a vendor command catalog over HTTPS.

Design constraints:

* HTTPS only - a plain-HTTP feed could be modified in transit.
* One request, bounded in time and size, with ETag/If-Modified-Since caching.
* The response is fully validated before the previous catalog is replaced.
* A failed or corrupt update leaves the last good catalog untouched; being
  offline is a normal outcome, not an error the operator needs to fix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from nexus_remote import config
from nexus_remote.assistant.builtin_catalog import CATALOG as BUILTIN_CATALOG
from nexus_remote.assistant.catalog import (
    CatalogFormatError,
    count_new_entries,
    parse_catalog,
    read_catalog,
    write_catalog,
)

MAX_BYTES = 2_000_000
DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class CatalogUpdateResult:
    """Outcome of one update attempt (always returned, never raised)."""

    status: str  # updated | unchanged | not-configured | error
    version: str = ""
    added: int = 0
    total: int = 0
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"updated", "unchanged", "not-configured"}


class CatalogUpdater:
    """Downloads and stores a vendor catalog for the completion engine."""

    def __init__(
        self,
        path,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = MAX_BYTES,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._opener = opener

    def update(self, feed_url: str) -> CatalogUpdateResult:
        url = (feed_url or "").strip()
        if not url:
            return CatalogUpdateResult(
                status="not-configured",
                message="No vendor catalog feed is configured.",
            )
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            return CatalogUpdateResult(
                status="error",
                message="The catalog feed must be an https:// URL.",
            )

        headers = {
            "User-Agent": f"{config.APP_NAME}/{config.APP_VERSION} catalog-updater",
            "Accept": "application/json",
        }
        previous = read_catalog(self.path)
        etag = ""
        if previous is not None and previous.source == url and previous.etag:
            etag = previous.etag
            headers["If-None-Match"] = etag

        try:
            response = self._opener(Request(url, headers=headers), timeout=self.timeout)
            with response:
                status = getattr(response, "status", 200)
                if status == 304:
                    return CatalogUpdateResult(
                        status="unchanged",
                        version=previous.version if previous else "",
                        total=previous.total if previous else 0,
                        message="The catalog is already up to date.",
                    )
                try:
                    body = self._read_bounded(response)
                except ValueError as exc:
                    return CatalogUpdateResult(status="error", message=str(exc))
                response_etag = response.headers.get("ETag", "") if response.headers else ""
        except HTTPError as exc:
            if exc.code == 304:
                return CatalogUpdateResult(
                    status="unchanged",
                    version=previous.version if previous else "",
                    total=previous.total if previous else 0,
                    message="The catalog is already up to date.",
                )
            return CatalogUpdateResult(
                status="error",
                message=f"The catalog server answered HTTP {exc.code}.",
            )
        except (URLError, TimeoutError, OSError) as exc:
            return CatalogUpdateResult(
                status="error",
                message=f"Could not reach the catalog feed: {getattr(exc, 'reason', exc)}",
            )

        try:
            payload = json.loads(body.decode("utf-8"))
            catalog = parse_catalog(payload, source=url)
            write_catalog(self.path, catalog, source=url, etag=response_etag or etag)
        except (ValueError, UnicodeDecodeError, OSError, CatalogFormatError) as exc:
            return CatalogUpdateResult(
                status="error",
                message=f"The catalog feed was rejected: {exc}",
            )

        added = count_new_entries(BUILTIN_CATALOG, catalog.environments)
        return CatalogUpdateResult(
            status="updated",
            version=catalog.version,
            added=added,
            total=catalog.total,
            message=(
                f"Catalog {catalog.version} downloaded: {catalog.total} entries, "
                f"{added} new suggestion(s)."
            ),
        )

    def _read_bounded(self, response) -> bytes:
        """Read at most ``max_bytes`` and fail loudly when the feed is larger."""
        chunks: list[bytes] = []
        remaining = self.max_bytes + 1
        while remaining > 0:
            chunk = response.read(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        if len(body) > self.max_bytes:
            raise ValueError("the catalog feed is larger than the size limit")
        return body
