[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stopScript = Join-Path $PSScriptRoot 'stop-services.ps1'
$startScript = Join-Path $projectRoot 'install-and-start.ps1'
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$requirements = Join-Path $projectRoot 'requirements.txt'

function Require-Command([string]$Name, [string]$Message) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw $Message
    }
}

Require-Command git 'Git is not available in PATH. Install Git for Windows, then run this updater again.'
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.git'))) {
    throw "This folder is not a Git checkout: $projectRoot"
}

Set-Location $projectRoot
Write-Host 'Checking for project updates...'
& git pull --ff-only
if ($LASTEXITCODE -ne 0) {
    throw 'git pull failed. Local tracked changes were preserved; resolve the message above and try again.'
}

Write-Host 'Restarting project services with the updated code...'
& $stopScript

if (Test-Path -LiteralPath $venvPython) {
    Write-Host 'Refreshing Python dependencies...'
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency refresh failed with exit code $LASTEXITCODE."
    }
}

& $startScript
if ($LASTEXITCODE -ne 0) {
    throw "Project startup failed with exit code $LASTEXITCODE."
}
