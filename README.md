# Nexus Remote

A Windows desktop workspace for remote access and device management: SSH/SFTP
sessions, a real interactive terminal, an optional native RDP launch, device
inventory, diagnostics and a deterministic assistant. Everything that matters
works offline; no AI model is required to connect, diagnose or run a playbook.

> **Status: early MVP (0.1.0).** SSH, SFTP and interactive terminal emulation
> are implemented. RDP launches `mstsc.exe` with a generated profile. SMB is an
> optional dependency and is not wired into the UI yet. Cloud discovery, NAT
> traversal and remote agents are deliberately deferred.

## Highlights

- **Device inventory** - environments, tags, criticality, per-protocol
  endpoints, groups, favourites, YAML import/export (secrets never exported).
- **Sessions** - multiple concurrent SSH/SFTP sessions, state machine, route
  selection, host-key pinning, audit trail.
- **Interactive terminal** - VT100/xterm emulation via `pyte`: cursor movement,
  colours, full-screen programs, scrollback, PTY resize, copy/paste, bracketed
  paste and application cursor keys.
- **SFTP file manager** - two-pane browser, streamed 512 KB transfers, resume
  and cancel.
- **Diagnostics** - DNS, TCP handshake, protocol banner, local route lookup,
  interface/VPN detection and ICMP (with honest caveats about what each result
  proves).
- **Deterministic assistant** - offline autocomplete with provenance labels,
  history, snippets, playbooks and rule-based error diagnosis. Optional model
  adapters are clearly labelled *unverified* and can never execute anything.
- **Security-first defaults** - credentials stay in Windows Credential Manager;
  SQLite and logs hold references only. Unknown SSH host keys are refused until
  the operator trusts the fingerprint. Dangerous commands are blocked by a
  deterministic policy engine.

## Requirements

- Windows 10/11 (the UI also runs on Linux/macOS for development).
- Python 3.11+ (developed on 3.13).
- Optional external tools: `mstsc.exe` for RDP. SMB support is optional and not
  yet exposed in the UI.

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
  protocols/           SSH/SFTP, RDP launcher, optional SMB
  diagnostics/         DNS, TCP, routing, interfaces, reports
  assistant/           autocomplete, history, snippets, errors, playbooks
  ui/                  PySide6 pages, theme, terminal widget, workers
```

## Documentation

- [Architecture](docs/architecture.md)
- [Threat model](docs/threat-model.md)
- [Security guide](docs/security.md)
- [Deployment and packaging](docs/deployment.md)

## Non-goals for this phase

Embedded RDP rendering (FreeRDP), SMB browsing in the UI, cloud asset
discovery, NAT traversal, relay/agent deployment and any automatic execution of
AI-proposed commands. The interfaces exist so these can be added without
changing the security model.
