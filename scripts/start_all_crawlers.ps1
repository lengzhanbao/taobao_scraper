# Taobao Live Scraper - Start All Crawlers (Hidden Mode)
# This script launches 5 crawler instances in hidden windows
# Each instance monitors different URLs and uses separate browser profiles

$ErrorActionPreference = 'Stop'

# Get Python executable
$py = $env:LIVE_PYTHON
if (-not $py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    $py = if ($cmd) { $cmd.Source } else { 'python' }
}

$workDir = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $workDir '_logs\hidden_launch'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$launcherLog = Join-Path $logDir ("launcher_{0}.log" -f $stamp)

function Write-Log {
    param([string]$Message)
    $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Message
    Add-Content -LiteralPath $launcherLog -Value $line -Encoding UTF8
    Write-Host $Message
}

# Define crawler instances
$jobs = @(
    @{ Name = 'crawler_1'; Script = 'scripts\crawler_instance_1.py'; Port = '9223' },
    @{ Name = 'crawler_2'; Script = 'scripts\crawler_instance_2.py'; Port = '9224' },
    @{ Name = 'crawler_3'; Script = 'scripts\crawler_instance_3.py'; Port = '9225' },
    @{ Name = 'crawler_4'; Script = 'scripts\crawler_instance_4.py'; Port = '9226' },
    @{ Name = 'crawler_5'; Script = 'scripts\crawler_instance_5.py'; Port = '9227' }
)

# Check for existing crawlers
$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'crawler_instance_[1-5]\.py' }
if ($existing) {
    $ids = ($existing.ProcessId -join ', ')
    Write-Log "ERROR: Crawlers already running (PIDs: $ids). Stop them first to avoid port conflicts."
    exit 1
}

Write-Log "Starting Taobao Live Scraper - 5 crawler instances"
Write-Log "Log directory: $logDir"

# Launch each crawler instance
for ($i = 0; $i -lt $jobs.Count; $i++) {
    $job = $jobs[$i]
    $outLog = Join-Path $logDir ("{0}_{1}.log" -f $job.Name, $stamp)
    $errLog = Join-Path $logDir ("{0}_{1}.err.log" -f $job.Name, $stamp)
    
    $scriptPath = Join-Path $workDir $job.Script
    
    $p = Start-Process -FilePath $py `
        -ArgumentList @($scriptPath) `
        -WorkingDirectory $workDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru
    
    Write-Log ("Started {0} (PID: {1}, Port: {2})" -f $job.Name, $p.Id, $job.Port)
    
    # Stagger launches to avoid resource conflicts
    if ($i -lt $jobs.Count - 1) {
        Write-Log "Waiting 45 seconds before starting next instance..."
        Start-Sleep -Seconds 45
    }
}

Write-Log ""
Write-Log "All 5 crawlers started successfully!"
Write-Log "Monitor logs in: $logDir"
Write-Log "To stop crawlers, run: scripts\stop_all_crawlers.ps1"
