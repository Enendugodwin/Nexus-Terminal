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
protocols: SSH/SFTP (paramiko), RDP (embedded FreeRDP), SMB 2/3 (optional)
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

### File manager

The Files tab works with either transport. `SessionManager` exposes connected
`sftp` and `smb` sessions in one picker; both adapters implement `listdir`,
`mkdir`, `remove`, `rmdir` and streaming `upload`/`download` with progress,
resume and cancellation, so the pane's path handling (`""`/`.` is the root) is
shared. SMB paths stay share-relative and forward-slash; `SMBAdapter._unc()`
converts them to UNC for `smbprotocol`. SMB2/3 only - there is no SMB1 fallback.

### One-off commands
The SSH console tab runs `SessionManager.execute()`. `PolicyEngine.classify()`
labels the command `read-only`, `modifies-state`, `destructive` or `unknown`.
Destructive commands are denied unless explicitly enabled *and* approved;
state-changing commands require confirmation. Denials and approvals are audited.

### Saved commands
The Commands page (`ui/commands_page.py`) is a manager over `SnippetStore`
(`command_snippets`): search by name, command, description or tag, filter by
environment, and create/edit/delete entries with a live risk label from
`PolicyEngine.classify()`. The dialog uses the same `SuggestionLineEdit` as the
console, so saved commands get the same inline completion and provenance rules.

Saved commands are data, not actions. "Send to console" only fills the SSH
console input (and switches the environment); running it still goes through
`run_command()` and the policy gate. The console's *Save command…* button opens
the same dialog prefilled with the typed line, so the library is also reachable
where commands are written. Snippets saved from the assistant or from history
appear in both places because they share one store.

### Assistant

The assistant lives beside the open terminal (Terminal tab, right-hand pane)
rather than as a separate page: its environment follows the attached shell
(Windows → PowerShell, Linux → bash). Tabs for suggestions, history, error
diagnosis/playbooks and optional AI remain available, and the pane can be
collapsed with the Assistant toggle.

Command completion is inline: `SuggestionLineEdit` (ui/command_input.py) paints
the engine's best match after the cursor in the faint token colour, so the
proposal is visibly not part of the line and can never be submitted, copied or
selected. Tab or Right-arrow at the end accepts it, Escape dismisses it, and
Up/Down drive a companion list that Enter fills in before anything runs. The same
widget is used by the SSH console and the assistant's Prefix box. Autocomplete,
snippets, playbooks and error diagnosis are local and deterministic. Model
adapters are optional; their suggestions carry the `AI-generated (unverified)`
label, rank below verified/history entries, and are never executed
automatically.

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
