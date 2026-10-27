param(
    [string]$TaskName = "KEIBA-AI-Authorized-Race-Feed",
    [int]$IntervalMinutes = 5
)
$ErrorActionPreference = "Stop"
if ($IntervalMinutes -lt 1 -or $IntervalMinutes -gt 60) {
    throw "IntervalMinutes must be between 1 and 60"
}
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = (Get-Command python -ErrorAction Stop).Source
$script = Join-Path $root "scripts\fetch_authorized_races.py"
if (-not (Test-Path $script)) { throw "Feed script missing" }
if (-not $env:KEIBA_FEED_URL -or -not $env:KEIBA_FEED_TOKEN) {
    throw "Configure KEIBA_FEED_URL and KEIBA_FEED_TOKEN first"
}
# schtasks stores only the executable and script path, not API credentials.
# Task must run under an account that has both variables configured.
$action = New-ScheduledTaskAction -Execute $python -Argument ('"' + $script + '" --out-dir "' + (Join-Path $root "data\authorized_feed") + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Authorized KEIBA race schedule, runners and odds collection" -Force | Out-Null
Write-Host "Task registered: $TaskName (every $IntervalMinutes minutes)"
