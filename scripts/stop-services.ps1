[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataServerScript = Join-Path $projectRoot 'scripts\stock_data_server.py'
$reportWebScript = Join-Path $projectRoot 'scripts\report_web_server.py'
$pushScript = Join-Path $projectRoot 'scripts\daily_push.py'
$stoppedProcessIds = [System.Collections.Generic.HashSet[int]]::new()

function Get-ProcessCommandLine([int]$ServiceProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ServiceProcessId" -ErrorAction SilentlyContinue
    return [string]$process.CommandLine
}

function Stop-ProjectListener([int]$Port, [string]$Label, [scriptblock]$MatchesProject) {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    if (-not $listeners) {
        Write-Host "$Label is not running on port $Port."
        return
    }
    foreach ($serviceProcessId in $listeners) {
        if ($stoppedProcessIds.Contains($serviceProcessId)) {
            continue
        }
        $commandLine = Get-ProcessCommandLine $serviceProcessId
        if (-not (& $MatchesProject $commandLine)) {
            Write-Warning "Port $Port is owned by process $serviceProcessId, which does not appear to be this project's $Label. It was left running."
            continue
        }
        Stop-Process -Id $serviceProcessId -Force -ErrorAction Stop
        [void]$stoppedProcessIds.Add($serviceProcessId)
        Write-Host "Stopped $Label (process $serviceProcessId, port $Port)."
    }
}

Stop-ProjectListener 3080 'DSH Web' {
    param($commandLine)
    $commandLine -match '(?i)@deepseek-ai[\\/]dsh|\\bdsh(?:\.cmd|\.js)?\\b'
}
Stop-ProjectListener 8765 'local stock-data service' {
    param($commandLine)
    $commandLine -like "*$dataServerScript*"
}
Stop-ProjectListener 8766 'local report website' {
    param($commandLine)
    $commandLine -like "*$reportWebScript*"
}

$pushProcesses = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$pushScript*" }
foreach ($pushProcess in $pushProcesses) {
    $serviceProcessId = [int]$pushProcess.ProcessId
    if ($stoppedProcessIds.Contains($serviceProcessId)) {
        continue
    }
    Stop-Process -Id $serviceProcessId -Force -ErrorAction Stop
    [void]$stoppedProcessIds.Add($serviceProcessId)
    Write-Host "Stopped background daily scheduler (process $serviceProcessId)."
}

Write-Host 'Current project services have been stopped. Windows scheduled tasks and local data files were left unchanged.'
