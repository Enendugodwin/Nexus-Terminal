"""RDP adapter.

RDP is performed by the application itself: it launches the FreeRDP client and
re-parents the client's window into the Nexus Remote workspace at the Win32
level (the SDL client ignores ``/parent-window``). Microsoft's ``mstsc.exe`` is
not used.

Security notes
--------------
* The password is never placed on the command line (where any local process
  could read it): it is written to the client's stdin with ``/from-stdin``.
* Certificates use trust-on-first-use by default (``/cert:tofu``); a changed
  certificate is reported. ``cert_ignore`` is an explicit per-session opt-out.
* The child process is tracked and terminated on ``close()`` so a wedged client
  cannot keep a session alive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections import deque
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


def managed_client_dir() -> Path:
    """Per-user directory where Nexus Remote keeps its own FreeRDP client.

    Installing here needs no administrator rights and keeps the client
    discoverable even when it is not on ``PATH``.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "NexusRemote" / "tools" / "freerdp"


def search_dirs() -> list[Path]:
    """Every directory scanned for a FreeRDP client, managed dir first."""
    directories = [managed_client_dir()]
    if os.name == "nt":
        directories.extend(_WINDOWS_CLIENT_DIRS)
    return [directory for directory in directories if directory.exists()]


# -- Win32 embedding -------------------------------------------------------
_GWL_STYLE = -16
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_WS_VISIBLE = 0x10000000
_WS_CAPTION = 0x00C00000
_WS_THICKFRAME = 0x00040000
_WS_SYSMENU = 0x00080000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_SWP_SHOWWINDOW = 0x0040
_SW_SHOW = 5


def _client_window(pid: int) -> int:
    """Return the largest visible top-level window owned by ``pid``."""
    if os.name != "nt" or pid <= 0:
        return 0
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    best: tuple[int, int] | None = None

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def visit(hwnd, _param):
        nonlocal best
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, 256)
        klass = name.value.lower()
        if "sdl" not in klass and "freerdp" not in klass:
            return True
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area < 20_000:  # ignore helper/tooltip windows
            return True
        if best is None or area > best[1]:
            best = (int(hwnd), area)
        return True

    user32.EnumWindows(visit, 0)
    return best[0] if best else 0


def _fit_embedded_window(child: int, parent: int) -> None:
    """Size a reparented client window to its host's client area."""
    if os.name != "nt" or not child or not parent:
        return
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    rect = wt.RECT()
    if user32.GetClientRect(parent, ctypes.byref(rect)):
        user32.SetWindowPos(
            child,
            0,
            0,
            0,
            rect.right,
            rect.bottom,
            _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED | _SWP_SHOWWINDOW,
        )


def _apply_embedding(child: int, parent: int) -> None:
    """Turn the client window into an undecorated child of ``parent``."""
    import ctypes

    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(child, _GWL_STYLE) & 0xFFFFFFFF
    style &= ~(_WS_POPUP | _WS_CAPTION | _WS_THICKFRAME | _WS_SYSMENU | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX)
    style |= _WS_CHILD | _WS_VISIBLE
    user32.SetWindowLongW(child, _GWL_STYLE, style)
    user32.SetParent(child, parent)
    _fit_embedded_window(child, parent)
    user32.ShowWindow(child, _SW_SHOW)


def embed_client_window(pid: int, parent: int, timeout: float = 20.0, settle: float = 2.0) -> int:
    """Wait for the FreeRDP window and make it a child of ``parent``.

    The SDL client positions its own window and may do so again just after it
    appears, so the style/parent/geometry are re-applied for a short settling
    period. Returns the embedded HWND, or 0 when no window appeared in time.
    Runs on a background thread so the caller (the GUI) never blocks.
    """
    if os.name != "nt" or not parent:
        return 0
    deadline = time.monotonic() + timeout
    child = 0
    while time.monotonic() < deadline and not child:
        child = _client_window(pid)
        if not child:
            time.sleep(0.1)
    if not child:
        return 0

    settle_until = time.monotonic() + max(0.0, settle)
    while True:
        replacement = _client_window(pid)
        if replacement:
            child = replacement
        _apply_embedding(child, parent)
        if time.monotonic() >= settle_until:
            break
        time.sleep(0.2)
    return child


_INSTALL_HINT = (
    "FreeRDP was not found. Nexus Remote performs RDP itself with FreeRDP "
    "(the mstsc client is not used). Install it with 'winget install FreeRDP.FreeRDP' "
    "or 'choco install freerdp', run 'packaging/install_freerdp.ps1' to fetch the "
    "official Windows nightly into the per-user tools directory, or place "
    "wlfreerdp.exe/sdl-freerdp.exe on PATH."
)


def freerdp_candidates() -> list[str]:
    """Return every FreeRDP client binary that can be found."""
    found: list[str] = []
    for name in _CLIENT_NAMES:
        which = shutil.which(name)
        if which and which not in found:
            found.append(which)
    for directory in search_dirs():
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


