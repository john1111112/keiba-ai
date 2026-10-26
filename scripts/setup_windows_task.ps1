# Windows Task Scheduler registration script for KEIBA AI Background Monitor
param(
    [string]$Action = "register",  # "register", "unregister", "status", "run-now"
    [int]$IntervalMinutes = 5
)

$TaskName = "KeibaAI_BackgroundMonitor"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot = Split-Path -Parent $ScriptDir
$PythonExe = (Get-Command py, python -ErrorAction SilentlyContinue | Select-Object -First 1).Source

if (-not $PythonExe) {
    if (Test-Path "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe") {
        $PythonExe = "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
    } else {
        Write-Error "Python 3 executable could not be located on PATH."
        exit 1
    }
}

$RunnerBat = Join-Path $RepoRoot "scripts\run_scheduled_task.bat"

if ($Action -eq "register") {
    Write-Host "[INFO] Registering Windows Scheduled Task: $TaskName"
    
    # Unregister existing task if present
    schtasks.exe /Delete /TN $TaskName /F 2>$null | Out-Null

    # Create task running at logon and repeating every N minutes
    # Using runner batch file to avoid Windows 261-character command line limit
    $Cmd = "schtasks.exe /Create /TN `"$TaskName`" /TR `"`"$RunnerBat`"`" /SC MINUTE /MO $IntervalMinutes /F"
    Invoke-Expression $Cmd
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[SUCCESS] Task '$TaskName' registered successfully (runs every $IntervalMinutes minutes)."
    } else {
        Write-Warning "[WARN] Registration with /SC MINUTE returned $LASTEXITCODE. Falling back to /SC ONLOGON..."
        schtasks.exe /Create /TN "$TaskName" /TR "`"$RunnerBat`"" /SC ONLOGON /F
    }
}
elseif ($Action -eq "unregister") {
    Write-Host "[INFO] Unregistering Windows Scheduled Task: $TaskName"
    schtasks.exe /Delete /TN $TaskName /F
}
elseif ($Action -eq "run-now") {
    Write-Host "[INFO] Executing task immediately: $TaskName"
    schtasks.exe /Run /TN $TaskName
}
elseif ($Action -eq "status") {
    $taskCheck = schtasks.exe /Query /TN $TaskName 2>&1
    if ($LASTEXITCODE -eq 0) {
        schtasks.exe /Query /TN $TaskName /V /FO LIST
    } else {
        Write-Host "[STATUS] Task '$TaskName' is currently NOT registered."
    }
}
else {
    Write-Host "Unknown action: $Action. Use 'register', 'unregister', 'status', or 'run-now'."
}
