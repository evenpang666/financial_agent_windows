[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$serverScript = Join-Path $projectRoot 'scripts\stock_data_server.py'
$reportWebScript = Join-Path $projectRoot 'scripts\report_web_server.py'
$pushScript = Join-Path $projectRoot 'scripts\daily_push.py'
$pushConfig = Join-Path $projectRoot 'config\daily-push.json'
$startedServer = $null

if (-not (Test-Path $pythonExe)) {
    throw "Python virtual environment not found: $pythonExe"
}

try {
    $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) {
        $startedServer = Start-Process -FilePath $pythonExe -ArgumentList @($serverScript) -WindowStyle Hidden -PassThru
        $ready = $false
        foreach ($attempt in 1..30) {
            try {
                $health = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2
                if ($health.status -eq 'ok') {
                    $ready = $true
                    break
                }
            } catch {
                Start-Sleep -Seconds 1
            }
        }
        if (-not $ready) {
            throw 'Stock data service did not become ready within 30 seconds.'
        }
    }
    $reportListener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
    if (-not $reportListener) {
        Start-Process -FilePath $pythonExe -ArgumentList @($reportWebScript) -WindowStyle Hidden
        $reportReady = $false
        foreach ($attempt in 1..15) {
            try {
                $reportHealth = Invoke-RestMethod 'http://127.0.0.1:8766/api/health' -TimeoutSec 2
                if ($reportHealth.status -eq 'ok') {
                    $reportReady = $true
                    break
                }
            } catch {
                Start-Sleep -Seconds 1
            }
        }
        if (-not $reportReady) {
            throw 'Report website did not become ready within 15 seconds.'
        }
    }
    & $pythonExe $pushScript --config $pushConfig --once
    if ($LASTEXITCODE -ne 0) {
        throw "Daily push returned exit code $LASTEXITCODE."
    }
} finally {
    if ($startedServer -and -not $startedServer.HasExited) {
        Stop-Process -Id $startedServer.Id -Force -ErrorAction SilentlyContinue
    }
}
