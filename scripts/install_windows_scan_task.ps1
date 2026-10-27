# Optional logged-in Windows background scan task. Does not require UI to run.
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Remove,
    [string]$Python = "",
    [string]$ProjectRoot = "",
    [int]$IntervalMinutes = 5,
    [string]$TaskName = "KeibaAI-PreRace-Scan"
)
$ErrorActionPreference = "Stop"
if ($Install -and $Remove) { throw "Choose -Install or -Remove." }
if ($IntervalMinutes -lt 1 -or $IntervalMinutes -gt 60) { throw "IntervalMinutes must be 1..60." }
if (-not $ProjectRoot) { $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
if (-not (Test-Path (Join-Path $ProjectRoot "scripts\run_background_scan.py"))) {
    throw "Missing scan runner."
}
if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed $TaskName"
    exit 0
}
if (-not $Python) {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $command) { throw "python.exe not found. Install Python 3 or pass -Python." }
    $Python = $command.Source
}
$Python = (Resolve-Path $Python).Path
$arguments = "-m scripts.run_background_scan --manifest data/upcoming_races.json --db data/odds_history.db --out data/last_background_scan.json"
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if (-not $Install) {
    Write-Host "DRY RUN; no task installed."
    Write-Host "User: $user"
    Write-Host "Command: $Python $arguments"
    Write-Host "Working directory: $ProjectRoot"
    Write-Host "Interval: $IntervalMinutes minutes"
    Write-Host "Use -Install to register. No automatic real odds until a provider is connected."
    exit 0
}
$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Installed $TaskName; background scan runs only while the user is logged in."
