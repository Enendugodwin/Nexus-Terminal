# Architecture

Nexus Remote is a modular monolith: one desktop process, one SQLite database,
and clear seams where a gateway, relay or endpoint agent can be added later.

## Layers

```
UI (PySide6)
  main_window, dashboard, device_manager, workspace,
  terminal_view, diagnostics_view, assistant_panel, security_center
        |
Services (services.py)
  AppServices: the only object graph; tests build it too
        |
+----------------+----------------+----------------+----------------+
| connections    | diagnostics    | assistant      | security       |
| manager        | dns/tcp/route  | autocomplete   | vault          |
| session        | interfaces     | history        | audit          |
| routing        | reports        | snippets       | host keys      |
|                |                | errors         | policy         |
+----------------+----------------+----------------+----------------+
        |
protocols: SSH/SFTP (paramiko), RDP (embedded FreeRDP), SMB (optional)
        |
storage: SQLite + forward-only migrations, FTS5 when available
```

The UI never talks to a transport directly for privileged actions. It asks
`SessionManager`, which consults `PolicyEngine`, `CredentialService`, `KnownHosts`
and `AuditLog` first.

## Key flows

### Connecting

1. `SessionManager.open(device, protocol)` validates that the protocol is
   enabled and an adapter is registered, then creates a `Session` in `pending`.
2. `SessionManager.connect(session)` resolves the route (`RoutingEngine`), asks
   the vault for the stored secret, builds a `ConnectionTarget` and drives the
   adapter.
3. The adapter (e.g. `ParamikoSSHAdapter`) verifies the host key against
   `KnownHosts` before authentication. An unknown key is stored as `pending` and
   the connection is refused; the UI can show the fingerprint for explicit trust.
4. Success transitions to `connected`, records `last_connection_ok` and writes an
   audit event. Failures are classified (`auth`, `timeout`, `refused`,
   `host_key`, ...) and recorded.

### Interactive terminal

`ParamikoSSHAdapter.open_shell()` returns a `ShellChannel` (an `invoke_shell`
PTY). `TerminalView` polls it on a 25 ms Qt timer, feeds bytes to
`TerminalBuffer` (`pyte.HistoryScreen` + `ByteStream`), and paints the screen.
Keystrokes are translated by `encode_key` (arrows, function keys, control
characters, application-cursor mode). Resizes are computed from font metrics
and sent with `resize_pty`. Scrollback is retained in pyte's history buffer;
selection and clipboard stay local. Remote output is rendered only - it is
never evaluated.

### One-off commands

The SSH console tab runs `SessionManager.execute()`. `PolicyEngine.classify()`
labels the command `read-only`, `modifies-state`, `destructive` or `unknown`.
Destructive commands are denied unless explicitly enabled *and* approved;
state-changing commands require confirmation. Denials and approvals are audited.

### Assistant

Autocomplete, snippets, playbooks and error diagnosis are local and
deterministic. Model adapters are optional; their suggestions carry the
`AI-generated (unverified)` label, rank below verified/history entries, and are
never executed automatically.

## Data model (SQLite)

Forward-only migrations tracked by `PRAGMA user_version`:

- `devices`, `connection_profiles`, `routing_profiles`, `device_groups`
- `credential_refs` - vault key names only, never secrets
- `host_keys` - trusted/pending fingerprints and key blobs
- `command_history`, `command_snippets`
- `audit_events`, `settings`

`002_search_fts.sql` adds FTS5 indexes; if the SQLite build lacks FTS5 the
migration is skipped and search falls back to `LIKE`/in-memory matching.

## Extension points

- **New protocol**: implement `ProtocolAdapter` (`connections/interfaces.py`),
  register it in `protocols/__init__.py`; the session/policy/audit path is
  reused unchanged.
- **New route**: add a method to `ROUTE_METHODS`/`ROUTE_ORDER` and teach
  `PolicyEngine._route_target` how to validate its configuration.
- **Gateway/agent/relay**: they are separate deployables. Route kinds
  (`agent`, `relay`, `cloud_private`) are already reserved and denied until
  configured and allow-listed.
- **Model provider**: implement the small adapter protocol in
  `assistant/model_adapters.py`. It cannot bypass policy.
