# Application updates

Nexus Remote has an optional, operator-controlled update channel. The app never
contacts a server unless an update feed URL is configured, and an installer is
never downloaded or run without an explicit confirmation.

## How it works

1. **Settings > Updates** holds the HTTPS **update feed URL** and an
   **automatic check** toggle (default on, but inert while the URL is empty).
2. At launch, if auto-check is enabled and a feed is configured, the app makes
   one bounded manifest request in the background. A newer version only adds a
   status-bar note; it never interrupts work.
3. **Check for updates now** performs the same request on demand and offers to
   download the installer when a newer version exists.
4. The installer is downloaded to
   `%LOCALAPPDATA%\NexusRemote\updates\`, hashed while streaming and accepted
   only if its size and SHA-256 match the manifest. A mismatch discards it.
5. After a successful download the app hands the `.exe` to Windows, asks the
   operator to let the wizard finish, and quits so its files can be replaced.
   Inno Setup upgrades in place and keeps the data directory.

There is no background download, no silent install, no delta patching and no
self-modifying executable. Rollback is a reinstall of the previous setup file.

## Manifest format

Host one small JSON document over HTTPS. The schema is deliberately minimal:

```json
{
  "schema": 1,
  "version": "0.3.0",
  "url": "https://downloads.example.com/NexusRemoteSetup-0.3.0.exe",
  "sha256": "9f2c...64 lowercase hex characters...",
  "size": 40569083,
  "notes": "Themes, serial console fixes and faster start-up.",
  "published": "2026-11-01"
}
```

| Field | Required | Rules |
| --- | --- | --- |
| `schema` | yes | Must be `1`. |
| `version` | yes | Dotted numeric version, e.g. `0.3.0` or `1.2`. Must be newer than the running build. |
| `url` | yes | `https://` absolute URL ending in `.exe`. |
| `sha256` | yes | 64 hex characters; lowercase is normalised. |
| `size` | recommended | Exact byte count of the installer. Enforced when non-zero. |
| `notes` | no | Plain text, shown before the download prompt (max 4 000 chars). |
| `published` | no | Free-form date label (max 40 chars). |

Limits: manifest read is capped at 128 KB with a 15-second timeout; installers
are capped at 300 MB. Unknown fields are ignored. Invalid manifests are rejected
without touching the filesystem.

Example `latest.json`:

```json
{
  "schema": 1,
  "version": "0.3.0",
  "url": "https://downloads.example.com/NexusRemoteSetup-0.3.0.exe",
  "sha256": "6b1f0c2d8a4e5f7091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708",
  "size": 40569083,
  "notes": "Adds an offline playbook editor and fixes serial reconnect.",
  "published": "2026-11-01"
}
```

## Release checklist

1. Bump `APP_VERSION` in `src/nexus_remote/config.py`, the version in
   `pyproject.toml`, and `packaging/version_info.txt`.
2. Run the tests and rebuild both artifacts:

   ```powershell
   .\packaging\build.ps1
   ISCC.exe packaging\installer.iss
   ```

3. Sign `dist\NexusRemote\NexusRemote.exe` **before** compiling the installer,
   then sign `dist\NexusRemoteSetup-<version>.exe`.
4. Generate the manifest (computes the hash and size):

   ```powershell
   .\.venv\Scripts\python.exe packaging\make_manifest.py `
     --url https://downloads.example.com/NexusRemoteSetup-0.3.0.exe `
     --notes "What changed" --published 2026-11-01
   ```

   Or compute them manually:

   ```powershell
   Get-FileHash .\dist\NexusRemoteSetup-0.3.0.exe -Algorithm SHA256
   Get-Item .\dist\NexusRemoteSetup-0.3.0.exe | Select-Object Length
   ```

5. Upload the setup file and `dist\latest.json`, and publish both over HTTPS.
6. Test from an older installed build: manual check, download, hash acceptance,
   upgrade and data preservation.

Keep the previous setup file available so a rollback is a normal install.

## Hosting requirements

- HTTPS with a certificate the Windows trust store accepts. There is no
  certificate pinning and no plain HTTP fallback.
- No authentication is supported in the manifest request; do not put secrets in
  the feed. If distribution must be private, host it behind a network policy or
  use a signed installer and a private mirror.
- Serve `latest.json` with `Content-Type: application/json` and a short cache
  lifetime; the app sends `Cache-Control: no-cache`.
- The manifest is authenticated only by HTTPS plus the SHA-256 of the installer.
  **Code signing is strongly recommended** (see `docs/deployment.md`); the hash
  protects integrity in transit, not the publisher's identity.

## Command line

Check the configured feed without opening a window:

```powershell
.\NexusRemote.exe --check-updates
```

The windowed build has no console, so use the source environment for readable
output:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m nexus_remote.main --check-updates
```

Exit status is `0` for available/current/not-configured and `1` for a malformed
feed or a network/HTTP failure.

## Threat notes

| Risk | Mitigation |
| --- | --- |
| Malicious manifest | HTTPS only, strict schema, bounded read. |
| Tampered installer | Mandatory SHA-256 and size check; partial file removed. |
| Drive-by install | Download and launch require explicit confirmation; auto-check only shows a status note. |
| Compromised host | No update path can defend against an attacker who already controls the workstation; code signing and OS controls still apply. |
| Downgrade | The app only offers newer numeric versions; older setups can be installed manually. Database migrations remain forward-only. |

The update subsystem does not use the credential vault, does not send device
inventory, and does not reuse session cookies. It is separate from the vendor
command-catalog feed described in `docs/catalog-feed.md`.
