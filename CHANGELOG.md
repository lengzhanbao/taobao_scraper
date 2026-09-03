# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

