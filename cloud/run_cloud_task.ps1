# Run by Task Scheduler on the cloud VM even when RDP is disconnected.
# No browser or Streamlit session is required. The owner must have installed
# and licensed JV-Link separately; this runner does not register Data Lab.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$scan = Join-Path $root "scripts\run_background_scan.py"
$feed = Join-Path $root "data\authorized_feed\latest_jvlink_feed.json"
$db = Join-Path $root "data\alert_state.db"
$report = Join-Path $root "data\last_background_scan.json"
$logs = Join-Path $root "data\logs"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing venv Python. Run cloud/install_cloud_worker.ps1 first."
}
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$log = Join-Path $logs ("cloud-monitor-" + (Get-Date -Format "yyyyMMdd") + ".log")
$env:PYTHONPATH = $root
$env:KEIBA_DEPLOYMENT_MODE = "cloud"
$env:PYTHONUNBUFFERED = "1"
Push-Location $root
try {
    # One synchronous scan per minute; failed acquisitions are visible in the
    # JSON heartbeat and in this dated text log.
    & $python $scan --manifest $feed --db $db --out $report --use-jvlink 2>&1 |
        Out-File -FilePath $log -Encoding utf8 -Append
    $code = $LASTEXITCODE
    # Remove only old logs; do not erase race/model/odds evidence.
    Get-ChildItem -Path $logs -Filter "cloud-monitor-*.log" -File |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
        Remove-Item -Force
    exit $code
} catch {
    "[$(Get-Date -Format o)] Cloud runner crashed: $($_.Exception.Message)" |
        Out-File -FilePath $log -Encoding utf8 -Append
    exit 1
} finally {
    Pop-Location
}
