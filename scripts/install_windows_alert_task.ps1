# Register a background alert-check task for the CURRENT logged-in user.
# This script does not run until the user explicitly invokes it with -Install.
# The app UI does not need to remain open, but the user must be logged in.
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Remove,
    [string]$Python = "python",
    [string]$ProjectRoot = "",
    [int]$IntervalMinutes = 5,
    [string]$TaskName = "KeibaAI-PreRace-Alerts",
    [switch]$EnableDelivery
)

$ErrorActionPreference = "Stop"
if ($Install -and $Remove) { throw "Choose -Install or -Remove, not both." }
if ($IntervalMinutes -lt 1 -or $IntervalMinutes -gt 60) {
    throw "IntervalMinutes must be between 1 and 60."
}
if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$scriptFile = Join-Path $ProjectRoot "scripts\run_unattended_alerts.py"
if (-not (Test-Path $scriptFile)) { throw "Missing unattended alert runner." }
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "Removed scheduled task: $TaskName"
    exit 0
}

$arguments = "-m scripts.run_unattended_alerts --candidates data/verified_alert_candidates.json --state-db data/alert_state.db"
if ($EnableDelivery) { $arguments += " --deliver" }
$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited

if (-not $Install) {
    Write-Host "DRY RUN: no Windows task was installed."
    Write-Host "Task: $TaskName"
    Write-Host "User: $taskUser (logged-on session required)"
    Write-Host "Directory: $ProjectRoot"
    Write-Host "Command: $Python $arguments"
    Write-Host "Interval: every $IntervalMinutes minutes"
    Write-Host "Run with -Install to register; add -EnableDelivery only after live sources are verified."
    exit 0
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Installed: $TaskName"
if (-not $EnableDelivery) {
    Write-Host "DRY RUN mode: notification delivery remains disabled."
}
