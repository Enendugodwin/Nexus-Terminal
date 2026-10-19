# Threat model

Scope: the desktop application, its local data, and the connections it makes to
remote devices. Gateway, relay and endpoint-agent components are out of scope
for this phase; their interfaces are reserved but not deployed.

## Assets

1. Credentials: passwords, SSH keys, passphrases, API tokens.
2. Device inventory and routing knowledge (a map of the estate).
3. Audit history and command history (may reveal operational detail).
4. Integrity of commands sent to production systems.
5. The user's workstation and clipboard.

## Trust boundaries

```
[operator] --UI--> [Nexus Remote process] --SSH/SFTP--> [remote device]
                         |  |  |
                         |  |  +--> Windows Credential Manager (OS boundary)
                         |  +-----> SQLite / logs on disk (local boundary)
                         +--------> optional model endpoint (network boundary)
```

- **Remote output is untrusted.** Terminal escape sequences are rendered by
  pyte; no content is executed, evaluated or used to build shell commands.
- **The model is untrusted and non-authoritative.** Model output is text shown
  with an *unverified* label. It cannot call tools, approve commands or alter
  policy.
- **Local storage is semi-trusted.** It is protected by the OS user account, not
  by application-level encryption (secrets excepted).

## Threats and mitigations

| Threat | Impact | Mitigation |
| --- | --- | --- |
| Credential theft from disk | Account compromise | Secrets live only in Windows Credential Manager; SQLite stores opaque key names. Tests assert no secret appears in DB rows. |
| Secret leakage into logs/audit | Account compromise | `RedactingFilter` masks sensitive keys and patterns in logs; `AuditLog._sanitize` redacts dictionaries before insert; audit details are JSON and bounded. |
| SSH man-in-the-middle | Full session compromise | Unknown host keys are refused and stored as `pending`; changed keys are flagged. Trust requires explicit fingerprint confirmation in the UI. |
| Accidental destructive command | Data loss / outage | Deterministic `PolicyEngine` denies destructive patterns; state changes require confirmation; every denial is audited. |
| Remote escape-sequence attack | UI spoofing, clipboard abuse | pyte parses bytes into a character grid; the renderer never interprets control sequences as application actions. OSC title changes only update a status label. |
| Malicious model suggestion | Running attacker commands | AI suggestions are labelled unverified, ranked below verified/history, and never auto-executed. Execution still passes the policy gate. |
| Command injection through device fields | Arbitrary local execution | No `shell=True` command construction from profile data. SSH uses paramiko channels; RDP writes parameters into a data file, not a command line. |
| RDP credential exposure | Credential theft | The generated `.rdp` file contains no password; Windows prompts. It is written under the user's temp directory and never stores secret material. |
| Clipboard leakage | Secret exposure | Clipboard use is explicit (copy/paste). No automatic clipboard sync from remote sessions. |
| Local attacker as the same user | Full compromise | Out of scope for app-level controls: Windows DPAPI/ACL protects the vault and data directory. Use a locked workstation and separate accounts. |
| Database tampering | Misleading inventory/audit | SQLite has no signatures. Restrict NTFS permissions on `%LOCALAPPDATA%\NexusRemote`; export/verify audit logs to a protected location if needed. |
| Supply-chain compromise | Arbitrary code | Dependencies are pinned by lower bounds; build from a reviewed lock/venv and sign the executable. PyInstaller bundles increase the attack surface. |
| Denial of service by remote output floods | UI freeze | Reads are bounded per timer tick; scrollback is capped; the UI remains responsive. |
| Network scanning / unintended egress | Policy violation | The app only connects to inventory entries and hosts typed into diagnostics. There is no discovery sweep in this phase. |

## Explicit non-goals

- Protection against a compromised operating system or a malicious administrator.
- Hiding inventory metadata from someone who can read the user's files.
- Multi-user or server-side access control (the app is single-user desktop).
- Embedded RDP rendering; `mstsc.exe` is trusted as an OS component.

## Residual risks

- A user who trusts a wrong SSH fingerprint defeats host-key verification.
- A user who enables destructive commands and confirms a prompt can still cause
  damage; that is an explicit, audited decision.
- Model providers may log prompts; only send data you are allowed to share.
- Memory-resident secrets cannot be reliably zeroed in Python.
