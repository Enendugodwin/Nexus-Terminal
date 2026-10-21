# Installing Nexus Remote

Three supported ways to get a working copy. Pick one; they all use the same data
directory, so you can switch between them without losing anything.

| Route | Needs admin | Needs Python | Best for |
|---|---|---|---|
| [Windows installer](#1-windows-installer) | No | No | Most people |
| [Portable ZIP](#2-portable-zip) | No | No | USB sticks, locked-down PCs, no uninstall entry |
| [From source](#3-from-source) | No | Yes (3.11+) | Development, auditing the code, contributing |

Everything on this page is offline-first: nothing is contacted until you add a
device or configure an update/AI provider.

---

## 1. Windows installer

**File:** `NexusRemoteSetup-0.3.0.exe`

1. Run the installer.
2. Start **Nexus Remote** from the Start menu. A desktop shortcut is optional.
3. Verify the download first if you like:

   ```powershell
   Get-FileHash .\NexusRemoteSetup-0.3.0.exe -Algorithm SHA256
   ```

   The value must match `SHA256SUMS-0.3.0.txt`.

The installer is per user: it writes to `%LOCALAPPDATA%\Programs\Nexus Remote`,
needs no administrator rights and does not touch the machine-wide PATH.

**Silent install (IT / scripted):**

```powershell
.\NexusRemoteSetup-0.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

**Uninstall:** Settings → Apps → Installed apps → *Nexus Remote* → Uninstall, or
run `unins000.exe` in the install folder. Your data in
`%LOCALAPPDATA%\NexusRemote` is kept on purpose.

The installer is not code-signed, so SmartScreen may say *Windows protected your
PC*. Check the SHA-256, then choose **More info → Run anyway**.

---

## 2. Portable ZIP

**File:** `NexusRemote-0.3.0-portable.zip`

```powershell
Expand-Archive .\NexusRemote-0.3.0-portable.zip -DestinationPath C:\Tools
C:\Tools\NexusRemote\NexusRemote.exe
```

No installer, no registry entries, no admin rights: delete the folder to remove
it. The bundle already contains Python and every dependency.

- Keep the whole `NexusRemote` folder together; `NexusRemote.exe` needs
  `_internal\` beside it.
- Everything is self-contained except the optional FreeRDP client
  (`packaging\install_freerdp.ps1` fetches it into your user tools folder).
- User data still goes to `%LOCALAPPDATA%\NexusRemote` unless you pass
  `--data-dir` to keep it beside the executable.

---

## 3. From source

Requires Windows 10/11 with Python 3.11 or newer (`py --version`). No admin
rights are needed.

```powershell
git clone https://github.com/Enendugodwin/Nexus-Terminal.git
cd Nexus-Terminal
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m nexus_remote.main
```

`-e` means editable: edits under `src\` take effect on the next launch. The
`nexus-remote` command on PATH does the same thing once the venv is active:

```powershell
.\.venv\Scripts\Activate.ps1
nexus-remote
```

**Without installing the package**, the checkout launcher puts `src\` on the
path for you:

```powershell
.\.venv\Scripts\python.exe app.py
```

**Check the environment without opening a window:**

```powershell
.\.venv\Scripts\python.exe -m nexus_remote.main --selftest
```

**Optional extras:**

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[smb]"   # SMB file browser
.\.venv\Scripts\python.exe -m pip install -e ".[build]" # PyInstaller, for your own bundle
```

### Build your own installer

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build.ps1 -Installer
```

Produces `dist\NexusRemote\` (portable folder) and, with Inno Setup 6 installed,
`dist\NexusRemoteSetup-0.3.0.exe`.

---

## Where your data lives

| What | Default location |
|---|---|
| Database, logs, exports, known hosts | `%LOCALAPPDATA%\NexusRemote` |
| Credentials | Windows Credential Manager (references only in the database) |
| Settings | `%LOCALAPPDATA%\NexusRemote\nexus.db` |

Override the data directory with `--data-dir <folder>` or the
`NEXUS_REMOTE_HOME` environment variable. Uninstalling never deletes it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| SmartScreen blocks the installer | Verify the SHA-256, then *More info → Run anyway*. Signing is the only full fix. |
| `python` not found | Install Python 3.11+ from python.org and tick *Add python.exe to PATH*. |
| PowerShell page says the PTY is unavailable | `pip install pywinpty` in the same environment (Windows only). Remote SSH/SFTP still work without it. |
| The app starts but AI is "not ready" | Settings → Optional AI: choose a provider and save a key. AI is opt-in and unconfigured is a normal state. |
| Nothing works offline | Expected: inventory, command memory, rules, playbooks and terminals are local; only AI and update checks need a network, and both are optional. |
| Port already in use / stale window | Close every `Nexus Remote` process, then relaunch. User data is safe. |

Logs: `%LOCALAPPDATA%\NexusRemote\logs\nexus-remote.log`.
