# Configure cloud VM's private webhook without passing the secret to Git/CLI.
# Does not contact Discord or JRA-VAN.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dataDir = Join-Path $root "data"
$configPath = Join-Path $dataDir "discord_config.json"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$private = Read-Host "Discord Webhook URL (input hidden)" -AsSecureString
$handle = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($private)
try {
    $url = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($handle)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($handle)
    $private.Dispose()
}
if ($url -notmatch '^https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+$') {
    throw "Invalid Discord webhook URL. No configuration was saved."
}
$payload = @{ webhook_url = $url } | ConvertTo-Json
[System.IO.File]::WriteAllText(
    "$configPath.tmp", $payload, [System.Text.UTF8Encoding]::new($false)
)
Move-Item -Force "$configPath.tmp" $configPath
$secretOwner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$ownerGrant = $secretOwner + ":(R,W)"
& icacls.exe $configPath /inheritance:r /grant:r $ownerGrant "SYSTEM:(F)" "Administrators:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Webhook saved, but its file access control must be corrected manually."
}
Write-Host "[OK] Private Discord webhook stored on this cloud Windows VM."
Write-Host "No test message was sent."
