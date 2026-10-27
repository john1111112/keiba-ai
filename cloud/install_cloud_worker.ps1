# KEIBA AI cloud installer - Japanese Windows 10/11 VM only.
# JRA-VAN does NOT support cloud JV-Link: this is a best-effort installation.
# Run elevated; -Activate intentionally permits JV-Link calls / trial usage.
param(
    [switch]$Activate,
    [ValidateRange(1,30)][int]$IntervalMinutes = 1,
    [System.Management.Automation.PSCredential]$Credential
)
$ErrorActionPreference = "Stop"
$taskName = "KeibaAI_CloudMonitor"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$runner = Join-Path $PSScriptRoot "run_cloud_task.ps1"
$principal = [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Start PowerShell as Administrator in the cloud VM."
}
$os = Get-CimInstance Win32_OperatingSystem
if ($os.ProductType -ne 1 -or $os.Caption -notmatch 'Windows (10|11)') {
    throw "Official JV-Link supports Japanese Windows 10/11, not Windows Server."
}
if ((Get-UICulture).Name -ne "ja-JP") {
    Write-Warning "JRA-VAN officially supports Japanese Windows 10/11 only."
}
Write-Warning "JRA-VAN: virtualized/cloud JV-Link is untested and unsupported."

Write-Host "[1/4] Python and dependency preparation"
foreach ($required in @(
    "requirements.txt", "scripts\run_background_scan.py",
    "scripts\probe_jvlink_com.py",
    "models\keiba_lgbm_win_v2_28cols.joblib", "data\keiba.db"
)) {
    if (-not (Test-Path (Join-Path $root $required))) {
        throw "Missing required file: $required"
    }
}
if (-not (Test-Path $python)) {
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        & py.exe -3 -m venv (Join-Path $root ".venv")
    } else {
        $pythonCmd = (Get-Command python.exe -ErrorAction Stop).Source
        & $pythonCmd -m venv (Join-Path $root ".venv")
    }
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed." }
}
& $python -m pip install -r (Join-Path $root "requirements.txt") pywin32
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }

Write-Host "[2/4] Read-only JV-Link COM probe (does not call JVInit)"
& $python (Join-Path $root "scripts\probe_jvlink_com.py")
if ($LASTEXITCODE -ne 0) {
    Write-Warning "JVDTLab.JVLink COM not available. Install the licensed SDK."
    if ($Activate) { throw "Cannot activate until COM is available." }
}

$discordPath = Join-Path $root "data\discord_config.json"
$discordOK = $false
if (Test-Path $discordPath) {
    try {
        $discordURL = (Get-Content -Raw $discordPath | ConvertFrom-Json).webhook_url
        $discordOK = [bool]($discordURL -match '^https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+')
    } catch { $discordOK = $false }
}
if ($Activate -and -not $discordOK) {
    throw "Run cloud/configure_discord.ps1 and set a private webhook first."
}

Write-Host "[3/4] Register task with password logon (works while RDP disconnected)"
if (-not $Credential) {
    $Credential = Get-Credential -Message "Cloud Windows account with JV-Link settings (unattended logon)"
}
if (-not $Credential) { throw "A Windows task account is required." }
$actionArgs = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $runner + '"'
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgs -WorkingDirectory $root
$triggerArgs = @{
    Once = $true
    At = (Get-Date).AddMinutes(1)
    RepetitionInterval = (New-TimeSpan -Minutes $IntervalMinutes)
    RepetitionDuration = (New-TimeSpan -Days 3650)
}
$recurring = New-ScheduledTaskTrigger @triggerArgs
$atStartup = New-ScheduledTaskTrigger -AtStartup
$settingsArgs = @{
    MultipleInstances = "IgnoreNew"
    StartWhenAvailable = $true
    ExecutionTimeLimit = (New-TimeSpan -Minutes 10)
    RestartCount = 3
    RestartInterval = (New-TimeSpan -Minutes 1)
    AllowStartIfOnBatteries = $true
    DontStopIfGoingOnBatteries = $true
}
$settings = New-ScheduledTaskSettingsSet @settingsArgs
$password = $Credential.GetNetworkCredential().Password
try {
    $task = @{
        TaskName = $taskName
        Action = $action
        Trigger = @($atStartup, $recurring)
        Settings = $settings
        User = $Credential.UserName
        Password = $password
        RunLevel = "Highest"
        Force = $true
    }
    Register-ScheduledTask @task | Out-Null
} finally {
    $password = $null
}

# Disable desktop toasts: only the phone's Discord webhook matters.
$configPath = Join-Path $root "data\alert_config.json"
$cfg = @{}
if (Test-Path $configPath) {
    $stored = Get-Content -Raw $configPath | ConvertFrom-Json
    if ($stored) {
        foreach ($prop in $stored.PSObject.Properties) { $cfg[$prop.Name] = $prop.Value }
    }
}
$cfg["windows_toast_enabled"] = $false
$cfg["discord_enabled"] = $true
$cfg["max_retries"] = 0
$cfg["safety_lock_enabled"] = -not [bool]$Activate
$json = $cfg | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText(
    "$configPath.tmp", $json, [System.Text.UTF8Encoding]::new($false)
)
Move-Item -Force "$configPath.tmp" $configPath

Write-Host "[4/4] Final activation state"
if ($Activate) {
    Enable-ScheduledTask -TaskName $taskName | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Host "[ACTIVE] Cloud VM now monitors all races without the home PC."
} else {
    Disable-ScheduledTask -TaskName $taskName | Out-Null
    Write-Host "[PREPARED] Cloud task is registered but disabled."
    Write-Host "Authorize JRA-VAN, configure Discord, then rerun with -Activate."
    Write-Host "No JVInit, free-trial start, or Discord message was sent."
}
