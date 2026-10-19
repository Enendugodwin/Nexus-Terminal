<#
.SYNOPSIS
    Install the FreeRDP Windows client for Nexus Remote.

.DESCRIPTION
    FreeRDP publishes no stable release binary for Windows; the client is only
    available as source releases or as nightly builds. This script fetches the
    latest successful win64 nightly from the official FreeRDP CI and places it in
    the per-user directory that Nexus Remote scans first:

        %LOCALAPPDATA%\NexusRemote\tools\freerdp

    No administrator rights are required and nothing is added to PATH.

.PARAMETER Destination
    Override the install directory (defaults to the per-user tools directory).

.PARAMETER Force
    Re-download even if a client is already present.
#>
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA "NexusRemote\tools\freerdp"),
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$clientNames = @("wlfreerdp.exe", "sdl-freerdp.exe", "xfreerdp.exe")

if (-not $Force) {
    foreach ($name in $clientNames) {
        $existing = Join-Path $Destination $name
        if (Test-Path $existing) {
            Write-Host "FreeRDP already installed: $existing"
            exit 0
        }
    }
}

$api = "https://ci.freerdp.com/job/freerdp-nightly-windows/lastSuccessfulBuild/api/json" +
       "?tree=number,runs[number,result,url,artifacts[fileName,relativePath]]"
Write-Host "Querying the FreeRDP CI for the latest win64 build..."
$build = Invoke-RestMethod -Uri $api -UseBasicParsing -Headers @{ "User-Agent" = "nexus-remote" }

$run = $build.runs |
    Where-Object { $_.result -eq "SUCCESS" -and $_.url -match "label=vs2017" -and $_.url -match "arch=win64" } |
    Sort-Object number -Descending |
    Select-Object -First 1

if (-not $run) {
    throw "Could not find a successful win64 FreeRDP nightly in build $($build.number)."
}

$artifact = $run.artifacts | Where-Object { $_.fileName -match "freerdp\.exe$" } | Select-Object -First 1
if (-not $artifact) {
    throw "The nightly build exposes no client executable in $($run.url)."
}

$base = $run.url.TrimEnd("/")
$url = "$base/artifact/$($artifact.relativePath)"
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$target = Join-Path $Destination $artifact.fileName

Write-Host "Downloading $($artifact.fileName) from build $($run.number)..."
Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing -Headers @{ "User-Agent" = "nexus-remote" }

$bytes = [System.IO.File]::ReadAllBytes($target)
if ($bytes.Length -lt 1024 -or $bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) {
    Remove-Item $target -Force
    throw "The downloaded file is not a Windows executable; aborting."
}

Write-Host "Installed $target ($([math]::Round($bytes.Length / 1MB, 1)) MB)."
Write-Host "Restart Nexus Remote to pick it up."
