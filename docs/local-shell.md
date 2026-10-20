# Local PowerShell console

The **PowerShell** page is a real interactive console for **this Windows
computer**. It is not a remote session, it does not appear in device inventory,
and it never starts on its own.

## Starting and stopping

1. Open **PowerShell** in the sidebar (or **Workspace menu > Local PowerShell**).
2. Choose the edition: *Windows PowerShell 5.1* or *PowerShell 7+* (whichever is
   installed) and a working directory.
3. Press **Start PowerShell**. The shell loads your normal PowerShell profile
   (`-NoLogo`, no `-NoProfile`).
4. Press **Stop** to terminate it, or close the application - the main window
   stops the child process on exit.

The selected edition and directory are remembered. Nothing is spawned when the
application launches or when the page is first opened.

## What it is

`host_shell.PtyChannel` wraps a Windows pseudo-terminal through `pywinpty` and
exposes the same small channel surface the SSH and serial terminals already
use: `read`, `write`, `resize`, `close` and `closed`. `TerminalView` therefore
paints the local shell exactly like a remote one: colours, cursor keys,
full-screen programs, resizing, selection and copy all work.

Nexus Remote uses pywinpty's **WinPTY** backend first and falls back to
**ConPTY** only if the WinPTY agent cannot start. The reason is Ctrl+C: in
testing, ConPTY through pywinpty ignored `ETX` while a Windows PowerShell
command was running, so Ctrl+C did not interrupt it. WinPTY delivers the normal
Ctrl+C key event, so `Ctrl+C` cancels the current line or interrupts a running
command as expected.

A background reader thread performs the blocking PTY read and queues bytes; the
Qt timer only drains the queue, so the UI never blocks on a silent shell.
Output is capped per tick and scrollback is bounded, as for remote sessions.

## Policy and safety

The remote command policy applies to commands sent to **remote** devices. A
local shell runs with your own Windows account, on your own machine, and is not
filtered by that policy. The page says this explicitly. Anyone who can use your
account can already open PowerShell, so Nexus Remote does not add a privilege
boundary here - it removes a convenience barrier, not a security one.

- The shell is operator-driven. Remote output, AI output and clipboard content
  are never typed into it automatically.
- **Insert into shell** from the assistant pastes one line without pressing
  Enter. You still review it and press Enter yourself.
- Starting and stopping the shell is audited (`local_shell.started`,
  `local_shell.stopped`) with the edition and working directory.
- Closing the window terminates the process; there is no hidden background
  shell.

## Requirements and troubleshooting

- Windows 10/11; the packaged app installs `pywinpty` with its WinPTY agent and
  ConPTY binaries. WinPTY is preferred so Ctrl+C reaches PowerShell correctly.
- *Windows PowerShell 5.1* ships with Windows. *PowerShell 7+* is detected via
  `pwsh` on `PATH` or `%ProgramFiles%\PowerShell\7\pwsh.exe`.
- If no edition is found, the page disables **Start** and says so.
- If a working directory no longer exists, the shell starts in your home
  directory after an explanatory message.
- A closed child shell shows *PowerShell exited.* and the Start button returns.
