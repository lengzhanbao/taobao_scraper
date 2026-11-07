# Taobao Scraper 2.8.0 优化报告

- **项目**：`taobao_scraper`（本地 checkout：`E:\直播爬取_codex_isdigital_flags`）
- **基准**：`main` / 2.7.1 / `37ef7f032da53be897a9e6feeb8f975676bf75b8`
- **本轮分支**：`feature/research-data-upgrade`
- **版本**：2.7.1 → 2.8.0
- **报告日期**：2026-11-07
- **推送状态**：未推送远程；仅本地提交。

> 本轮没有连接真实淘宝直播间做长时间端到端采集。所有测试数据均为合成数据；不能把离线测试通过表述为真实淘宝长时采集已验证。

---

## 1. 修改目标与实际结果

本轮不是重写爬虫，也不是重做控制台。完成的重点是：

1. 把“录制成功”“技术有效”“目标时长达成”“含音轨”“完整归档”拆成独立事实；
2. 把商品数据从“一个当前商品名称/价格”升级为带来源、时间、价格角色和归属证据的事件序列；
3. 让缺失 `liveId`、归属冲突或无法归属的响应与明确匹配响应分开；
4. 为视频、音频、网页接口时间建立可追溯但**不伪造精度**的时间证据；
5. 把失败尝试也变成可分析的样本形成记录；
6. 在不破坏旧 JSON 和原始数据的前提下降低最终写入的峰值内存。

未完成：商品与真实口播/ASR 的语义匹配、`current_confirmed` 判定、真实长时端到端验收、数据库化。

---

## 2. 修改前后对照

| 项目 | 修改前 | 修改后 | 具体文件 | 状态 |
|---|---|---|---|---|
| 录制质量 | 只有技术有效/无效；20 分钟目标下 7 分钟视频也可通过技术核验，且没有独立的目标达成指标 | 技术有效性保持旧口径；新增目标时长、实际时长、达成率、90% 达成阈值，三者分开 | `src/utils/segment_evidence.py`、`src/crawler/recording.py`、`src/parser/parse_data.py` | 已完成 |
| 音视频事实 | 只检查视频流与时长；不保证存在可用的音频流 | 新增 `has_audio`、音视频编码、音频时长、分辨率、帧率、首包 PTS；缺失为 `null` | `src/utils/safe_io.py`、`src/utils/segment_evidence.py` | 已完成 |
| 商品数据 | 从最后一次 `item.callback.query` 的 `curItemList[0]` 或列表第一项取名称/价格 | 新增 `product_events.jsonl`：商品 ID、SKU、名称、角色、价格角色/单位依据、来源 API、响应序号、接收时间、归属等级、证据路径；不默认第一项为当前商品 | `src/parser/product_events.py`、`src/crawler/recording.py`、`src/parser/parse_data.py` | 已完成 |
| 商品时间结构 | `product_timeline` 主要基于 `live.detail.get`；单次观测可能延伸到录制结束 | 新增 `product_intervals.jsonl`：只在两次当前商品候选观测之间生成区间；单次观测不生成区间 | `src/parser/product_events.py`、`src/crawler/recording.py` | 已完成 |
| 响应归属 | `matched` / `conflict` / `unscoped` / `unknown`；派生表可能接受 `unknown` | `matched` / `context_only` / `conflict` / `unknown`；旧 `unscoped` 读取时映射为 `context_only`；严格派生只接受 `matched` | `src/crawler/response_attribution.py`、`src/crawler/taobao_crawler.py`、`src/parser/parse_data.py` | 已完成 |
| 时间对齐 | 有录制墙钟与 FFprobe 时长，但没有独立时间证据文件；没有区分媒体 PTS 与接口接收时间 | 新增 `timing_evidence.json`：墙钟、媒体 PTS、接口时间基准、页面刷新/FFmpeg 尝试事件、`approximate`/`unresolved` 与 `null` 不确定性 | `src/utils/segment_evidence.py`、`src/crawler/recording.py`、`src/parser/parse_data.py` | 已完成 |
| 失败样本 | 日志有失败原因，但没有统一可分析台账 | 新增 `collection_attempts_instance_<N>.jsonl`，区分未开播/非数字人/网络/验证码/接流/录制/核验失败并关联 `recording_id` | `src/crawler/attempt_log.py`、`src/crawler/taobao_crawler.py` | 已完成 |
| 内存/磁盘 | 最终 JSON 把 journal 全部读入 Python 列表后再 `json.dump`，峰值约为全部响应的对象内存 | 最终 JSON 仍内嵌 `responses`（兼容），但按 journal 逐行流式写入；新增 `responses_journal`、`captured_count`、`journal_sha256` | `src/utils/safe_io.py`、`src/crawler/recording.py` | 已完成（兼容模式） |
| 归档输出 | 归档 manifest 只有 JSON/视频/弹幕/汇总 | 新增会话级商品事件、区间、质量、时间文件，并写入 `analysis_files` / `analysis_summary` | `src/parser/parse_data.py` | 已完成 |
| 旧数字人工具 | `collect_digital.py` 仍把响应正文截断到 800,000 字符 | 保留完整正文并记录 `body_truncated=false` / `body_original_chars` | `src/detector/collect_digital.py` | 已完成 |
| 控制台 | 只有技术有效、完整归档、数字人标记计数 | 增加目标时长达成段数、缺失音轨段数；不与技术有效段混算 | `src/control/server.py`、`src/control/web/*` | 已完成 |
| 旧格式兼容 | 无统一兼容说明 | 旧最终 JSON 可解析；`technical_validation` 仍 schema 1；旧 `unscoped` 可读；差异派生文件先备份 | 多个文件 | 已完成 |

