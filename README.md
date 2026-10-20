# Nexus Remote

A Windows desktop workspace for remote access and device management: SSH/SFTP
sessions, serial console connections, a real interactive terminal, an embedded
FreeRDP desktop, device inventory, diagnostics and a deterministic assistant.
Everything that matters works offline; no AI model is required to connect,
diagnose or run a playbook.

> **Status: MVP 0.3.0.** SSH (password, SSH agent or private key), SFTP,
> serial consoles, interactive terminal emulation, a local PowerShell console,
> SMB browsing, optional hosted AI providers, four themes, a Windows installer
> and a signed-manifest update channel are implemented. RDP
> is performed by the application itself through the FreeRDP client, embedded in
> the workspace window (`mstsc.exe` is not used). SMB needs the optional
> `smbprotocol` extra and is SMB 2/3 only. Cloud discovery, NAT traversal and
> remote agents are deliberately deferred.

## Highlights

- **Device inventory** - environments, tags, criticality, per-protocol
  endpoints, groups, favourites, YAML import/export (secrets never exported).
- **Sessions** - multiple concurrent SSH/SFTP/serial sessions, state machine,
  route selection, host-key pinning, audit trail.
- **SSH private keys and serial consoles** - SSH and SFTP authenticate with a
  password from the credential vault, the SSH agent, a local key file or a key
  pasted into the vault (with an optional passphrase, also vault-only). Serial
  profiles open a raw COM/tty console with configurable baud, data bits, parity,
  stop bits and flow control (detected ports are listed), for switches, routers
  and embedded devices.
- **Interactive terminal** - VT100/xterm emulation via `pyte`: cursor movement,
  colours, full-screen programs, scrollback, PTY resize, copy/paste, bracketed
  paste and application cursor keys.
- **Local PowerShell section** - a real ConPTY console for **this computer**,
  with Windows PowerShell 5.1 / PowerShell 7 detection, a chosen working
  directory, full colours and interactive programs. It never starts at launch
  and is stopped when the app closes. Local commands run with your Windows
  account and are not filtered by the remote command policy; the page says so.
- **System information strip** - under the terminal, five cards read from the
  connected host: CPU cores and model, memory (used/total plus swap), storage
  (used/total for `/`), OS/kernel/architecture/hostname and uptime with load and
  logged-in users. Collected on attach and on demand with one fixed read-only
  probe; nothing is executed automatically and unsupported hosts degrade to a
  clear "unavailable" note.
- **SFTP and SMB file manager** - two-pane browser for SSH/SFTP sessions and
  SMB 2/3 shares, streamed 512 KB transfers, resume and cancel.
- **Saved commands** - the Commands section keeps a searchable library per
  environment with names, descriptions, tags, a "verified by me" flag and the
  policy's risk classification. Send a command to the SSH console (it is never
  executed from the library) or save the console line straight from the console
  tab; the policy gate runs when you press Run.
- **Command catalog with vendor updates** - ~390 curated commands for bash,
  PowerShell, cmd, Cisco IOS and PAN-OS are available offline. A vendor JSON
  feed can add more over HTTPS: validated first, merged without overriding
  built-ins, labelled *Catalog (downloaded)* and checked quietly at launch or on
  demand (see [docs/catalog-feed.md](docs/catalog-feed.md)).
- **Diagnostics** - DNS, TCP handshake, protocol banner, local route lookup,
  interface/VPN detection and ICMP (with honest caveats about what each result
  proves).
- **Four bundled themes** - Matrix (green), Midnight (blue), Ember (amber) and
  Daylight (light). Switch live in **Settings > Appearance**; the choice is
  stored in the local database and applied before the first window is shown.
  Terminal ANSI colours have a dedicated readable palette per theme.
- **Installable desktop app** - a real Windows setup executable with a Start
  Menu entry, optional desktop shortcut, uninstaller and per-user install under
  `%LOCALAPPDATA%\Programs\Nexus Remote` (no administrator rights). The app
  carries its own multi-resolution icon and version metadata.
- **Verified application updates** - opt in by setting an HTTPS update-manifest
  URL in **Settings > Updates**. Checks are bounded, the installer is downloaded
  only after consent and must match the SHA-256 in the manifest; nothing is ever
  installed silently (see [docs/updates.md](docs/updates.md)).
- **Deterministic assistant** - offline autocomplete with provenance labels,
  history, snippets, playbooks and rule-based error diagnosis. The best match is
  previewed inline (ghost text) while you type: Tab or → completes it, ↑ ↓ browse
  the full list, Enter completes then runs. Next to the prefix field, **Send**
  types it into the open interactive shell without pressing Enter, and **Copy**
  puts it on the clipboard. Optional model adapters are clearly labelled
  *unverified* and can never execute anything.
