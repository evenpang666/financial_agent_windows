# One-click setup for the local dsh-finance-agent project.
# Run from PowerShell: .\install-and-start.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pluginPath = Join-Path $projectRoot 'dsh-finance-agent'
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$serverScript = Join-Path $projectRoot 'scripts\stock_data_server.py'
$pushScript = Join-Path $projectRoot 'scripts\daily_push.py'
$pushConfig = Join-Path $projectRoot 'config\daily-push.json'
$pushConfigExample = Join-Path $projectRoot 'config\daily-push.example.json'
$taskInstaller = Join-Path $projectRoot 'scripts\install-daily-task.ps1'

function Require-Command([string]$Name, [string]$Message) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw $Message
    }
}

function Find-DshCommand {
    $command = Get-Command dsh -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $candidate = Join-Path (& npm prefix -g) 'dsh.cmd'
    if (Test-Path $candidate) {
        return $candidate
    }
    return $null
}

Require-Command node 'Node.js is not available in PATH. Close and reopen PowerShell after installing Node.js, then run this script again.'
Require-Command npm 'npm is not available in PATH. Close and reopen PowerShell after installing Node.js, then run this script again.'
Require-Command python 'Python 3.11 or later is required. Install Python and run this script again.'

Set-Location $projectRoot
$dshCommand = Find-DshCommand
if (-not $dshCommand) {
    Write-Host 'Installing dsh and pnpm...'
    npm install -g @deepseek-ai/dsh pnpm
    $dshCommand = Find-DshCommand
}
if (-not $dshCommand -or -not (Test-Path $dshCommand)) {
    throw 'dsh was installed but its executable could not be located.'
}

$profileNodeModules = Join-Path $env:USERPROFILE '.dsh\profiles\web\node_modules'
$localPluginInstalled = Test-Path (Join-Path $profileNodeModules 'dsh-finance-agent')
$venvReady = Test-Path $venvPython

if (-not (Test-Path $venvPython)) {
    Write-Host 'Creating Python virtual environment...'
    python -m venv .venv
}

if (-not $venvReady) {
    Write-Host 'Installing Python dependencies...'
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
}

if (-not $localPluginInstalled) {
    Write-Host 'Registering the local dsh-finance-agent plugin...'
    & $dshCommand plugin --profile web add $pluginPath
}

if ($dshCommand -and $venvReady -and $localPluginInstalled) {
    Write-Host 'All components are already installed; starting services...'
}

$dataServerRunning = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if (-not $dataServerRunning) {
    Write-Host 'Starting the local stock-data service...'
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
        "& '$venvPython' '$serverScript'"
    ) -WindowStyle Hidden
    Start-Sleep -Seconds 2
} else {
    Write-Host 'A service is already listening on port 8765; leaving it running.'
}

if (-not (Test-Path $pushConfig)) {
    Copy-Item -LiteralPath $pushConfigExample -Destination $pushConfig
    Write-Host "Created daily push config: $pushConfig"
}
try {
    & $taskInstaller
} catch {
    Write-Warning "Could not install the Windows scheduled task; starting a background scheduler for this login instead. $($_.Exception.Message)"
    $pushRunning = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*daily_push.py*' }
    if (-not $pushRunning) {
        Start-Process -FilePath $venvPython -ArgumentList @($pushScript, '--config', $pushConfig) -WindowStyle Hidden
    }
}

Write-Host 'Opening dsh web...'
& $dshCommand web
