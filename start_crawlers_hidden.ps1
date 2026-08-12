$ErrorActionPreference = 'Stop'

$py = 'C:\Users\SYH\AppData\Local\Programs\Python\Python311\python.exe'
$workDir = $PSScriptRoot
$logDir = Join-Path $workDir '_logs\hidden_launch'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$launcherLog = Join-Path $logDir ("launcher_{0}.log" -f $stamp)

function Write-Log {
    param([string]$Message)
    $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Message
    Add-Content -LiteralPath $launcherLog -Value $line -Encoding UTF8
}

$jobs = @(
    @{ Name = 'edge_9223'; Script = 'taobao_run_edge_1.py'; Urls = 'urls_1.txt'; Port = '9223' },
    @{ Name = 'edge_9224'; Script = 'taobao_run_edge_2.py'; Urls = 'urls_2.txt'; Port = '9224' },
    @{ Name = 'edge_9225'; Script = 'taobao_run_edge_3.py'; Urls = 'urls_3.txt'; Port = '9225' },
    @{ Name = 'edge_9226'; Script = 'taobao_run_edge_4.py'; Urls = 'urls_4.txt'; Port = '9226' },
    @{ Name = 'edge_9227'; Script = 'taobao_run_edge_5.py'; Urls = 'urls_5.txt'; Port = '9227' }
)

$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'taobao_run_edge_[1-5]\.py' }
if ($existing) {
    $ids = ($existing.ProcessId -join ', ')
    Write-Log ("Already running crawler pids: {0}; abort to avoid duplicate ports." -f $ids)
    exit 1
}

Write-Log 'Start hidden launch.'

for ($i = 0; $i -lt $jobs.Count; $i++) {
    $job = $jobs[$i]
    $outLog = Join-Path $logDir ("{0}_{1}.log" -f $job.Name, $stamp)
    $errLog = Join-Path $logDir ("{0}_{1}.err.log" -f $job.Name, $stamp)
    $p = Start-Process -FilePath $py `
        -ArgumentList @($job.Script, $job.Urls, $job.Port) `
        -WorkingDirectory $workDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru
    Write-Log ("Started {0} pid={1}" -f $job.Name, $p.Id)
    if ($i -lt $jobs.Count - 1) {
        Start-Sleep -Seconds 45
    }
}

Write-Log 'All 5 crawlers started.'
