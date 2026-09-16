[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$venvPythonw = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$controlPanel = Join-Path $PSScriptRoot 'control_panel.py'
$componentInstaller = Join-Path $PSScriptRoot 'install-components.ps1'
$pluginPath = Join-Path $projectRoot 'dsh-finance-agent'

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
        return (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path.TrimEnd('\')
    } catch {
        return [System.IO.Path]::GetFullPath($candidate).TrimEnd('\')
    }
}

function Test-PluginRegistration([string]$ExpectedPluginPath) {
    $profileRoot = Join-Path $env:USERPROFILE '.dsh\profiles\web'
    $manifestPath = Join-Path $profileRoot 'package.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        return $false
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $spec = [string]$manifest.dependencies.'dsh-finance-agent'
        if (-not $spec.StartsWith('link:')) {
            return $false
        }
        $registeredPath = Get-NormalizedPath $spec.Substring(5) $profileRoot
        $expectedPath = Get-NormalizedPath $ExpectedPluginPath
        return $registeredPath -and $expectedPath -and $registeredPath.Equals($expectedPath, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

Set-Location $projectRoot
Write-Host '[Bootstrap 1/6] Checking Node.js, npm, and Python...'
Require-Command node 'Node.js was not found. Install Node.js, then run control_panel.cmd again.'
Require-Command npm 'npm was not found. Reinstall Node.js, then run control_panel.cmd again.'
Require-Command python 'Python was not found. Install Python 3.11 or later, then run control_panel.cmd again.'
& python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.11 or later is required.'
}

Write-Host '[Bootstrap 2/6] Checking dsh and pnpm...'
$dshReady = $null -ne (Find-DshCommand)
$pnpmReady = $null -ne (Get-Command pnpm -ErrorAction SilentlyContinue)
Write-Host "  dsh: $(if ($dshReady) { 'installed' } else { 'missing' })"
Write-Host "  pnpm: $(if ($pnpmReady) { 'installed' } else { 'missing' })"

Write-Host '[Bootstrap 3/6] Checking the Python virtual environment...'
$venvReady = (Test-Path -LiteralPath $venvPython) -and (Test-Path -LiteralPath $venvPythonw)
Write-Host "  .venv: $(if ($venvReady) { 'ready' } else { 'missing or incomplete' })"

Write-Host '[Bootstrap 4/6] Checking Python dependencies...'
$dependenciesReady = $false
if ($venvReady) {
    & $venvPython -c "import akshare, numpy, pandas" 2>$null
    $dependenciesReady = $LASTEXITCODE -eq 0
}
Write-Host "  Python packages: $(if ($dependenciesReady) { 'ready' } else { 'missing or incomplete' })"

Write-Host '[Bootstrap 5/6] Checking the local DSH plugin...'
$pluginReady = $dshReady -and (Test-PluginRegistration $pluginPath)
Write-Host "  dsh-finance-agent: $(if ($pluginReady) { 'registered' } else { 'missing or stale' })"

if (-not ($dshReady -and $pnpmReady -and $venvReady -and $dependenciesReady -and $pluginReady)) {
    Write-Host 'Missing or incomplete components were found. Starting installation/repair...'
    & $componentInstaller
} else {
    Write-Host 'All required components are ready; no installation is needed.'
}

if (-not (Test-Path -LiteralPath $venvPythonw)) {
    throw "The virtual-environment GUI interpreter was not created: $venvPythonw"
}

Write-Host '[Bootstrap 6/6] Starting the control panel with the virtual environment...'
Start-Process -FilePath $venvPythonw -ArgumentList @("`"$controlPanel`"") -WorkingDirectory $projectRoot
Write-Host 'Control panel started successfully.'
