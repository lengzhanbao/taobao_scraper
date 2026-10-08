# Taobao Live Scraper Configuration

## Environment Variables (Optional)

You can customize paths using environment variables:

### Windows (PowerShell)
```powershell
$env:LIVE_STUDY_ROOT = "D:\MyData\taobao_live_data"
$env:LIVE_FFMPEG = "D:\Tools\ffmpeg\bin\ffmpeg.exe"
$env:LIVE_PYTHON = "python3"
```

### Linux/Mac
```bash
export LIVE_STUDY_ROOT="/path/to/data"
export LIVE_FFMPEG="/usr/bin/ffmpeg"
export LIVE_PYTHON="python3"
```

## Available Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LIVE_STUDY_ROOT` | `./直播研究数据` | Data root directory |
| `LIVE_FFMPEG` | `./DouyinLiveRecorder_v4.0.7/ffmpeg/ffmpeg.exe` | FFmpeg executable |
| `LIVE_PYTHON` | `python` | Python executable |
| `LIVE_EDGE_PATH` | Windows default Edge path | Edge browser path |
| `LIVE_PLAYWRIGHT_CORE_PATH` | None | Playwright core path for detection |
| `LIVE_MAX_MIN` | `20` | Minutes recorded per segment |
| `LIVE_MAX_ROUND` | `3` | Default segments per room (urls file can override) |
| `LIVE_COOLDOWN_SEC` | `7200` | Cooldown between two recordings of the same room |
| `LIVE_BATCH_ROOMS` | `6` | Positive integer; 1 archives each completed room |
| `LIVE_PAUSE_FILE` | unset | Per-instance JSON control file with paused=true/false; pause after the current segment |
| `LIVE_PRODUCT_MIN_SEC` | `0` | Product switch handling (0 = record only) |
| `LIVE_MAX_COLLECTED` | `800` | Max intercepted responses kept in memory |
| `LIVE_USER_AGENT` | Chrome 126 UA | Browser user agent |

## Directory Structure

After setup, your project will have:

```
taobao-live-scraper/
├── 直播研究数据/           # Data directory (not in git)
│   ├── _config/           # Configuration files
│   │   ├── urls_1.txt     # URLs for crawler 1
│   │   ├── urls_2.txt     # URLs for crawler 2
│   │   ├── urls_3.txt     # URLs for crawler 3
│   │   ├── urls_4.txt     # URLs for crawler 4
│   │   ├── urls_5.txt     # URLs for crawler 5
│   │   └── taobao_cookies.json  # Login cookies
│   ├── _staging/          # Temporary recording data
│   └── sessions/          # Archived results
│       └── <店铺>_<liveId>/
│           ├── video/     # Recorded videos
│           ├── crawler/   # Extracted data (CSV)
│           └── raw/       # Raw JSON responses
└── DouyinLiveRecorder_v4.0.7/  # FFmpeg (not in git)
    └── ffmpeg/
        └── ffmpeg.exe
```
