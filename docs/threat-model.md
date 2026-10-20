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
| Command injection through device fields | Arbitrary local execution | No `shell=True` command construction from profile data. SSH uses paramiko channels; RDP builds an argv list passed to FreeRDP without a shell. |
| Serial console misuse | Unauthorised device changes | A serial line has no authentication; the app only opens the local port named in a profile. Typed bytes are user-initiated and outside the command gate, exactly like a physical console cable. |
| Local PowerShell misuse | Unauthorised local changes | The local shell uses the operator's Windows account and is outside the remote policy gate by design. It never starts at launch, requires an explicit Start, warns on the page, audits start/stop, and is terminated when the app closes. It grants nothing the same user could not do by opening PowerShell. |
| API key sent to a malicious or plaintext endpoint | Credential theft | Keys are read from the OS vault only for the selected provider. A keyed request over plain HTTP is refused unless the host is loopback. The endpoint is visible in Settings and audited; no silent provider fallback exists. |
| Prompt or answer disclosure to a provider | Data leakage | Only the operator's question and optional pasted context are sent; terminal output is never uploaded automatically. Answers are unverified text and audit records exclude prompt and answer bodies. Treat providers as third parties. |
| SSH key file substitution | Impersonation / key theft | Key files are referenced by path only (never copied); the passphrase stays in the vault. An attacker who can replace the file already has the operator's filesystem access. |
| RDP credential exposure | Credential theft | The password is written to the FreeRDP client's standard input, never to the command line (where any local process could read it) and never to disk. |
| Clipboard leakage | Secret exposure | Clipboard use is explicit (copy/paste). No automatic clipboard sync from remote sessions. |
| Local attacker as the same user | Full compromise | Out of scope for app-level controls: Windows DPAPI/ACL protects the vault and data directory. Use a locked workstation and separate accounts. |
| Database tampering | Misleading inventory/audit | SQLite has no signatures. Restrict NTFS permissions on `%LOCALAPPDATA%\NexusRemote`; export/verify audit logs to a protected location if needed. |
| Supply-chain compromise | Arbitrary code | Dependencies are pinned by lower bounds; build from a reviewed lock/venv and sign the executable. PyInstaller bundles increase the attack surface. |
| Malicious update feed | Arbitrary code | HTTPS only, strict schema and size bounds. Installers are downloaded only after consent and must match the manifest's SHA-256 and size; nothing auto-installs. |
| Tampered update download | Arbitrary code | Streamed hash verification with a mandatory digest; partial or mismatched files are deleted before they can be executed. Sign release builds for publisher identity. |
| Denial of service by remote output floods | UI freeze | Reads are bounded per timer tick; scrollback is capped; the UI remains responsive. |
| Network scanning / unintended egress | Policy violation | The app only connects to inventory entries and hosts typed into diagnostics. There is no discovery sweep in this phase. |

## Explicit non-goals

- Protection against a compromised operating system or a malicious administrator.
- Hiding inventory metadata from someone who can read the user's files.
- Multi-user or server-side access control (the app is single-user desktop).
- Securing the remote host's RDP stack; FreeRDP is trusted as the client and
  renders inside the workspace window. Certificate verification is on by default
  and ignoring it is an explicit per-session opt-in.

## Residual risks

- A user who trusts a wrong SSH fingerprint defeats host-key verification.
- A user who enables destructive commands and confirms a prompt can still cause
  damage; that is an explicit, audited decision.
- Model providers may log prompts; only send data you are allowed to share.
- Memory-resident secrets cannot be reliably zeroed in Python.