---

## 3. 修改的文件与原因

### 3.1 新增文件

| 文件 | 原因 |
|---|---|
| `src/utils/api_names.py` | 统一 API 名提取与商品 API 识别，避免解析器和商品事件重复实现 |
| `src/parser/product_events.py` | 商品事件、价格角色、归属过滤与区间推断的唯一实现 |
| `src/crawler/attempt_log.py` | 采集尝试台账的单一写入器与失败分类 |
| `tests/test_research_upgrade.py` | 本轮新增行为的回归测试（质量、归属、商品、时间、台账、流式 JSON、解析归档） |
| `docs/RESEARCH_DATA_UPGRADE.md` | 面向研究使用者的数据说明、字段解释、路径与限制 |

### 3.2 修改的核心源码

| 文件 | 改动 |
|---|---|
| `src/utils/safe_io.py` | 新增 `probe_media`、`probe_first_packet`、`iter_jsonl`、`atomic_jsonl`、`atomic_json_with_raw_array`；保留原有 `probe_video` 行为与异常语义 |
| `src/utils/segment_evidence.py` | 新增 `target_duration`、`recording_quality`、`timing_evidence`；`video_validation` 可选附加媒体事实但不改变 schema 1；数字人观测改为单遍累加器 |
| `src/crawler/response_attribution.py` | 新增 `context_only`、旧值映射、`summarize_attribution`、`attribution_basis`；归属判断优先明确 `liveId`，仅页面上下文不再等同房间身份 |
| `src/crawler/recording.py` | journal 单遍提取质量/时间/商品/归属事实；生成四个研究文件；最终 JSON 流式写入；成功与失败都写 `last_record_outcome` |
| `src/crawler/taobao_crawler.py` | 监听响应增加 `source_api` 与兼容状态字段；接入采集台账；回执增加质量字段；`validation_receipt` 记录质量摘要 |
| `src/parser/parse_data.py` | 严格归属过滤；读取 journal 回退；按段重算质量/时间/商品；增加 CSV 研究字段；写会话级研究文件和 manifest 分析摘要 |
| `src/control/server.py` | 从进度凭据提取质量摘要，分别汇总目标达成/未达成/音轨缺失/未知；不改变技术有效计数 |
| `src/control/web/app.js`、`index.html`、`style.css` | 新增两个独立质量卡片；保留技术有效与完整归档口径；兼容 2.7/2.8 设置界面 |
| `finalize_7min_segments.py` | 历史候选 final 也写质量/时间证据，仍只在独立报告目录生成，不改原 staging |
| `src/detector/collect_digital.py` | 取消 800,000 字符静默截断，统一使用完整正文与元数据 |
| `setup.py`、`src/__init__.py`、`README.md`、`CHANGELOG.md` | 版本、说明与变更记录 |

