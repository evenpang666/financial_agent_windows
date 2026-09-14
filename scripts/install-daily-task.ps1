[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $projectRoot 'scripts\run-scheduled-daily-push.ps1'
$taskName = 'DSH A-Share Pre-open Research'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$webServer = Join-Path $projectRoot 'scripts\report_web_server.py'
$webTaskName = 'DSH A-Share Report Site'
$powerShellExe = Join-Path $PSHOME 'powershell.exe'
$arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`""
$action = New-ScheduledTaskAction -Execute $powerShellExe -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '09:20'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Generate and push the A-share research brief at 09:20, before the 09:30 market open.' -Force | Out-Null
Write-Host "Installed Windows scheduled task: $taskName (daily 09:20; non-trading days are skipped)."

$webAction = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$webServer`"" -WorkingDirectory $projectRoot
$webTrigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$webSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $webTaskName -Action $webAction -Trigger $webTrigger -Settings $webSettings -Principal $principal -Description 'Serve archived A-share reports to this computer and its local network.' -Force | Out-Null
Write-Host "Installed Windows scheduled task: $webTaskName (starts at sign-in)."
