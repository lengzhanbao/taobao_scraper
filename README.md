# 淘宝直播爬取

淘宝直播数字人直播采集代码。仓库只保存代码，不包含直播数据、结果、Cookie、浏览器登录态和 session。

## 运行前提

代码默认从仓库根目录读取以下本地目录：

```text
直播研究数据/
  _config/
    urls_1.txt ... urls_5.txt
    taobao_cookies.json
  _staging/
  sessions/
DouyinLiveRecorder_v4.0.7/
  ffmpeg/ffmpeg.exe
```

这些目录和文件不随 Git 上传，需要在运行机器上准备。`sessions` 不要放进 Git。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LIVE_STUDY_ROOT` | `./直播研究数据` | 数据根目录 |
| `LIVE_FFMPEG` | `./DouyinLiveRecorder_v4.0.7/ffmpeg/ffmpeg.exe` | ffmpeg 可执行文件 |
| `LIVE_PYTHON` | `python` | Python 可执行文件 |
| `LIVE_EDGE_PATH` | Windows 默认 Edge 路径 | Edge 可执行文件 |
| `LIVE_PLAYWRIGHT_CORE_PATH` | 无 | 数字人检测脚本的 `playwright-core` 路径 |

## 安装依赖

```powershell
pip install -r requirements.txt
```

如果使用 `detect_digital_20260806.mjs`，还需要 Node.js 和 `playwright-core`。本机没有全局安装时，设置：

```powershell
$env:LIVE_PLAYWRIGHT_CORE_PATH = "你的 playwright-core 安装路径"
```

## 启动 5 实例爬虫

```powershell
powershell -ExecutionPolicy Bypass -File start_crawlers_hidden.ps1
```

也可以双击 `start_crawlers_hidden.vbs`，脚本会隐藏启动 5 个 Edge 实例。

每个实例读取：

```text
urls_1.txt -> 9223 -> taobao_run_edge_1.py
urls_2.txt -> 9224 -> taobao_run_edge_2.py
urls_3.txt -> 9225 -> taobao_run_edge_3.py
urls_4.txt -> 9226 -> taobao_run_edge_4.py
urls_5.txt -> 9227 -> taobao_run_edge_5.py
```

## 解析归档

```powershell
python parse_taobao_data.py
```

## 更新 urls 录制状态

先预览：

```powershell
python update_urls_v2.py
```

确认后写入：

```powershell
python update_urls_v2.py --apply
```

## 数字人直播检测

Node 版：

```powershell
node detect_digital_20260806.mjs "C:\path\to\urls.txt"
```

Python 版：

```powershell
python detect_digital_20260806.py <liveId>
```

## 补齐 7 分钟段 final JSON

```powershell
python finalize_7min_segments.py
```

## 仪表盘

```powershell
python _logs/serve_status.py
```

然后打开：

```text
http://127.0.0.1:8765
```

## 说明

- 仓库不含 Cookie，首次运行可能需要登录或注入 Cookie。
- 仓库不含 Edge 登录 profile，`.edge_9223~9227` 需要在本机准备。
- 数据、结果、日志、浏览器 profile 全部由 `.gitignore` 排除。