### 3.3 测试文件修改

| 文件 | 改动 |
|---|---|
| `tests/test_core.py` | 归属判断期望改为 `context_only`；为 AST 抽取的监听函数补充新依赖 |
| `tests/test_capture_runtime.py` | 同上，并补充无导航上下文时为 `unknown` |
| `tests/test_control_simulation.py` | 录制成功/失败测试增加流式最终 JSON、研究文件与失败状态断言；AST 命名空间补充台账/质量/API 依赖 |
| `tests/test_research_upgrade.py` | 新增 12 个测试，覆盖本轮新增行为 |

---

## 4. 已实际实现的能力

### 4.1 录制质量

- `technical_valid` 沿用旧最低时长规则：`min(420, 0.9 × max_minutes × 60)`；
- `target_duration_met` 独立按 90% 目标判断；
- `has_audio`、编码、分辨率、帧率、音频时长、首包 PTS 均为实测事实，缺失为 `null`；
- 失败段的 `attempt_status.json` 也保存质量与时间摘要。

### 4.2 商品事件

- 解析实际观察到的三个 API 结构；
- 记录 `itemId`、`skuId`、`itemName`、价格角色与单位依据；
- 同一响应多个当前项降级为 `unknown`；
- 列表项只记 `listed`；
- 商品观测保留来源 API、响应序号、接收时间、归属等级、证据路径；
- 单次观测不生成持续时间，最后一项不延伸到录制结束。

### 4.3 响应归属

- 原始响应全部保留；
- 严格房间统计只接受 `matched`；
- `context_only`、`conflict`、`unknown` 不进入严格派生；
- 旧 `unscoped` 与无归属字段的历史数据按保守规则读取。

### 4.4 时间证据

- 墙钟、媒体 PTS、接口接收时间分别记录；
- 页面刷新、FFmpeg 尝试、首次视频数据进入 `events[]`；
- `delta` 未估计时保持 `null`，不宣称精确对齐。

### 4.5 采集尝试台账

- 每次扫描/录制尝试一行；
- 能区分“本来不符合条件”和“网络/登录/接流/核验失败”；
- 成功记录关联 `recording_id`、时长与目标达成状态。

### 4.6 内存优化

- journal 仍是完整原文来源；
- 最终 JSON `responses` 仍内嵌，保持旧解析器兼容；
- 写入时逐行复制 journal，避免同时持有全部响应对象；
- 解析器支持 `responses` 缺失时回退 journal。

---

## 5. 数据结构示例（模拟数据）

> 以下均为合成数据，只说明字段结构，不代表真实平台返回。

### 5.1 最终 JSON（节选）

