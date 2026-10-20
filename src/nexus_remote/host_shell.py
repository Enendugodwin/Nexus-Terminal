"""Local PowerShell console for the Windows host.

This is deliberately separate from the remote session layer: the shell runs on
the operator's own computer, with the operator's account. Remote policy rules
do not apply to it, and nothing from a remote device or an AI provider is ever
typed into it automatically.

A real console needs a pseudo-terminal, so the module uses ``pywinpty`` (WinPTY
with a ConPTY fallback). The Qt terminal widget only sees the small channel
interface it already uses for SSH: ``read`` / ``write`` / ``resize`` / ``close``
plus a ``closed`` flag.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class HostShellUnavailable(RuntimeError):
    """Raised when no local console backend or PowerShell executable exists."""


@dataclass(frozen=True)
class ShellOption:
    key: str
    label: str
    executable: str


def available_shells() -> list[ShellOption]:
    """Return the PowerShell editions installed on this computer."""

    options: list[ShellOption] = []
    if sys.platform == "win32":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        windows_powershell = (
            system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        )
        if windows_powershell.is_file():
            options.append(
                ShellOption("powershell", "Windows PowerShell 5.1", str(windows_powershell))
            )
    pwsh = shutil.which("pwsh")
    if not pwsh and sys.platform == "win32":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerShell" / "7" / "pwsh.exe"
        if candidate.is_file():
            pwsh = str(candidate)
    if pwsh:
        options.append(ShellOption("pwsh", "PowerShell 7+", pwsh))
    return options


def default_shell() -> ShellOption | None:
    options = available_shells()
    if not options:
        return None
    # Prefer PowerShell 7 when installed; fall back to Windows PowerShell.
    return next((option for option in options if option.key == "pwsh"), options[0])


class PtyChannel:
    """A non-blocking view of a local pseudo-terminal for :class:`TerminalView`."""

    def __init__(self, process: object, label: str = "") -> None:
        self._process = process
        self.label = label
        self._queue: queue.Queue[tuple[str, bytes | str]] = queue.Queue()
        self._closed = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, name="nexus-pty", daemon=True)
        self._reader.start()

    @classmethod
    def spawn(
        cls,
        executable: str,
        *,
        cwd: str | Path | None = None,
        rows: int = 24,
        columns: int = 80,
        label: str = "",
        arguments: tuple[str, ...] = (),
    ) -> "PtyChannel":
        if sys.platform != "win32":  # pragma: no cover - the packaged app is Windows
            raise HostShellUnavailable("the local PowerShell console is a Windows feature")
        try:
            from winpty import Backend, PtyProcess
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise HostShellUnavailable(
                "the local console requires pywinpty; reinstall Nexus Remote or run: pip install pywinpty"
            ) from exc
        if not executable:
            raise HostShellUnavailable("no PowerShell executable was selected")
        working_dir = Path(cwd).expanduser() if cwd else Path.home()
        if not working_dir.is_dir():
            working_dir = Path.home()
        command = subprocess.list2cmdline([str(executable), "-NoLogo", *arguments])
        size = (max(4, int(rows)), max(20, int(columns)))
        # WinPTY is used first because ConPTY through pywinpty did not
        # reliably interrupt Windows PowerShell in testing: ETX (Ctrl+C) was
        # ignored while a command was running. WinPTY delivers the normal
        # Ctrl+C key event, which stops the command and cancels the input
        # line. ConPTY remains the fallback where the WinPTY agent cannot
        # start.
        try:
            process = PtyProcess.spawn(
                command,
                cwd=str(working_dir),
                dimensions=size,
                backend=Backend.WinPTY,
            )
        except Exception:
            try:
                process = PtyProcess.spawn(
                    command,
                    cwd=str(working_dir),
                    dimensions=size,
                    backend=Backend.ConPTY,
                )
            except Exception as exc:
                raise HostShellUnavailable(f"could not start PowerShell: {exc}") from exc
        return cls(process, label=label)

    # -- channel interface --------------------------------------------------
    def read(self, timeout: float = 0.0, max_bytes: int = 65536) -> bytes:
        deadline = time.monotonic() + max(0.0, float(timeout))
        chunks: list[bytes] = []
        total = 0
        while total < max_bytes:
            wait = max(0.0, deadline - time.monotonic())
            try:
                if wait:
                    kind, payload = self._queue.get(timeout=wait)
                else:
                    kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "data":
                data = payload if isinstance(payload, bytes) else str(payload).encode("utf-8", "replace")
                chunks.append(data)
                total += len(data)
                # Keep draining immediately available output without waiting.
                deadline = time.monotonic()
                continue
            if kind == "eof":
                self._closed.set()
                break
            if kind == "error":
                self._closed.set()
                break
        return b"".join(chunks)[:max_bytes]

    def write(self, data: bytes | str) -> None:
        if self._closed.is_set():
            return
        text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
        if not text:
            return
        try:
            self._process.write(text)
        except Exception as exc:  # pragma: no cover - process race
            raise OSError(f"could not write to PowerShell: {exc}") from exc

    def resize(self, columns: int, rows: int) -> None:
        if self._closed.is_set():
            return
        try:
            self._process.setwinsize(max(4, int(rows)), max(20, int(columns)))
        except Exception:  # pragma: no cover - resize is best effort
            return

    @property
    def closed(self) -> bool:
        return self._closed.is_set() and self._queue.empty()

    def close(self) -> None:
        self._closed.set()
        process = self._process
        try:
            process.terminate(force=True)
        except Exception:  # pragma: no cover - already gone
            pass
        try:
            process.close(force=True)
        except Exception:  # pragma: no cover - already gone
            pass

    # -- background reader --------------------------------------------------
    def _read_loop(self) -> None:
        while not self._closed.is_set():
            try:
                data = self._process.read(4096)
            except EOFError:
                break
            except Exception as exc:  # pragma: no cover - backend specific
                self._queue.put(("error", str(exc)))
                break
            if not data:
                break
            self._queue.put(("data", data))
        self._queue.put(("eof", b""))
        self._closed.set()
