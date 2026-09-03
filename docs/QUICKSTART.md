# 快速入门指南 (5分钟上手)

## 第一步：安装项目 (1分钟)

```bash
# 克隆项目
git clone https://github.com/lengzhanbao/taobao_scraper.git
cd taobao_scraper

# 运行快速安装脚本
python setup_quick.py
```

## 第二步：配置URL (1分钟)

编辑 `直播研究数据/_config/urls_1.txt`，添加直播间：

```
https://tbzb.taobao.com/live?liveId=123456789,测试店铺,已录制0/3
```

## 第三步：登录淘宝 (2分钟)

```bash
python scripts/crawler_instance_1.py
```

- 浏览器会自动打开淘宝登录页
- 使用手机淘宝扫码登录
- 登录成功后Cookie会自动保存
- 按Ctrl+C停止

## 第四步：开始录制 (1分钟)

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\start_all_crawlers.ps1

# Linux/Mac
nohup python scripts/crawler_instance_1.py > /dev/null 2>&1 &
```

## 第五步：查看结果

录制完成后，数据在：
```
直播研究数据/sessions/<店铺名>_<liveId>/
├── video/        # 视频文件
└── crawler/      # CSV数据
```

---

## 常见问题

### Q: 找不到FFmpeg？
**A:** 下载FFmpeg并放到 `DouyinLiveRecorder_v4.0.7/ffmpeg/`

### Q: Cookie过期？
**A:** 删除 `直播研究数据/_config/taobao_cookies.json`，重新登录

### Q: 如何停止爬虫？
**A:** 
```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all_crawlers.ps1
```

---

需要详细文档？查看 [README.md](../README.md)
