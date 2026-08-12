# 淘宝直播爬取

这个仓库是一套淘宝直播数字人直播间采集工具。它可以帮你：

- 通过 5 个浏览器实例录制淘宝直播间的视频。
- 抓取直播间弹幕、商品信息和直播摘要。
- 检测哪些直播间是数字人直播。
- 把录制结果整理成 `sessions` 目录下的研究数据。

仓库里只保存代码，不包含直播数据、结果、Cookie、浏览器登录态和 `sessions`。这些内容都需要在你自己的电脑上准备。

## 这个项目的基本结构

```text
taobao_scraper/
├─ taobao_run_edge_1.py ~ 5.py    # 5 个爬虫实例
├─ parse_taobao_data.py            # 解析并归档录制数据
├─ update_urls_v2.py               # 更新 urls 录制状态
├─ collect_digital_urls.py         # 收集数字人直播间
├─ detect_digital_20260806.py/mjs  # 检测数字人直播间
├─ finalize_7min_segments.py       # 补齐 7 分钟段的 final JSON
├─ start_crawlers_hidden.ps1/vbs   # 隐藏启动 5 个爬虫实例
├─ _logs/serve_status.py           # 本地仪表盘服务
└─ config.py                       # 路径配置
```

## 从零开始

如果你第一次使用这个项目，可以按下面的顺序准备。

### 第 1 步：下载项目

```powershell
git clone https://github.com/lengzhanbao/taobao_scraper.git
cd taobao_scraper
```

### 第 2 步：安装 Python

推荐使用 Python 3.11 或更高版本。

先确认 Python 已经安装：

```powershell
python --version
```

### 第 3 步：安装 Python 依赖

推荐使用虚拟环境，这样不会影响系统里的其他 Python 环境。

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

如果你不想使用虚拟环境，也可以直接安装：

```powershell
pip install -r requirements.txt
```

### 第 4 步：安装 Node.js 和 playwright-core

数字人检测脚本 `detect_digital_20260806.mjs` 需要 Node.js 和 `playwright-core`。

```powershell
node --version
npm install playwright-core
```

如果你已经有自己的 `playwright-core`，可以不执行 `npm install`，改为设置路径：

```powershell
$env:LIVE_PLAYWRIGHT_CORE_PATH = "你的 playwright-core 安装路径"
```

### 第 5 步：创建运行目录

项目运行时需要以下本地目录：

```text
直播研究数据/
  _config/
  _staging/
  sessions/
DouyinLiveRecorder_v4.0.7/
  ffmpeg/
```

你可以用下面的命令创建：

```powershell
New-Item -ItemType Directory -Force -Path "直播研究数据\_config"
New-Item -ItemType Directory -Force -Path "直播研究数据\_staging"
New-Item -ItemType Directory -Force -Path "直播研究数据\sessions"
New-Item -ItemType Directory -Force -Path "DouyinLiveRecorder_v4.0.7\ffmpeg"
```

这些目录是运行必需的，但它们不会进入 Git。

### 第 6 步：准备 urls 文件

在 `直播研究数据/_config` 下创建：

```text
urls_1.txt
urls_2.txt
urls_3.txt
urls_4.txt
urls_5.txt
```

每行格式如下：

```text
https://tbzb.taobao.com/live?liveId=123456789,直播间名,已录制0/3
```

其中：

- `urls_1.txt ~ urls_3.txt` 和 `urls_5.txt` 每个直播间录 3 段。
- `urls_4.txt` 每个直播间录 4 段。

`urls_4.txt` 示例：

```text
https://tbzb.taobao.com/live?liveId=123456789,直播间名,已录制0/4
```

### 第 7 步：准备登录 Cookie

仓库不包含 Cookie。你需要准备淘宝登录态。

最直接的方式是把你已有的 `taobao_cookies.json` 放到：

```text
直播研究数据/_config/taobao_cookies.json
```

如果你没有现成 Cookie，可以先单独运行一个爬虫脚本完成登录：

```powershell
python taobao_run_edge_1.py urls_1.txt 9223
```

登录成功后脚本会保存 Cookie。之后再用隐藏方式启动 5 个实例。

### 第 8 步：准备 ffmpeg

