"""RDP adapter.

RDP is performed by the application itself: it launches the FreeRDP client
(``wlfreerdp``/``xfreerdp``) and embeds its window inside the Nexus Remote
workspace by passing the Qt widget's native window handle as
``/parent-window``. Microsoft's ``mstsc.exe`` is not used.

Security notes
--------------
* The password is never placed on the command line (where any local process
  could read it): it is written to the client's stdin with ``/from-stdin``.
* Certificate verification is left on by default. ``cert_ignore`` is an
  explicit, per-session opt-in for lab hosts with self-signed certificates.
* The child process is tracked and terminated on ``close()`` so a wedged client
  cannot keep a session alive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from nexus_remote.connections.interfaces import (
    ConnectionErrorKind,
    ConnectionTarget,
    ConnectResult,
    ProtocolAdapter,
    ProtocolError,
)

#: Client binaries, most preferred first. On Windows the FreeRDP GUI client is
#: ``wlfreerdp.exe``; ``xfreerdp``/``sdl-freerdp`` are used elsewhere.
_CLIENT_NAMES = (
    "wlfreerdp.exe",
    "wlfreerdp",
    "sdl-freerdp.exe",
    "sdl-freerdp",
    "xfreerdp.exe",
    "xfreerdp",
)

#: Directories that commonly hold a FreeRDP install on Windows.
_WINDOWS_CLIENT_DIRS = (
    Path(r"C:\Program Files\FreeRDP"),
    Path(r"C:\Program Files (x86)\FreeRDP"),
    Path(r"C:\ProgramData\chocolatey\bin"),
    Path(r"C:\ProgramData\chocolatey\lib\freerdp\tools"),
)

_INSTALL_HINT = (
    "FreeRDP was not found. Nexus Remote performs RDP itself with FreeRDP "
    "(the mstsc client is not used). Install it with 'winget install FreeRDP.FreeRDP' "
    "or 'choco install freerdp', or place wlfreerdp.exe on PATH."
)


def freerdp_candidates() -> list[str]:
    """Return every FreeRDP client binary that can be found."""
    found: list[str] = []
    for name in _CLIENT_NAMES:
        which = shutil.which(name)
        if which and which not in found:
            found.append(which)
    if os.name == "nt":
        for directory in _WINDOWS_CLIENT_DIRS:
            if not directory.exists():
                continue
            for name in _CLIENT_NAMES:
                candidate = directory / name
                if candidate.exists() and str(candidate) not in found:
                    found.append(str(candidate))
    return found


def freerdp_client_path() -> str | None:
    """Locate a usable FreeRDP client, or ``None`` when it is not installed."""
    candidates = freerdp_candidates()
    return candidates[0] if candidates else None


def freerdp_available() -> bool:
    return freerdp_client_path() is not None


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
    cert_ignore: bool = False
    gateway_hostname: str | None = None
    parent_window: int | None = None
    client: str | None = None


class RDPAdapter(ProtocolAdapter):
    protocol = "rdp"

    def __init__(self, launcher: object | None = None, client_path: str | None = None) -> None:
        super().__init__()
        self._process: subprocess.Popen[bytes] | None = None
        self._launcher = launcher or subprocess.Popen
        self._client_path = client_path

    # -- lifecycle ----------------------------------------------------------
    def connect(self, target: ConnectionTarget) -> ConnectResult:
        client = self._client_path or freerdp_client_path()
        if client is None:
            raise ProtocolError(
                "FreeRDP is required to open an RDP session",
                kind=ConnectionErrorKind.POLICY,
                detail=_INSTALL_HINT,
            )
        options = RDPOptions(
            **{**asdict(RDPOptions()), **_known_options(target.options), "client": client}
        )
        if target.options.get("gateway"):
            options.gateway_hostname = str(target.options["gateway"])
        command = self.build_command(target, options)
        try:
            self._process = self._launcher(  # type: ignore[operator]
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ProtocolError(
                f"could not start the FreeRDP client: {exc}",
                kind=ConnectionErrorKind.PROTOCOL,
                detail=str(exc),
            ) from exc
        # The password reaches the client on stdin, never through argv.
        if target.secret and self._process is not None and self._process.stdin is not None:
            try:
                self._process.stdin.write((target.secret + "\n").encode("utf-8"))
                self._process.stdin.flush()
                self._process.stdin.close()
            except (OSError, ValueError):  # pragma: no cover - client already gone
                pass
        self._connected = True
        embedded = options.parent_window is not None
        return ConnectResult(
            ok=True,
            message=(
                f"FreeRDP session started for {target.endpoint()}"
                + (" (embedded)" if embedded else "")
            ),
            auth_method="vault-credential" if target.secret else "interactive-prompt",
            metadata={
                "client": client,
                "embedded": embedded,
                "parent_window": options.parent_window,
            },
        )

    def close(self) -> None:
        """Stop the client; escalate to kill if it does not exit promptly."""
        process = self._process
        self._process = None
        self._connected = False
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            process.wait(timeout=3)
        except Exception:  # pragma: no cover - client ignored terminate
            try:
                process.kill()
            except Exception:
                pass

    # -- command construction ----------------------------------------------
    def build_command(self, target: ConnectionTarget, options: RDPOptions | None = None) -> list[str]:
        """Build the FreeRDP argv. The password is deliberately absent."""
        settings = options or RDPOptions()
        client = settings.client or self._client_path or freerdp_client_path() or "wlfreerdp"
        port = int(target.port or 3389)
        address = target.host if port == 3389 else f"{target.host}:{port}"
        command = [client, f"/v:{address}"]
        if target.username:
            command.append(f"/u:{target.username}")
        if target.secret:
            # Read the password from stdin so it never appears in the process list.
            command.append("/from-stdin")
        command.append(f"/w:{settings.width}")
        command.append(f"/h:{settings.height}")
        command.append(f"/bpp:{settings.color_depth}")
        if settings.fullscreen:
            command.append("/f")
        if settings.clipboard:
            command.append("+clipboard")
        if settings.redirect_drives:
            command.append("/drive:home,$HOME")
        if settings.redirect_printers:
            command.append("/printer")
        if settings.nla:
            command.append("/sec:nla")
        if settings.gateway_hostname:
            command.append(f"/gateway:{settings.gateway_hostname}")
        if settings.cert_ignore:
            command.append("/cert:ignore")
        if settings.parent_window is not None:
            command.append(f"/parent-window:{int(settings.parent_window)}")
        command.append("/dynamic-resolution")
        return command


def _known_options(options: dict[str, object]) -> dict[str, object]:
    allowed = set(RDPOptions.__annotations__)
    return {key: value for key, value in options.items() if key in allowed}
