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
    $command = Get-Command dsh -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $candidate = Join-Path (& npm prefix -g) 'dsh.cmd'
    if (Test-Path -LiteralPath $candidate) {
        return $candidate
    }
    return $null
}

Require-Command node '未检测到 Node.js。请先安装 Node.js，并重新打开本程序。'
Require-Command npm '未检测到 npm。请重新安装 Node.js，并重新打开本程序。'
Require-Command python '未检测到 Python。请先安装 Python 3.11 或更高版本，并重新打开本程序。'

$pythonVersionOk = & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw 'Python 版本过低，请安装 Python 3.11 或更高版本。'
}

Set-Location $projectRoot
Write-Host '正在安装或更新 dsh 与 pnpm...'
& npm install -g @deepseek-ai/dsh pnpm
if ($LASTEXITCODE -ne 0) {
    throw "npm 依赖安装失败（退出码 $LASTEXITCODE）。"
}

$dshCommand = Find-DshCommand
if (-not $dshCommand -or -not (Test-Path -LiteralPath $dshCommand)) {
    throw 'dsh 安装完成后仍无法找到其可执行文件。'
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host '正在创建 Python 虚拟环境...'
    & python -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) {
        throw "Python 虚拟环境创建失败（退出码 $LASTEXITCODE）。"
    }
}

Write-Host '正在安装或更新 Python 依赖...'
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip 更新失败（退出码 $LASTEXITCODE）。"
}
& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Python 依赖安装失败（退出码 $LASTEXITCODE）。"
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
    Write-Host '正在移除旧的本地插件注册...'
    & $dshCommand plugin --profile web remove dsh-finance-agent
    if ($LASTEXITCODE -ne 0) {
        throw "旧插件注册移除失败（退出码 $LASTEXITCODE）。"
    }
}

Write-Host '正在注册并安装最新版财务研究插件...'
& $dshCommand plugin --profile web add $pluginPath
if ($LASTEXITCODE -ne 0) {
    throw "插件注册失败（退出码 $LASTEXITCODE）。"
}
& $dshCommand plugin --profile web install
if ($LASTEXITCODE -ne 0) {
    throw "插件安装失败（退出码 $LASTEXITCODE）。"
}

if (-not (Test-Path -LiteralPath $pushConfig)) {
    Copy-Item -LiteralPath $pushConfigExample -Destination $pushConfig
    Write-Host '已创建本地日报配置。'
}

Write-Host '安装完成。所有服务保持关闭，可在控制台中按需启用。'
