# Architecture

Nexus Remote is a modular monolith: one desktop process, one SQLite database,
and clear seams where a gateway, relay or endpoint agent can be added later.

## Layers

```
UI (PySide6)
  main_window, dashboard, device_manager, workspace, powershell_page,
  terminal_view, system_info, diagnostics_view, assistant_panel, security_center
        |
Services (services.py)
  AppServices: the only object graph; tests build it too
        |
+----------------+----------------+----------------+----------------+
| connections    | diagnostics    | assistant      | security       |
| manager        | dns/tcp/route  | autocomplete   | vault          |
| session        | sysinfo        | history        | audit          |
| routing        | reports        | snippets       | host keys      |
|                |                | errors         | policy         |
+----------------+----------------+----------------+----------------+
        |
protocols: SSH/SFTP (paramiko), serial (pyserial), RDP (FreeRDP), SMB 2/3 (optional)
        |
local console: PowerShell via Windows ConPTY (pywinpty)
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
   adapter. With `ssh_key` authentication the manager loads the key text and
   passphrase from the vault (kinds `ssh_private_key` / `ssh_key_passphrase`) and
   passes the configured key file path as `options["key_filename"]`; nothing
   secret is written to the device profile.
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

Under the terminal, `SystemInfoPanel` shows five cards: CPU, memory, storage,
operating system and uptime. `diagnostics/sysinfo.py` runs one fixed, read-only
POSIX probe over the same SSH client (`exec_command`) on a worker thread,
parses the sectioned output into `SystemInfo`, and the panel only formats it.
The probe is a constant, so it is not routed through the command policy gate;
the collection is still recorded in the audit log (`sysinfo.collected`). Hosts
without POSIX tools (for example Windows OpenSSH) show an "unavailable" note
instead of an error dialog.

### File manager

The Files tab works with either transport. `SessionManager` exposes connected
`sftp` and `smb` sessions in one picker; both adapters implement `listdir`,
`mkdir`, `remove`, `rmdir` and streaming `upload`/`download` with progress,
resume and cancellation, so the pane's path handling (`""`/`.` is the root) is
shared. SMB paths stay share-relative and forward-slash; `SMBAdapter._unc()`
converts them to UNC for `smbprotocol`. SMB2/3 only - there is no SMB1 fallback.

### Serial consoles

`SerialAdapter` (`protocols/serial.py`) opens a local COM port or POSIX tty with
pyserial from a device's `serial` connection (port, baud, data bits, parity,
stop bits, flow control). It exposes a `SerialChannel` with the same
read/write/resize/close surface as the SSH shell, so `TerminalView` attaches to
it unchanged. Reads are non-blocking (`in_waiting`) because the view polls on the
GUI timer. A serial line has no login, no `exec_command` and no PTY size, so
system information is marked unavailable and the console tab rejects the
session; typed bytes go straight to the device, outside the command gate, as
with a physical console cable.

### Local PowerShell

The **PowerShell** page is separate from sessions: `host_shell.PtyChannel`
spawns a chosen `powershell.exe` or `pwsh.exe` under Windows ConPTY via
`pywinpty`, with a selected working directory and the normal user profile. It
exposes the same `read`/`write`/`resize`/`close`/`closed` surface as the SSH and
serial channels, so `TerminalView` attaches unchanged. A background reader
thread performs the blocking ConPTY read into a queue; the Qt timer only drains
it, so a silent shell cannot freeze the UI. The shell never starts at launch,
is stopped from its page, and is terminated by `MainWindow.closeEvent`. Local
commands run with the operator's Windows account and are explicitly outside the
remote policy gate; start/stop events are audited. See
[local-shell.md](local-shell.md).

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

### Command catalog and vendor feed
The completion engine starts from `assistant/builtin_catalog.py`: roughly 390
curated commands across bash, PowerShell, cmd, Cisco IOS and PAN-OS. This file
is data only and ships inside the application, so an offline install still has
the full baseline.

A vendor feed adds commands at runtime. `assistant/catalog.py` validates the
JSON (schema 1, known environments, single-line commands, count/size limits) and
`assistant/catalog_update.py` fetches it over HTTPS with ETag caching and a hard
response-size cap. Accepted documents are written to
`<data>/catalog.json` through a temporary file plus `os.replace`, so a crash
cannot leave a half-written catalog. Invalid documents reject the whole update
and leave the previous file untouched.

`CompletionEngine` merges downloaded entries *after* the bundled ones: bundled
names always win, and downloaded entries carry `SuggestionSource.CATALOG`
("Catalog (downloaded)") rather than `VERIFIED`. This is deliberate — the feed
is remote input and must not gain the trust of a shipped entry, let alone
execute anything. Updates are audited (`catalog.updated`,
`catalog.update_failed`); the Commands page performs them in a worker thread and
swaps the engine via `AppServices.reload_catalog()`. When `catalog.auto_check`
is enabled and a feed URL is configured, `MainWindow` schedules one quiet check
four seconds after startup; being offline is a normal, silent outcome. See
[docs/catalog-feed.md](catalog-feed.md) for the feed format.

### Themes and appearance
`ui/theme.py` declares four palettes (Matrix, Midnight, Ember, Daylight) as
token dictionaries: surfaces, borders, text, accent, semantic colours, terminal
background/foreground and a readable ANSI map. `TOKENS` is a single mutable
mapping imported by widgets, so `set_theme()` can swap the active palette
without rebuilding pages. `apply_theme()` sets the Fusion style, a matching
`QPalette` and the generated stylesheet on the `QApplication`.

The Settings page exposes the choice; `MainWindow._apply_theme()` applies it
immediately, repaints the current page and stores `ui.theme` in the settings
table. `main.py` applies the stored theme before constructing the window, so
there is no flash of the default palette. Custom-painted widgets (the terminal,
ghost-text completion) read the active tokens at paint time; table colour maps
are computed on use rather than at import.

The window/taskbar icon comes from `resources/nexus.ico` (generated from
`packaging/icon.svg` by `packaging/make_icon.py`). Windows receives an explicit
`AppUserModelID` (`resources.set_windows_app_id`) so the taskbar uses the app
icon and groups windows correctly.

### Application updates
`updates.py` implements an optional update channel separate from the command
catalog. A configured HTTPS manifest names a newer version, a `.exe` installer,
its size and SHA-256. Checks are bounded (128 KB manifest, 15 s timeout) and
never raise: offline, HTTP and schema failures become result objects. The update
is only downloaded after confirmation, capped at 300 MB, hashed while streaming
and rejected unless size and hash match. A verified installer is handed to
Windows via `os.startfile`; the app then quits so Inno Setup can replace files.
Automatic checks merely add a status-bar hint. See
[docs/updates.md](updates.md).

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

`assistant/providers.py` owns the provider catalogue and settings migration
(the pre-0.3 keys were Ollama-only). `assistant/model_adapters.py` implements
Ollama, OpenAI Chat Completions (also OpenChat/OpenRouter/LM Studio/vLLM),
Anthropic Messages and Gemini generateContent. The active adapter is rebuilt
from settings and the OS vault at startup and after **Save settings**;
construction is offline. The assistant sends only the typed question and
optional context, treats responses as untrusted text, and audits provider,
model, character count and outcome without storing prompt or answer text. See
[ai-providers.md](ai-providers.md).

Two buttons sit beside the Prefix box. **Send to shell** pastes the command into
the attached interactive terminal without a newline, so the operator still
presses Enter; with no shell attached it falls back to loading the SSH console
input. **Copy** writes the command to the clipboard. Both are disabled while the
box is empty, and neither can execute anything by itself.

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
  `assistant/model_adapters.py` and add a preset in `assistant/providers.py`.
  It cannot bypass policy, and API keys belong in the vault.
