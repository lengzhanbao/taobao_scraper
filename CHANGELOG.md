# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- 2.6.1：完整响应写入 staging 扫描 journal 与段 journal，内存预览保留 800000 字符上限并记录截断元数据；最终 JSON 仍内嵌完整 responses。
- 统一监听与扫描共享状态锁，拒绝 liveId 不匹配的迟到直播详情；验证码改为有限时人工等待，控制台显示等待状态。
- 控制台操作按钮增加处理中反馈；监听异常增加短退避。

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

## [2.7.1] - 2026-10-22

### Changed
- 重整本地控制台的信息层级与响应式样式：首屏突出可信进度和五实例状态，实例表提前展示，设置与运行记录更易浏览；未改变采集行为或计数口径。

## [2.7.0] - 2026-10-22

### Added
- 可配置常用与高级采集参数，支持实例覆盖、配置 JSON 预览/导出、默认值恢复及每次运行设置快照。
- 面板分开显示当前运行实际参数与下次启动参数；控制台最终 JSON 核验缓存只保存轻量摘要并继续按 SHA256 与文件身份复核。
- 自然完成时收尾归档达到满段阈值的尾批；主动停止不启动新归档并保留待处理项。面板展示扫描结果、落盘状态与待归档信息。
- 响应按 `liveId` 与 `liveUrl` 作用域配对并记录归属元数据；多 `liveId` 存在歧义时保守跳过派生数据，同时保留原始响应证据。
- 增加 journal 写入锁与落盘屏障、真实落盘健康状态，并可从完整 journal 重建时间线。

### Changed
- 设置保存后仅供下次启动使用；面板状态刷新间隔为纯 UI 设置。最终 JSON 仍内嵌完整 `responses`，尚未实现全商品分页、数小时独立采集或彻底有界的 final JSON 内存方案。

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

