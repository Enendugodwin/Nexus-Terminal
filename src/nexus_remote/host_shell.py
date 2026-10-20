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

#: How long the WinPTY agent may take to start and answer an RPC, in
#: milliseconds. pywinpty's own default is 30 seconds, which made a failed
#: start look like a hang.
WINPTY_AGENT_TIMEOUT_MS = 5000

#: How long the whole spawn attempt may take before it is abandoned.
SPAWN_TIMEOUT_SECONDS = 15.0

#: How long a single executable lookup may take. An unreachable network path
#: (a disconnected mapped drive) blocks ``os.path.exists`` for a minute or
#: more, so the lookup runs with a deadline of its own.
LOOKUP_TIMEOUT_SECONDS = 2.0


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


def _bounded_lookup(probe, timeout: float):
    """Run ``probe`` with a deadline.

    Returns ``(value, error, timed_out)``. A timed-out probe leaves a daemon
    thread behind; it cannot be cancelled, but the caller stops waiting for it.
    """

    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["value"] = probe()
        except Exception as exc:  # pragma: no cover - defensive
            box["error"] = exc

    worker = threading.Thread(target=run, name="nexus-pty-lookup", daemon=True)
    worker.start()
    worker.join(max(0.1, float(timeout)))
    if worker.is_alive():
        return None, None, True
    return box.get("value"), box.get("error"), False


def _resolve_executable(executable: str, timeout: float = LOOKUP_TIMEOUT_SECONDS) -> str:
    """Return the full path of ``executable`` or fail quickly.

    The lookup is bounded because ``os.path.exists`` on a disconnected mapped
    drive can block for a minute; the operator should see the error message
    immediately, not after the network gives up.
    """

    def probe():
        try:
            candidate = Path(executable)
            if candidate.is_file():
                return str(candidate)
        except OSError:  # pragma: no cover - defensive
            pass
        return shutil.which(executable)

    value, error, timed_out = _bounded_lookup(probe, timeout)
    if timed_out:
        raise HostShellUnavailable(
            f"the path {executable!r} could not be reached within {float(timeout):.0f} seconds. "
            "It may be on a disconnected network drive."
        )
    if error is not None:
        raise HostShellUnavailable(f"could not find PowerShell: {error}")
    if not value:
        raise HostShellUnavailable(f"PowerShell was not found at {executable}.")
    return str(value)


def _spawn_process(argv: list[str], cwd: str, size: tuple[int, int]):
    """Start the console, preferring WinPTY and falling back to ConPTY.

    WinPTY is used first because ConPTY through pywinpty did not reliably
    interrupt Windows PowerShell in testing: ETX (Ctrl+C) was ignored while a
    command was running. WinPTY delivers the normal Ctrl+C key event, which
    stops the command and cancels the input line. ConPTY remains the fallback
    where the WinPTY agent cannot start.

    The WinPTY agent gets a short timeout of its own, so a broken agent
    reports an error in seconds instead of half a minute.
    """

    from winpty import Backend, PTY, PtyProcess

    environment = dict(os.environ)
    env_block = "\0".join(f"{key}={value}" for key, value in environment.items()) + "\0"
    command = argv[0]
    cmdline = (" " + subprocess.list2cmdline(argv[1:])) if len(argv) > 1 else ""
    errors: list[str] = []

    for backend in (Backend.WinPTY, Backend.ConPTY):
        name = "WinPTY" if backend == Backend.WinPTY else "ConPTY"
        try:
            if backend == Backend.WinPTY:
                pty = PTY(size[1], size[0], backend=backend, timeout=WINPTY_AGENT_TIMEOUT_MS)
            else:
                pty = PTY(size[1], size[0], backend=backend)
            if cmdline:
                pty.spawn(command, cwd=cwd, env=env_block, cmdline=cmdline)
            else:
                pty.spawn(command, cwd=cwd, env=env_block)
            process = PtyProcess(pty)
            process._winsize = size  # mirrors PtyProcess.spawn
            process.argv = list(argv)
            process.env = environment
            process.launch_dir = cwd
            return process
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("; ".join(errors))


def _close_late_process(outcome: dict[str, object], worker: threading.Thread) -> None:
    """Close a console that appeared after the caller gave up waiting."""

    worker.join(timeout=30)
    process = outcome.get("process")
    if process is None:
        return
    try:  # pragma: no cover - only runs after a timeout
        process.terminate(force=True)
    except Exception:
        pass
    try:  # pragma: no cover - only runs after a timeout
        process.close(force=True)
    except Exception:
        pass


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
        timeout: float = SPAWN_TIMEOUT_SECONDS,
    ) -> "PtyChannel":
        if sys.platform != "win32":  # pragma: no cover - the packaged app is Windows
            raise HostShellUnavailable("the local PowerShell console is a Windows feature")
        try:
            from winpty import Backend  # noqa: F401  (import guard only)
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise HostShellUnavailable(
                "the local console requires pywinpty; reinstall Nexus Remote or run: pip install pywinpty"
            ) from exc
        if not executable:
            raise HostShellUnavailable("no PowerShell executable was selected")
        working_dir = Path(cwd).expanduser() if cwd else Path.home()
        if not working_dir.is_dir():
            working_dir = Path.home()
        # Resolve the executable first, with a deadline of its own: looking up
        # a path on an unreachable drive blocks for minutes, and the failure
        # message must arrive at once.
        resolved = _resolve_executable(executable)
        size = (max(4, int(rows)), max(20, int(columns)))
        argv = [resolved, "-NoLogo", *arguments]

        outcome: dict[str, object] = {}

        def attempt() -> None:
            try:
                outcome["process"] = _spawn_process(argv, str(working_dir), size)
            except Exception as exc:  # reported to the caller below
                outcome["error"] = exc

        worker = threading.Thread(target=attempt, name="nexus-pty-spawn", daemon=True)
        worker.start()
        worker.join(max(0.5, float(timeout)))
        if worker.is_alive():
            # The backend is stuck (an unreachable path, a wedged agent). Stop
            # waiting, report the failure and clean up if the process appears
            # later.
            threading.Thread(
                target=_close_late_process, args=(outcome, worker), name="nexus-pty-cleanup", daemon=True
            ).start()
            raise HostShellUnavailable(
                f"starting PowerShell took longer than {float(timeout):.0f} seconds and was stopped. "
                "Check the working directory and try again."
            )
        error = outcome.get("error")
        if error is not None:
            raise HostShellUnavailable(f"could not start PowerShell: {error}") from error
        return cls(outcome["process"], label=label)

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
