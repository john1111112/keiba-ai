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
$QuietRunner = Join-Path $PSScriptRoot "run_scheduled_task_hidden.vbs"

if ($Action -eq "register") {
    if (-not (Test-Path -LiteralPath $RunnerBat)) {
        throw "Missing scheduled runner: $RunnerBat"
    }
    if (-not (Test-Path -LiteralPath $QuietRunner)) {
        throw "Missing hidden task launcher: $QuietRunner"
    }
    # Launch via wscript.exe so Task Scheduler does not flash cmd.exe every minute.
    # This preserves the original batch runner, exit code and 1-minute cadence.
    $taskCommand = 'wscript.exe "' + $QuietRunner + '"'
    & schtasks.exe /Create /TN $TaskName /TR $taskCommand /SC MINUTE /MO $IntervalMinutes /F | Out-Host
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