_ERROR_MESSAGES = {
    "ERRCONNECT_AUTHENTICATION_FAILED": "authentication failed - check the username and password",
    "ERRCONNECT_HYBRID_REQUIRED_BY_SERVER": "the server requires Network Level Authentication (NLA)",
    "ERRCONNECT_SECURITY_NEGOTIATION_FAILED": "security negotiation with the server failed",
    "ERRCONNECT_PROTOCOL_NEGOTIATION_FAILED": "the server rejected the RDP protocol negotiation",
    "ERRCONNECT_CONNECT_TRANSPORT_FAILED": "the server closed the connection during setup",
    "ERRCONNECT_DNS_ERROR": "the host name could not be resolved",
    "ERRCONNECT_TLS_CONNECT_FAILED": "the TLS handshake with the server failed",
    "ERRCONNECT_CONNECT_FAILED": "the TCP connection to the server failed",
}


def _fatal_error(line: str) -> str:
    """Translate a FreeRDP error line into a user-facing message (or '')."""
    for code, message in _ERROR_MESSAGES.items():
        if code in line:
            return message
    if "Protocol Security Negotiation Failure" in line:
        return "security negotiation with the server failed"
    return ""


class RDPAdapter(ProtocolAdapter):
    protocol = "rdp"

    def __init__(self, launcher: object | None = None, client_path: str | None = None) -> None:
        super().__init__()
        self._process: subprocess.Popen[bytes] | None = None
        self._launcher = launcher or subprocess.Popen
        self._client_path = client_path
        self._embed_parent: int = 0
        self._embedded_hwnd: int = 0
        self._embed_thread: threading.Thread | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self.embed_error: str = ""
        #: Set when FreeRDP reports a fatal connection error. The client keeps
        #: its window open (and waits for a dialog) in that case, so the UI uses
        #: this to close the session and explain what happened.
        self.last_error: str = ""

    @property
    def embedded_window(self) -> int:
        return self._embedded_hwnd

    @property
    def diagnostics(self) -> str:
        return "\n".join(self._stderr_tail)

    @property
    def exited(self) -> bool:
        """True when the client process is gone (so the session is dead)."""
        return self._process is not None and self._process.poll() is not None


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
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise ProtocolError(
                f"could not start the FreeRDP client: {exc}",
                kind=ConnectionErrorKind.PROTOCOL,
                detail=str(exc),
            ) from exc
        self._start_reader()
        # The password reaches the client on stdin, never through argv.
        if target.secret and self._process is not None and self._process.stdin is not None:
            try:
                self._process.stdin.write((target.secret + "\n").encode("utf-8"))
                self._process.stdin.flush()
                self._process.stdin.close()
            except (OSError, ValueError):  # pragma: no cover - client already gone
                pass
        self._connected = True
        embedded = bool(options.parent_window)
        if embedded:
            self._embed_parent = int(options.parent_window or 0)
            self._start_embedding()
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

    def _start_reader(self) -> None:
        """Drain the client's stderr so fatal errors can be surfaced."""
        process = self._process
        stream = getattr(process, "stderr", None)
        if stream is None:
            return

        def work() -> None:
            try:
                for raw in iter(stream.readline, b""):
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    self._stderr_tail.append(line)
                    message = _fatal_error(line)
                    if message and not self.last_error:
                        self.last_error = message
                        # FreeRDP leaves the window up waiting for a dialog on
                        # fatal errors; stop it so the UI can report the reason.
                        try:
                            if process is not None and process.poll() is None:
                                process.terminate()
                        except Exception:  # pragma: no cover - defensive
                            pass
            except (OSError, ValueError):  # pragma: no cover - stream closed
                pass

        self._reader_thread = threading.Thread(target=work, name="rdp-stderr", daemon=True)
        self._reader_thread.start()

    def _start_embedding(self) -> None:
        process = self._process
        pid = getattr(process, "pid", 0) or 0
        if pid <= 0 or os.name != "nt":
            return

        def work() -> None:
            hwnd = embed_client_window(pid, self._embed_parent)
            if hwnd:
                self._embedded_hwnd = hwnd
            else:
                self.embed_error = "the FreeRDP window did not appear"

        self._embed_thread = threading.Thread(target=work, name="rdp-embed", daemon=True)
        self._embed_thread.start()

    def resize_embedded(self) -> None:
        """Refit the client window after the host widget was resized."""
        if self._embedded_hwnd and self._embed_parent:
            _fit_embedded_window(self._embedded_hwnd, self._embed_parent)

    def close(self) -> None:
        """Stop the client; escalate to kill if it does not exit promptly."""
        process = self._process
        self._process = None
        self._connected = False
        self._embedded_hwnd = 0
        self._embed_parent = 0
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
            command.append(f"/drive:home,{Path.home()}")
        if settings.redirect_printers:
            command.append("/printer")
        if settings.nla:
            command.append("/sec:nla")
        if settings.gateway_hostname:
            command.append(f"/gateway:{settings.gateway_hostname}")
        if settings.cert_ignore:
            command.append("/cert:ignore")
        else:
            # Trust on first use: no silent MITM, but no prompt for a self-signed
            # certificate either. A changed certificate is reported by FreeRDP.
            command.append("/cert:tofu")
        command.append("/dynamic-resolution")
        return command


def _known_options(options: dict[str, object]) -> dict[str, object]:
    allowed = set(RDPOptions.__annotations__)
    return {key: value for key, value in options.items() if key in allowed}
