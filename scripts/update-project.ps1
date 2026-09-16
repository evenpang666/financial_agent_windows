[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stopScript = Join-Path $PSScriptRoot 'stop-services.ps1'
$installer = Join-Path $PSScriptRoot 'install-components.ps1'
$taskNames = @('DSH A-Share Pre-open Research', 'DSH A-Share Report Site')

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
Write-Host '正在停止服务和日报任务...'
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

Write-Host '正在重新安装插件和项目依赖...'
& $installer
Write-Host '更新完成。所有服务与计划任务均保持关闭。'
