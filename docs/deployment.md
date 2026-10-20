# Deployment

## Supported environment

- Windows 10/11, 64-bit.
- Python 3.11+ for a source install. The packaged bundle includes its own runtime.
- FreeRDP for RDP (`winget install FreeRDP.FreeRDP`, `choco install freerdp`, or
  place `wlfreerdp.exe`/`sdl-freerdp.exe` on `PATH`). The app runs the FreeRDP
  client itself and re-parents its native window into the workspace at the Win32
  level (the SDL client ignores `/parent-window`); it never launches `mstsc.exe`.
  Windows has no stable FreeRDP binary release, so
  `packaging/install_freerdp.ps1` fetches the latest official win64 nightly into
  `%LOCALAPPDATA%\NexusRemote\tools\freerdp`, which the app scans first.
  TLS certificates use trust-on-first-use (`/cert:tofu`) by default; "Ignore
  certificate" is an explicit per-session opt-out.
- No administrator rights are required. The app is a per-user desktop tool and
  does not install a service.
- SMB browsing is optional: `pip install -e ".[smb]"` (or the packaged bundle
  below) adds `smbprotocol`, which speaks SMB 2/3 only. The connection profile
  needs the share name.

## Source install

```powershell
git clone <repository>
cd nexus-remote
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,smb]"
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

The spec bundles Qt, paramiko, keyring, pyte, pyserial, pywinpty (WinPTY plus a
ConPTY fallback for the local PowerShell page) and the icon/resources, and
includes the SQL migration files. The executable carries version metadata
(0.3.0), the multi-resolution application icon and an explicit Windows app ID so
the taskbar groups and displays it correctly. `console=False`, so `--selftest`
output is not visible in this build; run the self-test from the source
environment. If you need a diagnostic build, change `console=False` to `True` in
`packaging/nexus_remote.spec` and rebuild.

The icon is generated from `packaging/icon.svg`:

```powershell
.\.venv\Scripts\python.exe packaging\make_icon.py
```

This writes `src/nexus_remote/resources/nexus.ico` (16-256 px) and `nexus.png`.
Commit both so builds do not depend on the generator.

## Build the installer

The setup executable is produced with Inno Setup 6 (per-user, no elevation):

```powershell
winget install JRSoftware.InnoSetup
& "$env:LOCALAPPDATA\Programs\Inno\ISCC.exe" packaging\installer.iss
```

Output: `dist\NexusRemoteSetup-<version>.exe` (about 40 MB).

`packaging/installer.iss` defines the per-user install directory
(`%LOCALAPPDATA%\Programs\Nexus Remote`), Start Menu shortcut, optional desktop
shortcut, uninstaller and version metadata. It does **not** remove user data.
For a silent deployment:

```powershell
.\NexusRemoteSetup-0.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

Rebuild the bundle before the installer whenever code changes; the installer
compiles `dist\NexusRemote\*` at build time.

The optional update feed is a JSON manifest pointing at that setup file. Generate
it after signing:

```powershell
.\.venv\Scripts\python.exe packaging\make_manifest.py `
  --url https://downloads.example.com/NexusRemoteSetup-0.3.0.exe `
  --notes "Themes and verified updates"
```

See [updates.md](updates.md) for hosting, schema and release steps.

### Code signing

Unsigned executables trigger SmartScreen warnings. Sign the bundle with an
Authenticode certificate **before** building the installer, then sign the setup:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
  /a dist\NexusRemote\NexusRemote.exe
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
  /a dist\NexusRemoteSetup-0.3.0.exe
```

Distribute a SHA-256 hash alongside the package and verify it before deployment.
The optional update feed also verifies the installer's hash and is documented in
[docs/updates.md](updates.md).

## First-run checklist

1. Launch the app and open **Settings**; confirm the vault backend shows
   `WinVaultKeyring`.
2. Add a device manually or import a YAML profile.
3. Connect once; compare and trust the SSH fingerprint out of band.
4. Run **Diagnostics** against the host and verify DNS/TCP/route results.
5. Confirm the policy defaults: destructive commands off, confirmation on.
6. Record the data directory and backup/ACL expectations.

## Upgrade

Migrations are forward-only (`PRAGMA user_version`). The packaged installer
upgrades in place and preserves `%LOCALAPPDATA%\NexusRemote`. Before any manual
replacement or a major upgrade:

1. Close the app.
2. Copy `nexus.db`, `logs\` and `known_hosts` to a safe location.
3. Install the new build and launch it once; verify the version and inventory.
4. Keep the backup until the new version has been used successfully.

Downgrading is not supported: an older build will refuse a database with a newer
schema version. To roll back, install the previous setup file manually; it keeps
the same data directory and still refuses a newer schema.

The in-app updater offers newer versions only and requires explicit consent (see
`docs/updates.md`).

## Uninstall

1. Revoke stored credentials on the **Security** page (or remove them with
   `cmdkey /delete:nexus-remote` entries).
2. Uninstall **Nexus Remote** from **Settings > Apps > Installed apps**. This
   removes the program files and shortcuts; the uninstaller deliberately leaves
   `%LOCALAPPDATA%\NexusRemote` in place.
3. Delete `%LOCALAPPDATA%\NexusRemote` after exporting anything you need.

The only registry footprint is the per-user uninstall entry (and any credential
entries you created).

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
