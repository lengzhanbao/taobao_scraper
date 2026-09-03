# Stop All Taobao Live Crawlers
$ErrorActionPreference = 'Continue'

Write-Host "Stopping all crawler instances..."

$processes = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'crawler_instance_[1-5]\.py' }

if (-not $processes) {
    Write-Host "No running crawlers found."
    exit 0
}

foreach ($proc in $processes) {
    Write-Host "Stopping PID $($proc.ProcessId)..."
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2

# Verify all stopped
$remaining = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'crawler_instance_[1-5]\.py' }

if ($remaining) {
    Write-Host "WARNING: Some crawlers may still be running."
} else {
    Write-Host "All crawlers stopped successfully."
}
