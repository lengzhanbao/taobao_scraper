# 故障排查指南

## 目录

- [安装问题](#安装问题)
- [登录问题](#登录问题)
- [录制问题](#录制问题)
- [数据问题](#数据问题)
- [性能问题](#性能问题)
- [其他问题](#其他问题)

---

## 安装问题

### 问题1: pip install失败

**症状:**
```
ERROR: Could not find a version that satisfies the requirement DrissionPage
```

**解决方案:**
```bash
# 更新pip
python -m pip install --upgrade pip

# 使用国内镜像
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 单独安装失败的包
pip install DrissionPage -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 问题2: Python版本不兼容

**症状:**
```
SyntaxError: invalid syntax
```

**解决方案:**
```bash
# 检查Python版本 (需要3.8+)
python --version

# 使用正确的Python版本
python3.11 -m venv .venv
```

### 问题3: FFmpeg找不到

**症状:**
```
FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'
```

**解决方案:**

**Windows:**
1. 下载: https://ffmpeg.org/download.html
2. 解压到 `DouyinLiveRecorder_v4.0.7/ffmpeg/`
3. 或设置环境变量:
   ```powershell
   $env:LIVE_FFMPEG = "D:\ffmpeg\bin\ffmpeg.exe"
   ```

**Linux:**
```bash
sudo apt install ffmpeg
export LIVE_FFMPEG=$(which ffmpeg)
```

---

## 登录问题

### 问题1: 二维码不显示

**症状:**
浏览器打开但看不到二维码

**解决方案:**
1. 手动点击"二维码登录"
2. 检查网络连接
3. 尝试刷新页面
4. 检查浏览器版本

### 问题2: Cookie过期

**症状:**
```
[ERROR] 登录失效
[ERROR] Cookie expired
```

**解决方案:**
```bash
# 删除旧Cookie
rm 直播研究数据/_config/taobao_cookies.json

# 重新登录
python scripts/crawler_instance_1.py
```

### 问题3: 滑块验证失败

**症状:**
```
[captcha] slider failed
```

**解决方案:**
1. 自动重试会生效
2. 手动拖动滑块
3. 刷新页面重试
4. 更换网络环境

### 问题4: 账号被风控

**症状:**
- 频繁要求验证
- 无法登录
- IP被封

**解决方案:**
1. 更换账号
2. 更换IP/代理
3. 降低采集频率
4. 增加冷却时间

---

## 录制问题

### 问题1: 端口被占用

**症状:**
```
ERROR: Address already in use: 9223
```

**解决方案:**
```powershell
# Windows - 查找占用进程
netstat -ano | findstr "9223"
taskkill /PID <进程ID> /F

# Linux
lsof -i :9223
kill -9 <PID>
```

### 问题2: 直播间无法访问

**症状:**
```
[ERROR] 页面加载失败
[ERROR] Navigation timeout
```

**解决方案:**
1. 检查直播间是否开播
2. 检查网络连接
3. 验证URL格式正确
4. 尝试手动访问该URL

### 问题3: 视频录制中断

**症状:**
- FLV文件不完整
- 录制时长不足20分钟

**解决方案:**
1. 检查磁盘空间
2. 检查网络稳定性
3. 查看FFmpeg日志
4. 降低并发实例数量

### 问题4: 爬虫卡死不动

**症状:**
长时间无日志输出

**解决方案:**
```powershell
# 查看进程
Get-Process python | Where-Object {$_.CommandLine -match "crawler"}

# 强制结束
Stop-Process -Name python -Force

# 重启爬虫
powershell -ExecutionPolicy Bypass -File scripts\start_all_crawlers.ps1
```

### 问题5: 内存占用过高

**症状:**
系统变慢，内存使用率90%+

**解决方案:**
1. 减少并发实例 (只运行1-3个)
2. 定期重启爬虫
3. 增加物理内存
4. 关闭浏览器GPU加速

---

## 数据问题

### 问题1: 数据解析失败

**症状:**
```
[ERROR] JSON decode error
[跳过] KeyError: 'liveId'
```

**解决方案:**
1. 检查JSON文件完整性
2. 查看原始数据格式
3. 更新解析逻辑
4. 手动修复损坏文件

### 问题2: CSV乱码

**症状:**
Excel打开CSV显示乱码

**解决方案:**
1. CSV已使用UTF-8-BOM编码
2. 用记事本打开->另存为UTF-8
3. 使用专业工具 (Notepad++, VSCode)
4. Excel导入时选择UTF-8

### 问题3: 视频文件损坏

**症状:**
- 无法播放
- 播放卡顿
- 文件大小异常

**解决方案:**
```bash
# 检查视频完整性
ffmpeg -v error -i video.flv -f null -

# 修复视频
ffmpeg -i broken.flv -c copy fixed.flv

# 转换格式
ffmpeg -i video.flv -c:v libx264 -c:a aac video.mp4
```

### 问题4: 数据缺失

**症状:**
- 弹幕为空
- 商品信息缺失
- 某些字段为空

**可能原因:**
1. 直播间刚开播（数据未就绪）
2. API返回不完整
3. 网络拦截失败
4. 直播间类型特殊

**解决方案:**
1. 延长录制时间
2. 检查网络拦截日志
3. 对比原始JSON
4. 手动补充数据

---

## 性能问题

### 问题1: CPU使用率100%

**原因:**
- 多个浏览器实例
- FFmpeg编码
- 数据处理

**解决方案:**
1. 降低并发数
2. 错峰启动
3. 限制FFmpeg线程数
4. 升级硬件

### 问题2: 磁盘空间不足

**症状:**
```
[ERROR] No space left on device
```

**解决方案:**
```powershell
# 检查磁盘空间
Get-PSDrive E

# 清理旧数据
Remove-Item -Path "_logs\*" -Recurse -Force -Older ((Get-Date).AddDays(-7))

# 压缩视频
ffmpeg -i large.flv -crf 28 compressed.flv

# 移动到其他磁盘
Move-Item -Path "直播研究数据\sessions\*" -Destination "D:\backup\"
```

### 问题3: 网络带宽不足

**症状:**
- 多个实例同时录制时视频卡顿
- 录制质量下降

**解决方案:**
1. 减少并发实例
2. 错峰录制
3. 升级网络带宽
4. 使用有线网络

---

## 其他问题

### 问题1: 日志太多

**解决方案:**
```powershell
# 清理7天前的日志
Get-ChildItem -Path "_logs" -Recurse -Filter "*.log" |
    Where-Object {$_.LastWriteTime -lt (Get-Date).AddDays(-7)} |
    Remove-Item -Force
```

### 问题2: Edge浏览器版本问题

**症状:**
```
[ERROR] Browser version mismatch
```

**解决方案:**
1. 更新Edge浏览器
2. 更新DrissionPage
3. 指定浏览器路径

### 问题3: Windows防火墙阻止

**症状:**
无法连接网络

**解决方案:**
1. 添加Python到防火墙例外
2. 添加Edge到防火墙例外
3. 临时关闭防火墙测试

### 问题4: 如何调试

**启用详细日志:**
```python
# 在crawler代码开头添加
import logging
logging.basicConfig(level=logging.DEBUG)
```

**查看实时日志:**
```powershell
Get-Content _logs\hidden_launch\crawler_1_*.log -Wait -Tail 50
```

**抓包分析:**
使用Fiddler或Charles抓包，分析API请求

---

## 获取帮助

如果以上方案都无法解决问题：

1. 查看完整错误日志
2. 搜索GitHub Issues
3. 提交新Issue，包含:
   - 完整错误信息
   - 系统环境
   - 复现步骤
   - 相关日志

GitHub Issues: https://github.com/lengzhanbao/taobao_scraper/issues

