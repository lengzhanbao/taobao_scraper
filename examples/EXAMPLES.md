# 使用示例

## 场景1: 监控单个数字人直播间

### 1. 准备URL

在 `直播研究数据/_config/urls_1.txt` 添加:
```
https://tbzb.taobao.com/live?liveId=123456789,测试店铺,已录制0/3
```

### 2. 启动爬虫

```bash
python scripts/crawler_instance_1.py
```

### 3. 等待录制完成

爬虫会自动:
- 每段录制20分钟
- 录制完成后冷却2小时
- 共录制3段后停止

### 4. 解析数据

```bash
python scripts/parse_data.py
```

### 5. 查看结果

数据位于: `直播研究数据/sessions/测试店铺_123456789/`

---

## 场景2: 批量监控多个直播间

### 1. 准备多个URL文件

**urls_1.txt** (实例1 - 3段)
```
https://tbzb.taobao.com/live?liveId=111111111,店铺A,已录制0/3
https://tbzb.taobao.com/live?liveId=222222222,店铺B,已录制0/3
```

**urls_2.txt** (实例2 - 3段)
```
https://tbzb.taobao.com/live?liveId=333333333,店铺C,已录制0/3
https://tbzb.taobao.com/live?liveId=444444444,店铺D,已录制0/3
```

**urls_4.txt** (实例4 - 4段，重点房间)
```
https://tbzb.taobao.com/live?liveId=555555555,重点店铺,已录制0/4
```

### 2. 启动所有爬虫

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_all_crawlers.ps1
```

### 3. 监控运行状态

查看日志:
```powershell
Get-Content _logs\hidden_launch\crawler_1_*.log -Tail 20
```

### 4. 停止爬虫

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all_crawlers.ps1
```

---

## 场景3: 自动发现数字人直播间

### 1. 配置Cookie

确保已登录并保存Cookie到 `直播研究数据/_config/taobao_cookies.json`

### 2. 运行收集脚本

```bash
python scripts/collect_digital.py
```

### 3. 查看收集结果

新发现的数字人URL会追加到: `直播研究数据/_config/live_urls.txt`

### 4. 分配到爬虫实例

手动复制URL到 `urls_1.txt ~ urls_5.txt`

---

## 场景4: 数据分析

### 1. 导入CSV到Excel/Python

```python
import pandas as pd

# 读取直播汇总数据
df = pd.read_csv('直播研究数据/sessions/店铺名_123456789/crawler/lives_summary_*.csv', encoding='utf-8-sig')

# 查看基本统计
print(df[['观看人数', '点赞数', '弹幕数']].describe())

# 分析粉丝增长
print(df[['粉丝数(开始)', '粉丝数(结束)', '新增粉丝量']])
```

### 2. 弹幕词云分析

```python
from wordcloud import WordCloud
import matplotlib.pyplot as plt

# 读取弹幕
comments = pd.read_csv('直播研究数据/sessions/店铺名_123456789/crawler/comments_第1段_*.csv', encoding='utf-8-sig')

# 生成词云
text = ' '.join(comments['内容'].dropna())
wordcloud = WordCloud(font_path='simhei.ttf', width=800, height=400).generate(text)

plt.figure(figsize=(10, 5))
plt.imshow(wordcloud, interpolation='bilinear')
plt.axis('off')
plt.savefig('wordcloud.png')
```

### 3. 视频分析

视频文件位于: `直播研究数据/sessions/<店铺名>_<liveId>/video/`

可使用FFmpeg进一步处理:
```bash
# 转换为MP4
ffmpeg -i input.flv -c:v libx264 -c:a aac output.mp4

# 提取关键帧
ffmpeg -i input.flv -vf "select=eq(pict_type\,I)" -vsync vfr frame_%04d.png

# 压缩视频
ffmpeg -i input.flv -vcodec libx264 -crf 28 output_compressed.mp4
```

---

## 场景5: 定时任务（Windows）

### 使用任务计划程序

1. 打开"任务计划程序"
2. 创建基本任务
3. 触发器: 每天早上9点
4. 操作: 启动程序
   - 程序: `powershell.exe`
   - 参数: `-ExecutionPolicy Bypass -File "E:\taobao-live-scraper\scripts\start_all_crawlers.ps1"`
5. 完成设置

### 使用schtasks命令

```powershell
schtasks /create /tn "TaobaoLiveScraper" /tr "powershell.exe -ExecutionPolicy Bypass -File 'E:\taobao-live-scraper\scripts\start_all_crawlers.ps1'" /sc daily /st 09:00
```

---

## 场景6: Linux后台运行

### 使用screen

```bash
# 创建screen会话
screen -S taobao_crawler

# 启动爬虫
python scripts/crawler_instance_1.py

# 分离会话 (Ctrl+A, D)

# 重新连接
screen -r taobao_crawler
```

### 使用systemd服务

创建 `/etc/systemd/system/taobao-crawler@.service`:

```ini
[Unit]
Description=Taobao Live Crawler Instance %i
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/taobao-live-scraper
ExecStart=/usr/bin/python3 scripts/crawler_instance_%i.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

启动服务:
```bash
sudo systemctl enable taobao-crawler@1.service
sudo systemctl start taobao-crawler@1.service
sudo systemctl status taobao-crawler@1.service
```

---

## 常用命令参考

### 查看爬虫状态

```powershell
# Windows
Get-Process python | Where-Object {$_.CommandLine -match "crawler_instance"}

# Linux
ps aux | grep crawler_instance
```

### 清理旧日志

```powershell
# 删除7天前的日志
Get-ChildItem -Path "_logs\hidden_launch" -Filter "*.log" | 
    Where-Object {$_.LastWriteTime -lt (Get-Date).AddDays(-7)} | 
    Remove-Item
```

### 批量更新URL状态

```bash
# 预览
python scripts/update_urls.py

# 应用
python scripts/update_urls.py --apply
```

### 检查磁盘空间

```powershell
# Windows
Get-PSDrive E | Select-Object Used,Free

# Linux
df -h /path/to/data
```