- **Optional external AI** - choose Ollama (local), OpenAI-compatible endpoints
  (OpenChat, OpenRouter, LM Studio, vLLM), OpenAI, Anthropic Claude or Google
  Gemini in **Settings > Optional AI**. API keys are stored in Windows
  Credential Manager; a key is never sent over plain HTTP except to loopback.
  The assistant sends only the question and context you type, labels every
  answer *unverified*, and inserts a suggested line into the shell only when you
  press the button - you still press Enter. See
  [docs/ai-providers.md](docs/ai-providers.md).
- **Security-first defaults** - credentials stay in Windows Credential Manager;
  SQLite and logs hold references only. Unknown SSH host keys are refused until
  the operator trusts the fingerprint. Dangerous commands are blocked by a
  deterministic policy engine.

## Requirements

- Windows 10/11 (the UI also runs on Linux/macOS for development).
- Python 3.11+ (developed on 3.13).
- Optional external tools: the FreeRDP client (`wlfreerdp.exe`/`sdl-freerdp.exe`)
  for RDP, embedded in the workspace window. `packaging/install_freerdp.ps1`
  fetches the official Windows nightly into the per-user tools directory.
- Optional SMB support (`pip install "nexus-remote[smb]"`, which installs
  `smbprotocol`). Without it the SMB rows still appear, and connecting explains
  exactly what to install. SMB1 is never used.
- Serial consoles need `pyserial` (installed with the package). If it is
  missing, connecting to a serial profile explains how to install it; no other
  feature is affected.
- The local PowerShell console needs `pywinpty` (installed with the package on
  Windows) and Windows 10 1809+ for ConPTY. Without it, only the PowerShell page
  is unavailable; remote features are unaffected.

## Install and run

### Packaged Windows app

Run `NexusRemoteSetup-0.3.0.exe` from `dist\` (or the copy published by your
team). It installs per user, needs no administrator rights and leaves existing
data in place during upgrades. Start **Nexus Remote** from the Start Menu.

Silent deployment for IT:

```powershell
.\NexusRemoteSetup-0.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

Uninstall through **Settings > Apps > Installed apps**; user data in
`%LOCALAPPDATA%\NexusRemote` is intentionally kept.

### From source

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m nexus_remote.main
```

Or from a checkout without installing:

```powershell
.\.venv\Scripts\python.exe app.py
```

Verify the environment without opening a window:

```powershell
.\.venv\Scripts\python.exe -m nexus_remote.main --selftest
```

Data (database, logs, exports, known_hosts) lives in
`%LOCALAPPDATA%\NexusRemote` by default. Override it with `--data-dir` or the
`NEXUS_REMOTE_HOME` environment variable.

## Tests

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest
```

The suite covers storage/migrations, device models and repository, credential
references, host keys, redaction, policy, routing, sessions, transfers,
diagnostics, autocomplete/history/snippets, error rules, the terminal buffer,
the terminal widget (offscreen Qt), the local PowerShell ConPTY channel, AI
provider request shaping and settings, theme switching and the update feed.

## Project layout

```
src/nexus_remote/
  config.py            paths and constants
  services.py          single wiring point for the whole app
  main.py              CLI and GUI bootstrap
  host_shell.py        local PowerShell console over Windows ConPTY
  updates.py           update manifest check, verified download, installer hand-off
  resources.py         bundled icon lookup and Windows app identity
  resources/           multi-resolution icon and PNG mark
  storage/             SQLite connection, migrations, FTS5 fallback
  devices/             models, repository, YAML profiles
  security/            redaction, vault, audit, known_hosts, policy
  connections/         session state machine, routing, manager
  protocols/           SSH/SFTP, serial console, embedded FreeRDP, optional SMB
  diagnostics/         DNS, TCP, routing, interfaces, reports
  assistant/           autocomplete, history, snippets, errors, playbooks
  ui/                  PySide6 pages, theme, terminal widget, workers
```

## Documentation

- [Architecture](docs/architecture.md)
- [Command catalog feed](docs/catalog-feed.md)
- [Optional AI providers](docs/ai-providers.md)
- [Local PowerShell console](docs/local-shell.md)
- [Update feed and release process](docs/updates.md)
- [Threat model](docs/threat-model.md)
- [Security guide](docs/security.md)
- [Deployment and packaging](docs/deployment.md)

## Non-goals for this phase

Embedded RDP rendering is done with FreeRDP; still deferred: cloud asset
discovery, NAT traversal, relay/agent deployment and any automatic execution of
AI-proposed commands. The interfaces exist so these can be added without
changing the security model.
