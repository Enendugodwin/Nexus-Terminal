# Build a Windows bundle for Nexus Remote.
#
#   .\packaging\build.ps1
#   .\packaging\build.ps1 -Python .\.venv\Scripts\python.exe
#   .\packaging\build.ps1 -Installer
#
# Output: dist\NexusRemote\NexusRemote.exe
# With -Installer and Inno Setup 6 installed, also
# dist\NexusRemoteSetup-<version>.exe.
param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [switch]$SkipTests,
    [switch]$Installer
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

if (-not (Test-Path $Python)) {
    throw "Python not found at $Python. Create the virtual environment first."
}

if (-not $SkipTests) {
    $env:PYTHONPATH = "src"
    & $Python -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; not building." }
    Remove-Item Env:\PYTHONPATH
}

& $Python -m pip install --upgrade "pyinstaller>=6.0"
if ($LASTEXITCODE -ne 0) { throw "Could not install PyInstaller." }

# Regenerate the icon from the SVG source so the bundle and installer agree.
if (Test-Path "packaging\icon.svg") {
    & $Python packaging\make_icon.py
    if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }
}

& $Python -m PyInstaller packaging\nexus_remote.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

Write-Host ""
Write-Host "Built: $root\dist\NexusRemote\NexusRemote.exe"
Write-Host "Smoke test the bundle by launching it, then check %LOCALAPPDATA%\NexusRemote\logs."

if ($Installer) {
    $iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno\ISCC.exe"
    if (-not (Test-Path $iscc)) {
        $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    }
    if (-not (Test-Path $iscc)) {
        throw "Inno Setup 6 not found. Install it, then rerun with -Installer."
    }
    & $iscc "packaging\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }
    Write-Host "Built installer under $root\dist."
}