爬虫使用 ffmpeg 录制直播流。

默认路径是：

```text
DouyinLiveRecorder_v4.0.7/ffmpeg/ffmpeg.exe
```

如果你把 ffmpeg 放在其他位置，可以设置：

```powershell
$env:LIVE_FFMPEG = "你的 ffmpeg.exe 路径"
```

### 第 9 步：准备 Edge

代码使用 Microsoft Edge 浏览器。

Windows 默认路径通常不需要配置。如果 Edge 安装在特殊位置，可以设置：

```powershell
$env:LIVE_EDGE_PATH = "你的 msedge.exe 路径"
```

第一次运行时需要登录淘宝。登录态会保存在：

```text
.edge_9223
.edge_9224
.edge_9225
.edge_9226
.edge_9227
```

这些目录不会进入 Git。

### 第 10 步：启动爬虫

准备完成后，可以用隐藏方式启动 5 个爬虫实例：

```powershell
powershell -ExecutionPolicy Bypass -File start_crawlers_hidden.ps1
```

也可以双击 `start_crawlers_hidden.vbs`。

每个实例对应关系：

```text
urls_1.txt -> 9223 -> taobao_run_edge_1.py
urls_2.txt -> 9224 -> taobao_run_edge_2.py
urls_3.txt -> 9225 -> taobao_run_edge_3.py
urls_4.txt -> 9226 -> taobao_run_edge_4.py
urls_5.txt -> 9227 -> taobao_run_edge_5.py
```

## 日常操作

### 解析归档

录制完成后，可以解析并归档：

```powershell
python parse_taobao_data.py
```

### 更新 urls 状态

先预览：

```powershell
python update_urls_v2.py
```

确认无误后写入：

```powershell
python update_urls_v2.py --apply
```

### 数字人直播检测

Node 版本：

```powershell
node detect_digital_20260806.mjs "C:\path\to\urls.txt"
```

Python 版本：

```powershell
python detect_digital_20260806.py <liveId>
```

### 补齐 7 分钟段 final JSON

```powershell
python finalize_7min_segments.py
```

### 查看仪表盘

启动服务：

```powershell
python _logs/serve_status.py
```

然后打开：

```text
http://127.0.0.1:8765
```

## 环境变量

以下是可配置的环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LIVE_STUDY_ROOT` | `./直播研究数据` | 数据根目录 |
| `LIVE_FFMPEG` | `./DouyinLiveRecorder_v4.0.7/ffmpeg/ffmpeg.exe` | ffmpeg 可执行文件 |
| `LIVE_PYTHON` | `python` | Python 可执行文件 |
| `LIVE_EDGE_PATH` | Windows 默认 Edge 路径 | Edge 可执行文件 |
| `LIVE_PLAYWRIGHT_CORE_PATH` | 无 | 数字人检测脚本的 `playwright-core` 路径 |

## 常见问题

### 提示 Cookie 过期或没有登录

把新的 `taobao_cookies.json` 放到：

```text
直播研究数据/_config/taobao_cookies.json
```

或者重新运行单个爬虫脚本完成登录。

### 提示找不到 ffmpeg

确认 ffmpeg 文件存在，或设置：

```powershell
$env:LIVE_FFMPEG = "你的 ffmpeg.exe 路径"
```

### 提示找不到 playwright-core

执行：

```powershell
npm install playwright-core
```

如果已经安装，仍然找不到，则设置：

```powershell
$env:LIVE_PLAYWRIGHT_CORE_PATH = "你的 playwright-core 安装路径"
```

### 提示 Edge 找不到

设置 Edge 路径：

```powershell
$env:LIVE_EDGE_PATH = "你的 msedge.exe 路径"
```

### sessions 会不会被上传

不会。`直播研究数据/` 已经被 `.gitignore` 排除，`sessions` 会保留在你本机。

## 最后说明

- 这个仓库只包含运行代码。
- 数据、结果、日志、Cookie、Edge profile 和 `sessions` 都不会进入 Git。
- 第一次运行可能需要登录淘宝。
- 如果只是录制，最少需要准备：Python、ffmpeg、urls 文件、Cookie 和 Edge。
- 如果需要数字人检测，还需要 Node.js 和 `playwright-core`。
