[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ruleName = 'DSH A-Share Report Site (Private LAN)'
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (-not $existing) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8766 -Profile Private -RemoteAddress LocalSubnet | Out-Null
    Write-Host 'Created private-LAN firewall rule for TCP port 8766.'
} else {
    Write-Host "Firewall rule already exists: $ruleName"
}
