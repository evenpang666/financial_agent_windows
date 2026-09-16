[CmdletBinding()]
param(
    [ValidateSet('EnableAgent', 'DisableAgent', 'EnableDshWeb', 'DisableDshWeb', 'Status')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dataServer = Join-Path $projectRoot 'scripts\stock_data_server.py'
$reportServer = Join-Path $projectRoot 'scripts\report_web_server.py'
$pushScript = Join-Path $projectRoot 'scripts\daily_push.py'
$pushConfig = Join-Path $projectRoot 'config\daily-push.json'
$pushConfigExample = Join-Path $projectRoot 'config\daily-push.example.json'
$taskInstaller = Join-Path $projectRoot 'scripts\install-daily-task.ps1'
$dailyTask = 'DSH A-Share Pre-open Research'
$reportTask = 'DSH A-Share Report Site'

function Test-PortListening([int]$Port) {
    return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Start-ProjectPythonService([string]$Script, [int]$Port, [string]$Label) {
    if (Test-PortListening $Port) {
        return
    }
    Start-Process -FilePath $pythonExe -ArgumentList @($Script) -WorkingDirectory $projectRoot -WindowStyle Hidden
    foreach ($attempt in 1..15) {
        if (Test-PortListening $Port) {
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "$Label did not start on port $Port."
}

function Stop-ProjectPort([int]$Port, [string]$ExpectedCommand, [string]$Label) {
    $processIds = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($processId in $processIds) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        if ($process -and $process.CommandLine -like "*$ExpectedCommand*") {
            Stop-Process -Id $processId -Force -ErrorAction Stop
            Write-Host "Stopped $Label."
        }
    }
}

function Set-TaskEnabled([string]$TaskName, [bool]$Enabled) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        return
    }
    if ($Enabled) {
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
    } else {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
    }
}

function Get-TaskState([string]$TaskName) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        return 'Missing'
    }
    return [string]$task.State
}

switch ($Action) {
    'EnableAgent' {
        if (-not (Test-Path -LiteralPath $pythonExe)) {
            throw "Python environment not found: $pythonExe. Run install-and-start.ps1 once first."
        }
        if (-not (Test-Path -LiteralPath $pushConfig)) {
            Copy-Item -LiteralPath $pushConfigExample -Destination $pushConfig
        }
        & $taskInstaller
        Set-TaskEnabled $dailyTask $true
        Set-TaskEnabled $reportTask $true
        Start-ProjectPythonService $dataServer 8765 'Local stock-data service'
        Start-ProjectPythonService $reportServer 8766 'Report website'
        Write-Host 'Agent enabled: daily report tasks are active. DSH Web was not started.'
    }
    'DisableAgent' {
        Set-TaskEnabled $dailyTask $false
        Set-TaskEnabled $reportTask $false
        Stop-ProjectPort 8766 $reportServer 'report website'
        $pushProcesses = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$pushScript*" }
        foreach ($process in $pushProcesses) {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        }
        if (-not (Test-PortListening 3080)) {
            Stop-ProjectPort 8765 $dataServer 'local stock-data service'
            Write-Host 'Agent disabled: daily report tasks and local agent services have stopped.'
        } else {
            Write-Host 'Agent disabled: daily report tasks have stopped. Data service remains available for DSH Web.'
        }
    }
    'EnableDshWeb' {
        if (-not (Test-Path -LiteralPath $pythonExe)) {
            throw "Python environment not found: $pythonExe. Run install-and-start.ps1 once first."
        }
        Start-ProjectPythonService $dataServer 8765 'Local stock-data service'
        if (Test-PortListening 3080) {
            Write-Host 'DSH Web is already running.'
            break
        }
        $dsh = Get-Command dsh -ErrorAction SilentlyContinue
        if (-not $dsh) {
            throw 'dsh command was not found. Run install-and-start.ps1 once first.'
        }
        Start-Process -FilePath $dsh.Source -ArgumentList @('web') -WorkingDirectory $projectRoot
        Write-Host 'Starting DSH Web...'
    }
    'DisableDshWeb' {
        $processIds = Get-NetTCPConnection -LocalPort 3080 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($processId in $processIds) {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
            if ($process -and $process.CommandLine -match '(?i)@deepseek-ai[\\/]dsh|\bdsh(?:\.cmd|\.js)?\b') {
                Stop-Process -Id $processId -Force -ErrorAction Stop
                Write-Host 'DSH Web stopped.'
            }
        }
        if ((Get-TaskState $dailyTask) -notin @('Ready', 'Running')) {
            Stop-ProjectPort 8765 $dataServer 'local stock-data service'
        }
    }
    'Status' {
        [pscustomobject]@{
            agent_task = Get-TaskState $dailyTask
            report_task = Get-TaskState $reportTask
            data_service = Test-PortListening 8765
            report_site = Test-PortListening 8766
            dsh_web = Test-PortListening 3080
        } | ConvertTo-Json -Compress
    }
}
