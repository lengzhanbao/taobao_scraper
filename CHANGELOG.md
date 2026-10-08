# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- 控制台升级到 2.6：记录已捕获标记是否全 true、缺失与检测空窗，不新增数字人样本分类或覆盖阈值。
- 录制凭据保存 FFprobe 实测时长和 SHA256；归档逐段核验 JSON、视频、弹幕与汇总成套对应。真实空弹幕、未捕获和提取失败分别记录。
- 历史 7 分钟补全改为递归只读核验和独立候选输出，不用墙钟时间补足时长，不改原录制时间戳或 staging。
- 状态请求增加 10 秒超时及串行刷新；修改请求超时提示核对结果且不自动重试。
- 旧检测入口复用全部响应汇总；修复安装脚本忽略非零返回码、结构检查漏查控制台、安装包 src 导入与网页资源缺失。
- 控制台升级到 2.5：有效进度缺少核验凭据时显示“未核验”，不再回退为历史计数。
- 增加历史计数单房间纠错：明确确认、原因、SHA256 原文件备份和追加式审计；更正不会改运行中的 URL 快照，也不会被更正前的旧 progress 计数覆盖。
- 控制台状态连续两次刷新失败后显示断线和最后同步时间；采集进程状态标记为未知，恢复连接后自动刷新。
- 端口已有服务必须版本与代码目录完全匹配才复用；页面版本号仅取自后台。
- 分离清单历史计数、文件凭据复核有效段、归档 manifest 段数；旧计数不再冒充有效进度。
- 网址备份改为按完整 SHA256 去重，重复清单历史按直播 ID 归并；不自动清理不同历史版本。
- 旧设置缺少已知字段时用默认值迁移并备份；未知字段或损坏设置明确报错且保留原件。
- 数字人检测统一保留 `isDigitalAnchorLive` 的 true、false 和缺失三态；只把平台标记全 true 的段视为严格数字人段。
- 汇总 CSV 增加段落与 room 层的全 true、含 false、缺失标记和段数；false 段标记为排除候选并保留原始证据。
- 标题含“虚拟主播”或“智能主播”时按研究口径标记数字人，同时保留平台原始标记。

## [2.0.3] - 2026-09-03

### Fixed
- 修复 CI：移除 Python 3.8 矩阵（DrissionPage/lxml 已不支持 3.8，缺预编译 wheel），最低版本升到 3.9
- 同步 `setup.py` / `setup_quick.py` / `README` 的最低 Python 版本声明为 3.9+

## [2.0.2] - 2026-09-03

### Changed
- 配置集中化：录制时长/轮次/冷却/内存上限/UA 全部进 `src/utils/config.py`，支持 `LIVE_*` 环境变量覆盖
- 爬虫/收集器/检测器统一从 config 读取 Edge 路径与 UA，不再硬编码
- `detect_digital.py` 参数化：支持 `liveId` / `--ids` / `--ids-file` / `--out`，默认输出改到 `sessions/数字人确认.txt`

### Fixed
- 修复 `collect_digital.py` 的 `log` 在使用之后才定义的顺序问题
- 修复 `detect_digital.py` 默认数据目录无视 `LIVE_STUDY_ROOT` 的问题

### Added
- 新增 `tests/test_core.py`（7 用例：JSONP 解析、deep_find、数字人响应解析、配置默认值），CI 跑 `pytest`

## [2.0.1] - 2026-09-03

### Fixed
- 修复 `finalize_room()` 归档路径：优先调用 `scripts/parse_data.py`，兼容旧版 `parse_taobao_data.py`
- 修复 `parse_data.main()` 全量模式：遍历 `_staging/browser_*/room_*`，修复 `process` 未定义问题
- 修复 `src/utils/config.py` 的 `BASE_DIR` 层级，使默认数据/FFmpeg 路径指向项目根目录
- 启动脚本改用 `runpy.run_path` 替代 `exec()`，保留堆栈与 lint 支持

### Changed
- 依赖拆分：`requirements.txt` 仅保留核心依赖（DrissionPage/requests），数据分析依赖移入 `requirements-optional.txt`
- 录制内存保护：`state["collected"]` 保留最近 N 条（`LIVE_MAX_COLLECTED`，默认 800）
- `.gitignore` 补齐 `taobao_cookies_*.txt`、`live_urls.txt`、`*.bak_*`
- `setup.py` 改用 `find_packages(where='src')`，修复 `pip install .` 装空包问题

## [2.0.0] - 2026-09-02

### Added
- 重构项目结构，采用标准Python包布局
- 新增模块化设计：crawler, parser, detector, utils
- 添加统一启动/停止脚本
- 创建完整的中英双语文档
- 添加详细的使用示例
- 集成GitHub Actions CI/CD
- 添加配置模板文件

### Changed
- 优化导入路径，支持作为Python包使用
- 改进日志记录和错误处理
- 统一配置管理，支持环境变量
- 重写README，增强SEO优化

### Fixed
- 修复多实例并发冲突问题
- 改进Cookie管理机制
- 优化内存使用

## [1.0.0] - 2026-08-01

### Added
- 初始版本发布
- 基础爬虫功能
- 数字人检测
- 数据解析和归档

# 2.1 local control panel (2026-10-08)

- Added a loopback-only browser dashboard with editable recording settings, per-instance segment counts, dependency checks, run snapshots, progress and logs.
- Added graceful stop requests, hidden child processes, persistent PID creation-time tracking and per-port locks.
- Removed automatic source deletion and global Edge/FFmpeg termination from the updated crawler/collector.
- Added atomic final metadata, separate retry attempts, complete response journals, FFprobe checks and per-file archive SHA256 manifests.
- Retained synthetic self-checks under `_control/selfchecks`; no production recordings run during validation.

# Local control panel 2.2

- Accept any positive integer archive threshold, including one completed room; reject zero and negative values.
- Show total and per-instance segment progress and current segment elapsed time with progress bars.
- Add independent pause/resume controls that take effect after the current segment and retain cooldown and source files.
- Compute active progress from the run snapshot and show scanning, validation, pause and recording failure states explicitly.
- Respect LIVE_SAVE_COOKIES=0 when saving the cookie JSON after login.
- Existing tasks keep their loaded version; the updated panel and crawler take effect after a restart.

