"""RDP adapter.

The first release launches the native Windows Remote Desktop client
(``mstsc``) with a generated ``.rdp`` profile, which keeps the remote-access
inventory in Nexus Remote while leaving the protocol to Microsoft's client.

Credentials are never written into the ``.rdp`` file: the profile pre-fills the
username and Windows prompts for the secret. Embedded/FreeRDP rendering and
DPAPI-encrypted credential injection are later-phase work.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from nexus_remote.connections.interfaces import (
    ConnectionErrorKind,
    ConnectionTarget,
    ConnectResult,
    ProtocolAdapter,
    ProtocolError,
)


def rdp_client_path() -> str | None:
    """Locate ``mstsc.exe`` (None on non-Windows hosts)."""
    if os.name != "nt":
        return None
    candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "mstsc.exe"
    if candidate.exists():
        return str(candidate)
    return shutil.which("mstsc")


@dataclass
class RDPOptions:
    width: int = 1920
    height: int = 1080
    fullscreen: bool = False
    color_depth: int = 32
    clipboard: bool = True
    redirect_drives: bool = False
    redirect_printers: bool = False
    nla: bool = True
    gateway_hostname: str | None = None


class RDPAdapter(ProtocolAdapter):
    protocol = "rdp"

    def __init__(self, launcher: object | None = None) -> None:
        super().__init__()
        self._process: subprocess.Popen[bytes] | None = None
        self._launcher = launcher or subprocess.Popen
        self.profile_path: Path | None = None

    def connect(self, target: ConnectionTarget) -> ConnectResult:
        client = rdp_client_path()
        if client is None:
            raise ProtocolError(
                "the Windows Remote Desktop client (mstsc) was not found",
                kind=ConnectionErrorKind.POLICY,
                detail="RDP requires Windows 10/11 with the Remote Desktop client installed.",
            )
        options = RDPOptions(**{**asdict(RDPOptions()), **_known_options(target.options)})
        if target.options.get("gateway"):
            options.gateway_hostname = str(target.options["gateway"])
        profile = self.build_rdp_file(target, options)
        self.profile_path = profile
        try:
            self._process = self._launcher([client, str(profile)])  # type: ignore[operator]
        except OSError as exc:
            raise ProtocolError(
                f"could not start the RDP client: {exc}",
                kind=ConnectionErrorKind.PROTOCOL,
                detail=str(exc),
            ) from exc
        self._connected = True
        return ConnectResult(
            ok=True,
            message=f"launched the Windows RDP client for {target.endpoint()}",
            auth_method="windows-credential-prompt",
            metadata={"rdp_profile": str(profile), "username": target.username},
        )

    def build_rdp_file(self, target: ConnectionTarget, options: RDPOptions | None = None) -> Path:
        """Write an ``.rdp`` profile without any secret material."""
        settings = options or RDPOptions()
        port = int(target.port or 3389)
        address = target.host if port == 3389 else f"{target.host}:{port}"
        lines = [
            f"full address:s:{address}",
            f"username:s:{target.username}" if target.username else "",
            "prompt for credentials:i:1",
            f"screen mode id:i:{2 if settings.fullscreen else 1}",
            f"desktopwidth:i:{settings.width}",
            f"desktopheight:i:{settings.height}",
            f"session bpp:i:{settings.color_depth}",
            f"redirectclipboard:i:{1 if settings.clipboard else 0}",
            f"redirectprinters:i:{1 if settings.redirect_printers else 0}",
            f"drivestoredirect:s:{'*' if settings.redirect_drives else ''}",
            f"enablecredsspsupport:i:{1 if settings.nla else 0}",
            "authentication level:i:2",
            "audiomode:i:0",
            "audiocapturemode:i:0",
            "camerastoredirect:s:",
        ]
        if settings.gateway_hostname:
            lines.extend(
                [
                    f"gatewayhostname:s:{settings.gateway_hostname}",
                    "gatewayusagemethod:i:2",
                    "gatewayprofileusagemethod:i:1",
                ]
            )
        directory = Path(tempfile.gettempdir()) / "nexus-remote" / "rdp"
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(char if char.isalnum() or char in "-_." else "_" for char in target.host)
        profile = directory / f"{safe_name}-{port}.rdp"
        profile.write_text("\r\n".join(line for line in lines if line) + "\r\n", encoding="utf-8")
        return profile

    def close(self) -> None:
        # mstsc owns the session; we only control the process we started.
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.terminate()
            except Exception:  # pragma: no cover - defensive
                pass
        self._process = None
        self._connected = False


def _known_options(options: dict[str, object]) -> dict[str, object]:
    allowed = set(RDPOptions.__annotations__)
    return {key: value for key, value in options.items() if key in allowed}
