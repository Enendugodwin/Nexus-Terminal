# Deployment

## Supported environment

- Windows 10/11, 64-bit.
- Python 3.11+ for a source install. The packaged bundle includes its own runtime.
- FreeRDP for RDP (`winget install FreeRDP.FreeRDP`, `choco install freerdp`, or
  place `wlfreerdp.exe` on `PATH`). The app embeds the FreeRDP window in the
  workspace; it does not launch `mstsc.exe`.
- No administrator rights are required. The app is a per-user desktop tool and
  does not install a service.

## Source install

```powershell
git clone <repository>
cd nexus-remote
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m nexus_remote.main --selftest
.\.venv\Scripts\python.exe -m nexus_remote.main
```

`app.py` runs directly from a checkout without an editable install.

## Data directory

Default: `%LOCALAPPDATA%\NexusRemote`

```
nexus.db            SQLite inventory, history, audit events
known_hosts         local export of trusted host keys
logs\               nexus-remote.log (rotating) + audit.jsonl
exports\            profile exports
```

Override with `--data-dir <path>` or `NEXUS_REMOTE_HOME`. Use a local path, not a
synced folder, and keep it out of backups that are less protected than the
workstation.

For a portable install on a USB stick:

```powershell
$env:NEXUS_REMOTE_HOME = "E:\NexusRemoteData"
.\NexusRemote.exe
```

Secrets still live in the Windows Credential Manager, not on the stick.

## Build a Windows bundle

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,build]"
.\packaging\build.ps1
```

Output: `dist\NexusRemote\NexusRemote.exe` (one-directory bundle).

The spec bundles Qt, paramiko, keyring and pyte, and includes the SQL migration
files. `console=False`, so `--selftest` output is not visible in this build; run
the self-test from the source environment. If you need a diagnostic build, change
`console=False` to `True` in `packaging/nexus_remote.spec` and rebuild.

### Code signing

Unsigned executables trigger SmartScreen warnings. Sign the bundle with an
Authenticode certificate:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
  /a dist\NexusRemote\NexusRemote.exe
```

Distribute a SHA-256 hash alongside the package and verify it before deployment.

## First-run checklist

1. Launch the app and open **Settings**; confirm the vault backend shows
   `WinVaultKeyring`.
2. Add a device manually or import a YAML profile.
3. Connect once; compare and trust the SSH fingerprint out of band.
4. Run **Diagnostics** against the host and verify DNS/TCP/route results.
5. Confirm the policy defaults: destructive commands off, confirmation on.
6. Record the data directory and backup/ACL expectations.

## Upgrade

Migrations are forward-only (`PRAGMA user_version`). Before replacing a build:

1. Close the app.
2. Copy `nexus.db`, `logs\` and `known_hosts` to a safe location.
3. Install the new build and launch it once; verify the version and inventory.
4. Keep the backup until the new version has been used successfully.

Downgrading is not supported: an older build will refuse a database with a newer
schema version.

## Uninstall

1. Revoke stored credentials on the **Security** page (or remove them with
   `cmdkey /delete:nexus-remote` entries).
2. Delete `%LOCALAPPDATA%\NexusRemote` after exporting anything you need.
3. Delete the application folder. There is no registry footprint beyond the
   credential entries.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `no usable OS credential backend` | The Windows Credential Manager is unavailable (service disabled, locked-down session). Run as the interactive user; do not use a service account. |
| Qt platform plugin error | The bundle is incomplete or `QT_PLUGIN_PATH` points elsewhere. Rebuild with the provided spec; do not mix a system Qt with the bundle. |
| `pyte` import error | Source install is missing dependencies: `pip install -e .`. |
| RDP client not found | FreeRDP is not installed. Run `winget install FreeRDP.FreeRDP` (or `choco install freerdp`) and reopen the app, or add `wlfreerdp.exe` to `PATH`. |
| Host key refused | Unknown or changed key. Verify the fingerprint with the device owner, then trust it in the prompt. |
| Database is locked | Another instance is running, or an antivirus scanner holds the file. Close other instances and retry. |
| SmartScreen warns | Expected for unsigned builds. Sign the executable or allow it explicitly. |

## Security operations

- Bind nothing to the network: this is a local desktop app, not a web service.
- Restrict NTFS permissions on the data directory to the operator's account.
- Forward `logs\audit.jsonl` to a protected collector if audit retention matters.
- Keep Windows, Python, Qt and paramiko patched; rebuild the bundle after
  dependency updates.
