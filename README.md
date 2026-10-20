# Nexus Remote

A Windows desktop workspace for remote access and device management: SSH/SFTP
sessions, a real interactive terminal, an embedded FreeRDP desktop, device
inventory, diagnostics and a deterministic assistant. Everything that matters
works offline; no AI model is required to connect, diagnose or run a playbook.

> **Status: early MVP (0.1.0).** SSH, SFTP, interactive terminal emulation and
> SMB browsing are implemented. RDP is performed by the application itself
> through the FreeRDP client, embedded in the workspace window (`mstsc.exe` is
> not used). SMB needs the optional `smbprotocol` extra and is SMB 2/3 only.
> Cloud discovery, NAT traversal and remote agents are deliberately deferred.

## Highlights

- **Device inventory** - environments, tags, criticality, per-protocol
  endpoints, groups, favourites, YAML import/export (secrets never exported).
- **Sessions** - multiple concurrent SSH/SFTP sessions, state machine, route
  selection, host-key pinning, audit trail.
- **Interactive terminal** - VT100/xterm emulation via `pyte`: cursor movement,
  colours, full-screen programs, scrollback, PTY resize, copy/paste, bracketed
  paste and application cursor keys.
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
- **Deterministic assistant** - offline autocomplete with provenance labels,
  history, snippets, playbooks and rule-based error diagnosis. The best match is
  previewed inline (ghost text) while you type: Tab or → completes it, ↑ ↓ browse
  the full list, Enter completes then runs. Next to the prefix field, **Send**
  types it into the open interactive shell without pressing Enter, and **Copy**
  puts it on the clipboard. Optional model adapters are clearly labelled
  *unverified* and can never execute anything.
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

## Install and run

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
diagnostics, autocomplete/history/snippets, error rules, the terminal buffer
and the terminal widget (offscreen Qt).

## Project layout

```
src/nexus_remote/
  config.py            paths and constants
  services.py          single wiring point for the whole app
  main.py              CLI and GUI bootstrap
  storage/             SQLite connection, migrations, FTS5 fallback
  devices/             models, repository, YAML profiles
  security/            redaction, vault, audit, known_hosts, policy
  connections/         session state machine, routing, manager
  protocols/           SSH/SFTP, embedded FreeRDP, optional SMB
  diagnostics/         DNS, TCP, routing, interfaces, reports
  assistant/           autocomplete, history, snippets, errors, playbooks
  ui/                  PySide6 pages, theme, terminal widget, workers
```

## Documentation

- [Architecture](docs/architecture.md)
- [Command catalog feed](docs/catalog-feed.md)
- [Threat model](docs/threat-model.md)
- [Security guide](docs/security.md)
- [Deployment and packaging](docs/deployment.md)

## Non-goals for this phase

Embedded RDP rendering is done with FreeRDP; still deferred: cloud asset
discovery, NAT traversal, relay/agent deployment and any automatic execution of
AI-proposed commands. The interfaces exist so these can be added without
changing the security model.
