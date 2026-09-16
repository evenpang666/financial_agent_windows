[CmdletBinding()]
param(
    [ValidateSet('EnableAgent', 'DisableAgent', 'EnableDshWeb', 'DisableDshWeb', 'Install', 'Update', 'Status')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dataServer = Join-Path $projectRoot 'scripts\stock_data_server.py'
$reportServer = Join-Path $projectRoot 'scripts\report_web_server.py'
$pushScript = Join-Path $projectRoot 'scripts\daily_push.py'
$pushConfig = Join-Path $projectRoot 'config\daily-push.json'
$pushConfigExample = Join-Path $projectRoot 'config\daily-push.example.json'
$taskInstaller = Join-Path $projectRoot 'scripts\install-daily-task.ps1'
$componentInstaller = Join-Path $projectRoot 'scripts\install-components.ps1'
$updater = Join-Path $projectRoot 'scripts\update-project.ps1'
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
        foreach ($attempt in 1..30) {
            if (Test-PortListening 3080) {
                Write-Host 'DSH Web 已启动。'
                break
            }
            Start-Sleep -Seconds 1
        }
        if (-not (Test-PortListening 3080)) {
            throw 'DSH Web 未能在 30 秒内启动。'
        }
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
    'Install' {
        Set-TaskEnabled $dailyTask $false
        Set-TaskEnabled $reportTask $false
        & (Join-Path $projectRoot 'scripts\stop-services.ps1')
        & $componentInstaller
    }
    'Update' {
        & $updater
    }
    'Status' {
        $manifestPath = Join-Path $env:USERPROFILE '.dsh\profiles\web\package.json'
        $pluginInstalled = $false
        if (Test-Path -LiteralPath $manifestPath) {
            try {
                $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
                $pluginInstalled = $null -ne $manifest.dependencies.'dsh-finance-agent'
            } catch {
                $pluginInstalled = $false
            }
        }
        [pscustomobject]@{
            agent_task = Get-TaskState $dailyTask
            report_task = Get-TaskState $reportTask
            data_service = Test-PortListening 8765
            report_site = Test-PortListening 8766
            dsh_web = Test-PortListening 3080
            node_available = $null -ne (Get-Command node -ErrorAction SilentlyContinue)
            python_available = $null -ne (Get-Command python -ErrorAction SilentlyContinue)
            git_available = $null -ne (Get-Command git -ErrorAction SilentlyContinue)
            git_checkout = Test-Path -LiteralPath (Join-Path $projectRoot '.git')
            venv_ready = Test-Path -LiteralPath $pythonExe
            dsh_available = $null -ne (Get-Command dsh -ErrorAction SilentlyContinue)
            plugin_installed = $pluginInstalled
        } | ConvertTo-Json -Compress
    }
}
