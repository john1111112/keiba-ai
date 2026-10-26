# One-time registration: unattended race monitoring independent of the UI.
# Registration survives reboots; no SDK subscription or trial is activated.
param(
    [ValidateSet("register", "unregister", "status", "run-now")]
    [string]$Action = "register",
    [ValidateRange(1, 60)]
    [int]$IntervalMinutes = 1
)

$ErrorActionPreference = "Stop"
$TaskName = "KeibaAI_BackgroundMonitor"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunnerBat = Join-Path $PSScriptRoot "run_scheduled_task.bat"

if ($Action -eq "register") {
    if (-not (Test-Path -LiteralPath $RunnerBat)) {
        throw "Missing scheduled runner: $RunnerBat"
    }
    # A per-minute task survives Windows restarts and requires no Streamlit.
    & schtasks.exe /Create /TN $TaskName /TR ('"' + $RunnerBat + '"') /SC MINUTE /MO $IntervalMinutes /F | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Task registration failed (exit code $LASTEXITCODE)"
    }
    try {
        $setting = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Set-ScheduledTask -TaskName $TaskName -Settings $setting | Out-Null
    } catch {
        Write-Warning "Could not configure IgnoreNew concurrency: $_"
    }
    Write-Host "[OK] Auto-monitor registered every $IntervalMinutes minute(s)."
    & schtasks.exe /Run /TN $TaskName | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Registered but immediate run failed; check task history."
    }
    exit 0
}
if ($Action -eq "unregister") {
    & schtasks.exe /Delete /TN $TaskName /F | Out-Host
    exit $LASTEXITCODE
}
if ($Action -eq "run-now") {
    & schtasks.exe /Run /TN $TaskName | Out-Host
    exit $LASTEXITCODE
}
if ($Action -eq "status") {
    & schtasks.exe /Query /TN $TaskName /V /FO LIST | Out-Host
    exit $LASTEXITCODE
}
