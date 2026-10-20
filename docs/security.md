# Security guide

## Credentials

- The only secret store is the OS vault through `keyring` (Windows Credential
  Manager, service prefix `nexus-remote`).
- `credential_refs` in SQLite stores `device_id`, `kind`, `username`, a unique
  `key_name`, and a timestamp. The value is fetched from the vault on demand and
  passed to the protocol adapter for one connection attempt.
- One-time passwords typed into the connect form are used once and cleared; they
  are never written to the database, logs or session metadata.
- If no vault backend is available the app falls back to an in-memory backend and
  reports it as non-persistent. Do not rely on that mode for production.
- SSH private keys are secrets too: paste a key into the vault as `ssh_private_key`
  and its passphrase as `ssh_key_passphrase`. The device profile only stores an
  optional key *file path*, never key material or the passphrase.
- A serial console needs no credential and has no login; physical access to the
  port is the access control. Its typed bytes are not command-gated (there is no
  shell to gate) and system information is unavailable on serial sessions.
- Revoking a device credential deletes the vault entry and its reference; deleting
  a device cascades the references but **does not** automatically delete vault
  entries. Use the Security page before removing a device if the secret must be
  destroyed.

## SSH host keys

- `KnownHosts` implements a paramiko policy that distinguishes unknown, trusted
  and changed keys.
- Unknown keys are stored with status `pending`, the connection is refused, and
  the UI shows the fingerprint for comparison with the device owner's record.
- Trusting a key is an explicit action and writes an audit event. A changed key
  is treated as possible interception and is never auto-accepted.
- `known_hosts` is a local export of the database; the database is authoritative.

## Command policy

`PolicyEngine` classifies commands before execution:

- `destructive` (e.g. disk writes, recursive deletes, service stops, user
  changes): denied unless `policy.allow_destructive` is enabled **and** the
  operator confirms each command.
- `modifies-state` (package installs, `systemctl restart`, registry changes,
  `git push`, `sudo`, ...): requires confirmation by default.
- `read-only` (inspection commands): allowed.
- `unknown`: allowed only through the normal confirmation path; treat as
  unverified.

Settings live in the `settings` table and are loaded into the policy at startup.
The policy engine is deterministic and has no model dependency.

## Terminal safety

- Remote output is parsed by pyte and painted; it is never executed.
- OSC sequences can change the window title only, which is displayed as text.
- The terminal does not automatically open links or paste on focus.
- Copy is explicit (`Ctrl+Shift+C` or the context menu). If a selection exists,
  `Ctrl+C` copies instead of sending an interrupt; otherwise it sends `^C`.

## Logging and audit

- Application logs rotate at 2 MB with three backups under the data directory.
- Both file and stream handlers use `RedactingFilter`, which masks values under
  sensitive key names and common token/password patterns.
- Audit events are stored in SQLite and appended as JSON lines to
  `logs/audit.jsonl`. Detail dictionaries are sanitised before storage.
- Subject lines and command text are redacted but may still contain hostnames and
  paths; protect the data directory accordingly.

## Data at rest

| Data | Location | Protection |
| --- | --- | --- |
| Secrets | Windows Credential Manager | OS user account + DPAPI |
| Inventory, history, audit | `%LOCALAPPDATA%\NexusRemote\nexus.db` | NTFS ACLs only |
| Logs | `...\logs\` | NTFS ACLs, redaction |
| Known hosts | DB + `...\known_hosts` | NTFS ACLs |
| Exports | `...\exports\`, or user-chosen path | User responsibility; secrets are refused |

The database is **not** encrypted. If inventory confidentiality matters, use
BitLocker/EFS and a dedicated Windows account.

## Application updates

- The update channel is disabled until an HTTPS manifest URL is configured.
- Checks are bounded (128 KB, 15 seconds) and failures are silent during an
  automatic check; they never block startup.
- An installer is downloaded only after explicit confirmation, capped at
  300 MB and accepted only when its size and SHA-256 match the manifest.
- Nothing is installed silently or in the background; the app quits and lets the
  Inno Setup wizard run after the operator consents.
- The update subsystem is independent of credentials, sessions and inventory:
  it sends no device data and stores no secrets. A downloaded installer lives in
  `%LOCALAPPDATA%\NexusRemote\updates\` until it is run.
- HTTPS authenticates the transport; the SHA-256 protects integrity. Publisher
  identity requires Authenticode signing of both the app and the setup file.

## Operational recommendations

1. Use a dedicated, non-admin Windows account for remote administration.
2. Keep the data directory off synced/cloud folders.
3. Verify SSH fingerprints out of band before trusting them.
4. Leave destructive commands disabled unless a change window requires them.
5. Review the Security page and `logs/audit.jsonl` after incidents.
6. Rotate credentials and host keys if a workstation is lost or shared.
7. Treat model endpoints as third parties; never send secrets or customer data.
8. Sign and hash release builds; verify the hash before deployment.
9. Point the update feed at an HTTPS server you control, publish `latest.json`
   deliberately and keep the previous setup file for rollback.

## Incident response

1. Disconnect active sessions and close the app.
2. Revoke affected credentials in Windows Credential Manager (Security page or
   `cmdkey /delete`), then rotate them at the target.
3. Remove the affected device's trusted host key and re-verify the fingerprint.
4. Export and preserve `logs/audit.jsonl` and the SQLite database before cleanup.
5. Rebuild the workstation or at least reinstall the app from a trusted package.