```json
{
  "schema_version": 2,
  "run_id": "run-1",
  "recording_id": "rec-1",
  "live_id": "123456789",
  "segment_index": 1,
  "record_start_t": 1760000000.0,
  "record_end_t": 1760000600.0,
  "video_duration_seconds": 600.0,
  "max_minutes": 20,
  "captured_count": 128,
  "responses_storage": "jsonl",
  "responses_journal": ".../data_20261107_120000_responses.jsonl",
  "responses_journal_relative": "data_20261107_120000_responses.jsonl",
  "journal_sha256": "64-hex",
  "responses_embedded": true,
  "recording_quality": {
    "technical_valid": true,
    "target_duration_seconds": 1200.0,
    "actual_duration_seconds": 600.0,
    "duration_completion_ratio": 0.5,
    "target_duration_met": false,
    "has_audio": true
  },
  "timing_evidence": {
    "alignment_status": "approximate",
    "alignment_uncertainty_ms": null,
    "first_video_pts": 0.0
  },
  "response_attribution_summary": {
    "by_status": {"matched": 96, "context_only": 21, "conflict": 3, "unknown": 8}
  },
  "product_event_summary": {
    "event_count": 17,
    "distinct_item_ids": 6,
    "interval_count": 2
  },
  "responses": [
    {"response_sequence": 1, "url": "...", "body": "...", "attribution_status": "matched"}
  ]
}
```

### 5.2 商品事件（节选）

```json
{
  "schema_version": 1,
  "event_id": "rec-1:108:mtop.tblive.live.item.callback.query:data.curItemList[0]:0",
  "run_id": "run-1",
  "recording_id": "rec-1",
  "live_id": "123456789",
  "live_id_basis": "matched_response",
  "response_sequence": 108,
  "source_api": "mtop.tblive.live.item.callback.query",
  "received_at": 1760000105.5,
  "event_relative_seconds": 105.5,
  "item_id": "881114573816",
  "sku_id": "5994544129612",
  "item_name": "示例商品",
  "item_role": "current_candidate",
  "role_evidence": "callback_speaking_item_marker",
  "display_price": 239.0,
  "original_price": null,
  "promotion_price": 183.27,
  "promotion_price_desc": "入会价",
  "price_unit": "CNY",
  "price_observations": [
    {"role": "item_price", "raw_value": "239", "value": 239.0,
     "unit_basis": "item_price_as_yuan",
     "evidence_path": "data.curItemList[0].itemPrice"},
    {"role": "promotion_price", "raw_value": "18327", "value": 183.27,
     "unit_basis": "live_item_price_in_cents",
     "evidence_path": "data.curItemList[0].liveItemPrice.promotionPrice"}
  ],
  "attribution_status": "matched",
  "evidence_path": "data.curItemList[0]",
  "raw_reference": "response:108"
}
```

### 5.3 采集尝试台账（节选）

```json
{
  "schema_version": 1,
  "run_id": "run-1",
  "instance_id": 1,
  "live_id": "123456789",
  "attempt_id": "8f0c...",
  "stage": "recording",
  "decision": "failed_recording",
  "failure_reason": "stream_stalled",
  "attempt_start_time": "2026-11-07T04:00:00.000Z",
  "attempt_end_time": "2026-11-07T04:12:00.000Z",
  "retry_count": 1,
  "recording_id": "rec-1",
  "segment_index": 1,
  "target_duration_met": false,
  "quality_state": "technical_valid_target_not_met"
}
```

---

## 6. 兼容性与数据保护

- **未修改真实科研数据**：本轮 checkout 的 `直播研究数据/sessions` 为空；所有测试写入临时目录或 `_control/_selfchecks` 合成目录；未运行真实采集。
- **不删除原始文件**：视频、JSON、JSONL、CSV、弹幕和历史日志保持原状；失败录制也不清理。
- **旧最终 JSON**：没有 `recording_quality`、`timing_evidence`、商品事件字段时仍可解析；质量与时间按现有事实重新推导，缺失保持 unknown。
- **旧技术凭据**：`technical_validation` 仍 schema 1，`validation_matches` 规则未变。
- **旧归属值**：`unscoped` 读取时映射为 `context_only`；原字段不改写。
- **旧归档 manifest**：schema 2 仍可被 `inspect_archive` 核验；新增分析文件是附加字段。
- **派生文件覆盖**：会话级 `product_events.jsonl`、`recording_quality.json` 等同名文件内容不同时，先复制 `.bak_<随机>` 再替换。
- **统计口径**：技术有效段计数未改变；新增目标达成与音轨计数单独显示；`context_only` 弹幕没有直接并入旧弹幕 CSV，避免历史口径跳变。

