[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pluginPath = Join-Path $projectRoot 'dsh-finance-agent'
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$requirements = Join-Path $projectRoot 'requirements.txt'
$pushConfig = Join-Path $projectRoot 'config\daily-push.json'
$pushConfigExample = Join-Path $projectRoot 'config\daily-push.example.json'

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
    $candidate = Join-Path (& npm prefix -g) 'dsh.cmd'
    if (Test-Path -LiteralPath $candidate) {
        return $candidate
    }
    return $null
}

Require-Command node 'Node.js was not found. Install Node.js, then reopen this control panel.'
Require-Command npm 'npm was not found. Reinstall Node.js, then reopen this control panel.'
Require-Command python 'Python was not found. Install Python 3.11 or later, then reopen this control panel.'

$pythonVersionOk = & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.11 or later is required.'
}

Set-Location $projectRoot
$missingGlobalPackages = @()
if (-not (Find-DshCommand)) {
    $missingGlobalPackages += '@deepseek-ai/dsh'
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    $missingGlobalPackages += 'pnpm'
}
if ($missingGlobalPackages.Count -gt 0) {
    Write-Host "Installing missing global packages: $($missingGlobalPackages -join ', ')..."
    & npm install -g @missingGlobalPackages
    if ($LASTEXITCODE -ne 0) {
        throw "npm dependency installation failed with exit code $LASTEXITCODE."
    }
} else {
    Write-Host 'dsh and pnpm are already installed; leaving them unchanged.'
}

$dshCommand = Find-DshCommand
if (-not $dshCommand -or -not (Test-Path -LiteralPath $dshCommand)) {
    throw 'The dsh executable was not found after installation.'
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host 'Creating the Python virtual environment...'
    & python -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) {
        throw "Python virtual-environment creation failed with exit code $LASTEXITCODE."
    }
}

Write-Host 'Installing or updating Python dependencies...'
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip update failed with exit code $LASTEXITCODE."
}
& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Python dependency installation failed with exit code $LASTEXITCODE."
}

$profileRoot = Join-Path $env:USERPROFILE '.dsh\profiles\web'
$manifestPath = Join-Path $profileRoot 'package.json'
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
    Write-Host 'Removing the previous local plugin registration...'
    & $dshCommand plugin --profile web remove dsh-finance-agent
    if ($LASTEXITCODE -ne 0) {
        throw "The previous plugin registration could not be removed (exit code $LASTEXITCODE)."
    }
}

Write-Host 'Registering and installing the current finance plugin...'
& $dshCommand plugin --profile web add $pluginPath
if ($LASTEXITCODE -ne 0) {
    throw "Plugin registration failed with exit code $LASTEXITCODE."
}
& $dshCommand plugin --profile web install
if ($LASTEXITCODE -ne 0) {
    throw "Plugin installation failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path -LiteralPath $pushConfig)) {
    Copy-Item -LiteralPath $pushConfigExample -Destination $pushConfig
    Write-Host 'Created the local daily-report configuration.'
}

Write-Host 'Installation completed. All services remain stopped.'
