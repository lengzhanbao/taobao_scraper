# Taobao Live Scraper - 项目概览

## 📦 新项目结构 (v2.0)

本次重构将原项目从混乱的单目录结构重组为标准的Python包结构，提升了可维护性和专业度。

### 主要改进

✅ **模块化设计** - 清晰的代码组织  
✅ **标准化结构** - 符合Python最佳实践  
✅ **完善文档** - 中英双语，SEO优化  
✅ **易用性提升** - 统一启动脚本，快速安装  
✅ **可扩展性** - 便于添加新功能  

### 目录对比

**旧结构 (v1.0):**
```
直播爬取/
├── taobao_run_edge_1.py
├── taobao_run_edge_2.py
├── ...
├── parse_taobao_data.py
├── 直播研究数据/
└── 大量混杂文件
```

**新结构 (v2.0):**
```
taobao-live-scraper/
├── src/              # 核心代码
│   ├── crawler/
│   ├── parser/
│   ├── detector/
│   └── utils/
├── scripts/          # 可执行脚本
├── config/           # 配置模板
├── docs/             # 文档
├── examples/         # 示例
└── 标准项目文件
```

---

## 📋 功能对照表

| 功能 | v1.0 | v2.0 | 说明 |
|------|------|------|------|
| 多实例爬虫 | ✅ | ✅ | 5个独立脚本 → 统一启动 |
| 数据解析 | ✅ | ✅ | 功能保持一致 |
| 数字人检测 | ✅ | ✅ | 功能保持一致 |
| URL管理 | ✅ | ✅ | 功能保持一致 |
| 统一启动 | ❌ | ✅ | 新增 |
| 统一停止 | ❌ | ✅ | 新增 |
| 快速安装 | ❌ | ✅ | 新增 |
| 模块化导入 | ❌ | ✅ | 新增 |
| 完整文档 | 部分 | ✅ | 大幅完善 |
| 中英双语 | ❌ | ✅ | 新增 |
| 配置模板 | ❌ | ✅ | 新增 |
| CI/CD | ❌ | ✅ | 新增 |

---

## 🔄 迁移指南

### 从v1.0迁移到v2.0

**1. 数据兼容性**

✅ 完全兼容！新版本可以直接使用旧版本的数据：

```powershell
# 保持原有数据目录
$env:LIVE_STUDY_ROOT = "E:\直播爬取\直播研究数据"

# 运行新版爬虫
cd E:\taobao-live-scraper
python scripts/crawler_instance_1.py
```

**2. 配置迁移**

```powershell
# 复制URL配置
Copy-Item "E:\直播爬取\直播研究数据\_config\*" -Destination "E:\taobao-live-scraper\直播研究数据\_config\" -Recurse

# 复制Cookie
Copy-Item "E:\直播爬取\直播研究数据\_config\taobao_cookies.json" -Destination "E:\taobao-live-scraper\直播研究数据\_config\"
```

**3. 脚本对应关系**

| v1.0 | v2.0 |
|------|------|
| `taobao_run_edge_1.py` | `scripts/crawler_instance_1.py` |
| `taobao_run_edge_2.py` | `scripts/crawler_instance_2.py` |
| `parse_taobao_data.py` | `scripts/parse_data.py` |
| `update_urls_v2.py` | `scripts/update_urls.py` |
| `collect_digital_urls.py` | `scripts/collect_digital.py` |
| `start_crawlers_hidden.ps1` | `scripts/start_all_crawlers.ps1` |

---

## 📚 文档索引

### 快速开始
- [快速入门 (5分钟)](docs/QUICKSTART.md) - 最快上手指南
- [README](README.md) - 完整使用文档

### 深入了解
- [架构说明](docs/ARCHITECTURE.md) - 项目架构设计
- [使用示例](examples/EXAMPLES.md) - 各种场景示例
- [故障排查](docs/TROUBLESHOOTING.md) - 问题解决方案

### 开发相关
- [贡献指南](CONTRIBUTING.md) - 如何贡献代码
- [更新日志](CHANGELOG.md) - 版本历史

### 配置说明
- [配置文档](config/README.md) - 环境变量和配置

---

## 🚀 快速命令参考

### 安装和设置
```bash
# 快速安装
git clone https://github.com/lengzhanbao/taobao_scraper.git
cd taobao_scraper
python setup_quick.py

# 手动安装
pip install -r requirements.txt
python -c "from src.utils.config import STUDY_ROOT; print('OK')"
```

### 运行爬虫
```powershell
# 启动所有实例
powershell -ExecutionPolicy Bypass -File scripts\start_all_crawlers.ps1

# 启动单个实例
python scripts/crawler_instance_1.py

# 停止所有实例
powershell -ExecutionPolicy Bypass -File scripts\stop_all_crawlers.ps1
```

### 数据处理
```bash
# 解析数据
python scripts/parse_data.py

# 更新URL状态
python scripts/update_urls.py --apply

# 收集数字人URL
python scripts/collect_digital.py
```

### 状态检查
```powershell
# 查看运行中的爬虫
Get-Process python | Where-Object {$_.CommandLine -match "crawler_instance"}

# 查看日志
Get-Content _logs\hidden_launch\crawler_1_*.log -Tail 20

# 检查磁盘空间
Get-PSDrive E
```

---

## 🎯 核心优势

### 1. 易用性
- ✅ 一键安装脚本
- ✅ 统一启动/停止
- ✅ 清晰的错误提示
- ✅ 完善的文档

### 2. 可靠性
- ✅ 自动Cookie管理
- ✅ 错误自动重试
- ✅ 数据完整性检查
- ✅ 防封禁机制

### 3. 可维护性
- ✅ 模块化代码
- ✅ 清晰的结构
- ✅ 完整的注释
- ✅ 标准化规范

### 4. 可扩展性
- ✅ 插件式架构
- ✅ 配置化设计
- ✅ 开放接口
- ✅ 便于二次开发

---

## 📊 性能数据

### 资源占用 (单实例)
- **CPU**: 15-25%
- **内存**: 500-800MB
- **磁盘**: ~3GB/小时 (视频)
- **网络**: 2-5Mbps

### 采集效率
- **录制时长**: 20分钟/段
- **冷却时间**: 2小时
- **单实例**: 3-4个房间/天
- **5实例**: 15-20个房间/天

---

## 🔗 相关链接

- **GitHub**: https://github.com/lengzhanbao/taobao_scraper
- **Issues**: https://github.com/lengzhanbao/taobao_scraper/issues
- **原项目**: https://github.com/lengzhanbao/taobao_scraper (v1.0)

---

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE)

---

## 🙏 致谢

感谢所有贡献者和使用者的支持！

如有问题或建议，欢迎提交Issue或Pull Request。

