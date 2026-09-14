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
$reportWebScript = Join-Path $projectRoot 'scripts\report_web_server.py'

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

function Get-NormalizedPath([string]$Path, [string]$BasePath = '') {
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }
    $candidate = if ([System.IO.Path]::IsPathRooted($Path)) {
        $Path
    } elseif ($BasePath) {
        Join-Path $BasePath $Path
    } else {
        $Path
    }
    try {
        return (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path.TrimEnd('\\')
    } catch {
        return [System.IO.Path]::GetFullPath($candidate).TrimEnd('\\')
    }
}

function Test-LocalPluginRegistration([string]$ProfileRoot, [string]$ExpectedPluginPath) {
    $manifestPath = Join-Path $ProfileRoot 'package.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        return $false
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $spec = [string]$manifest.dependencies.'dsh-finance-agent'
        if (-not $spec.StartsWith('link:')) {
            return $false
        }
        $registeredPath = Get-NormalizedPath $spec.Substring(5) $ProfileRoot
        $expectedPath = Get-NormalizedPath $ExpectedPluginPath
        return $registeredPath -and $expectedPath -and $registeredPath.Equals($expectedPath, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
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
$profileRoot = Split-Path -Parent $profileNodeModules
$localPluginInstalled = Test-LocalPluginRegistration $profileRoot $pluginPath
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
    Write-Host 'Registering the local dsh-finance-agent plugin for this project path...'
    $oldManifestPath = Join-Path $profileRoot 'package.json'
    $oldPluginRegistered = $false
    if (Test-Path -LiteralPath $oldManifestPath) {
        try {
            $oldManifest = Get-Content -LiteralPath $oldManifestPath -Raw | ConvertFrom-Json
            $oldPluginRegistered = $null -ne $oldManifest.dependencies.'dsh-finance-agent'
        } catch {
            $oldPluginRegistered = $true
        }
    }
    if ($oldPluginRegistered) {
        Write-Host 'Removing stale dsh-finance-agent registration...'
        & $dshCommand plugin --profile web remove dsh-finance-agent
        if ($LASTEXITCODE -ne 0) {
            throw "Could not remove the stale dsh-finance-agent registration (exit code $LASTEXITCODE)."
        }
    }
    & $dshCommand plugin --profile web add $pluginPath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not register dsh-finance-agent for the current project path (exit code $LASTEXITCODE)."
    }
    & $dshCommand plugin --profile web install
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install the current dsh-finance-agent registration (exit code $LASTEXITCODE)."
    }
    if (-not (Test-LocalPluginRegistration $profileRoot $pluginPath)) {
        throw 'dsh-finance-agent registration still does not point to this project after installation.'
    }
    $localPluginInstalled = $true
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

$reportSiteRunning = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
if (-not $reportSiteRunning) {
    Write-Host 'Starting the local-network report website...'
    Start-Process -FilePath $venvPython -ArgumentList @($reportWebScript) -WindowStyle Hidden
    Start-Sleep -Seconds 1
}

Write-Host 'Report website (this computer): http://127.0.0.1:8766'
$lanAddresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -ExpandProperty IPAddress -Unique
foreach ($address in $lanAddresses) {
    Write-Host "Report website (LAN): http://${address}:8766"
}

${dshWebRunning} = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 3080 -State Listen -ErrorAction SilentlyContinue
if ($dshWebRunning) {
    Write-Host 'DSH Web is already running on port 3080; reusing the existing session.'
    Write-Host 'Use the browser tab that DSH Web opened previously. Close that session first if you need to start a new one.'
} else {
    Write-Host 'Opening dsh web...'
    & $dshCommand web
    if ($LASTEXITCODE -ne 0) {
        throw "dsh web exited with code $LASTEXITCODE."
    }
}