---

## 7. 测试记录

### 7.1 基线

```text
python -m pytest tests/ -q
98 passed, 3 subtests passed in 184.46s
```

### 7.2 本轮修改后

```text
python -m pytest tests/ -q
110 passed, 3 subtests passed in 88.11s
```

新增 12 个测试，覆盖：

- 20 分钟收益下的目标达成率与 7 分钟“技术有效但目标未达成”；
- 无音轨与媒体事实 unknown；
- 墙钟/媒体 PTS/对齐状态；
- `matched` / `context_only` / `conflict` / `unknown` 与旧 `unscoped` 映射；
- 多商品、重复商品、价格角色、缺少 `itemId`、列表项与当前项区分；
- 单次观测不生成区间；
- 严格派生过滤与旧数据兼容；
- 采集台账；
- 流式最终 JSON；
- `parse_data` 生成会话级研究文件并保留旧 JSON 可读性；
- 控制台质量计数分离。

### 7.3 前端与语法

```text
node --check src/control/web/app.js
（通过）

node tests/test_control_ui.js
Dashboard checks passed: verified-only progress, disconnected/recovered state, version,
pause/resume, URLs.

python -m py_compile <本轮修改的 Python 文件>
（通过）
```

### 7.4 未执行的测试

- 未运行真实淘宝直播长时间采集；
- 未运行真实浏览器 + FFmpeg 的真实接流测试；
- 未进行真实 ASR 与商品口播匹配（本轮明确不做）。

---

## 8. 未解决的问题

按影响排序：

1. **真实环境长时验收缺失**：目前只能证明离线行为；真实直播间可能暴露 FFmpeg 重连、页面刷新、接口字段变化等新问题。
2. **商品与口播仍未关联**：`product_events` 只提供可连接的时间与身份证据，不产生“主播说了哪件商品”的结论。
3. **`current_candidate` 不是“正在讲解”**：回调的 speaking 标记仍可能滞后或误报；本版本不生成 `current_confirmed`。
4. **时间对齐仍是近似**：没有估计 `delta`，没有用音视频指纹重建真实偏移。
5. **弹幕 `context_only` 未并入旧 CSV**：这是有意的兼容选择，但会降低弹幕召回；后续应以新口径单独导出。
6. **归档分析文件未纳入逐文件复核**：manifest 记录 SHA256，但 `inspect_archive` 仍只核验原四类文件。
7. **价格与 SKU 覆盖有限**：只记录响应中出现的字段，不枚举全部 SKU，不推断未出现的原价。
8. **恢复路径不补质量字段**：对账生成的进度凭据可能缺少质量字段，控制台显示 unknown。

---

## 9. Git 结果

- 当前分支：`feature/research-data-upgrade`
- 代码/文档提交：
  - `8be5092e` — `feat: add recording quality and attribution evidence foundations`
  - `d4d6d5e4` — `feat: record product events, timing evidence and collection attempts`
  - `5b91e1ed` — `feat: export research artifacts and separate quality metrics in the panel`
- 本报告提交：见 `git log -1`。
- 是否推送远程：**否**。
- 工作区：提交后应为 clean（`git status --porcelain` 无输出）。
- 未执行 `git reset --hard`、`git clean -fd`，未改动历史真实数据。

---

## 10. 下一步最值得做的三件事

1. **做一次有明确验收标准的真实小规模运行**：从扫描、接流、录制、final JSON、商品事件、质量/时间文件到归档，逐项核对；记录失败原因分布，不追求批量。
2. **把 `context_only` 弹幕与新时间证据接入 ASR**：先做独立导出与新口径对照，确认时间映射误差，再讨论商品与口播关联。
3. **补齐归档派生文件核验**：让 `inspect_archive` 能核验 `analysis_files` 的 SHA256 与内容结构，并把质量/时间摘要纳入归档完整性判断。
