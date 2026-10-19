# Build a Windows bundle for Nexus Remote.
#
#   .\packaging\build.ps1
#   .\packaging\build.ps1 -Python .\.venv\Scripts\python.exe
#
# Output: dist\NexusRemote\NexusRemote.exe
param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [switch]$SkipTests
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

& $Python -m PyInstaller packaging\nexus_remote.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

Write-Host ""
Write-Host "Built: $root\dist\NexusRemote\NexusRemote.exe"
Write-Host "Smoke test the bundle by launching it, then check %LOCALAPPDATA%\NexusRemote\logs."
