# One-time non-disruptive migration for an already installed KEIBA AI task.
# Keeps the existing task account, triggers, frequency, and login settings.
# It changes only the task action to run the same batch invisibly through WSH.
$ErrorActionPreference = "Stop"
$TaskName = "KeibaAI_BackgroundMonitor"
$QuietRunner = Join-Path $PSScriptRoot "run_scheduled_task_hidden.vbs"
$RunnerBat = Join-Path $PSScriptRoot "run_scheduled_task.bat"
$WScriptExe = Join-Path $env:WINDIR "System32\wscript.exe"

if (-not (Test-Path -LiteralPath $QuietRunner)) { throw "Hidden launcher missing: $QuietRunner" }
if (-not (Test-Path -LiteralPath $RunnerBat)) { throw "Original monitor batch missing: $RunnerBat" }
if (-not (Test-Path -LiteralPath $WScriptExe)) { throw "Windows Script Host not found: $WScriptExe" }

# Get first so nonexistent tasks fail without changing anything.
$before = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$newAction = New-ScheduledTaskAction -Execute $WScriptExe -Argument ('"' + $QuietRunner + '"')
Set-ScheduledTask -TaskName $TaskName -Action $newAction -ErrorAction Stop | Out-Null
$after = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ($after.Actions[0].Execute -notlike "*wscript.exe") {
    throw "Task update did not persist. Check Task Scheduler permissions."
}
Write-Host "[OK] KeibaAI_BackgroundMonitor now runs invisibly."
Write-Host "[OK] Existing schedule and background monitoring are preserved."
Write-Host "[INFO] The next scheduled run will use the hidden launcher."
