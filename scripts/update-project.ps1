[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stopScript = Join-Path $PSScriptRoot 'stop-services.ps1'
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$requirements = Join-Path $projectRoot 'requirements.txt'
$pluginPath = Join-Path $projectRoot 'dsh-finance-agent'
$taskNames = @('DSH A-Share Pre-open Research', 'DSH A-Share Report Site')

function Require-Command([string]$Name, [string]$Message) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw $Message
    }
}

function Find-DshCommand {
    $command = Get-Command dsh.cmd -CommandType Application -ErrorAction SilentlyContinue
    if (-not $command) {
        $command = Get-Command dsh -ErrorAction SilentlyContinue
    }
    if ($command) {
        return $command.Source
    }
    return $null
}

Require-Command git 'Git is not available in PATH. Install Git for Windows, then run this updater again.'
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.git'))) {
    throw "This folder is not a Git checkout: $projectRoot"
}

Set-Location $projectRoot
Write-Host 'Stopping services and daily-report tasks...'
foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Disable-ScheduledTask -TaskName $taskName | Out-Null
    }
}
& $stopScript

Write-Host 'Checking for project updates...'
& git pull --ff-only
if ($LASTEXITCODE -ne 0) {
    throw 'git pull failed. Local tracked changes were preserved; resolve the message above and try again.'
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Require-Command python 'Python was not found. Install Python 3.11 or later, then run the updater again.'
    Write-Host 'Creating the Python virtual environment...'
    & python -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) {
        throw "Python virtual-environment creation failed with exit code $LASTEXITCODE."
    }
}

Write-Host 'Updating dependencies in the Python virtual environment...'
& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Python dependency update failed with exit code $LASTEXITCODE."
}
Write-Host 'Re-registering the local finance plugin without updating dsh...'
$dshCommand = Find-DshCommand
if (-not $dshCommand) {
    throw 'The dsh executable was not found. Use Install / Repair before updating.'
}
$manifestPath = Join-Path $env:USERPROFILE '.dsh\profiles\web\package.json'
$registered = $false
if (Test-Path -LiteralPath $manifestPath) {
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $registered = $null -ne $manifest.dependencies.'dsh-finance-agent'
    } catch {
        $registered = $true
    }
}
if ($registered) {
    & $dshCommand plugin --profile web remove dsh-finance-agent
    if ($LASTEXITCODE -ne 0) {
        throw "Previous plugin registration removal failed with exit code $LASTEXITCODE."
    }
}
& $dshCommand plugin --profile web add $pluginPath
if ($LASTEXITCODE -ne 0) {
    throw "Plugin registration failed with exit code $LASTEXITCODE."
}
& $dshCommand plugin --profile web install
if ($LASTEXITCODE -ne 0) {
    throw "Plugin installation failed with exit code $LASTEXITCODE."
}
Write-Host 'dsh and pnpm were left unchanged; the local plugin was re-registered.'
Write-Host 'Update completed. All services and scheduled tasks remain stopped.'
