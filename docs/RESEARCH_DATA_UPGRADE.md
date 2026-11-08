# 研究数据升级说明（2.8.0）

本文说明 Taobao Scraper 2.8.0 在**不破坏既有录制、归档和恢复能力**的前提下，新增了哪些科研数据能力、这些数据生成在哪里、字段如何理解，以及当前仍存在的限制。

> 本轮修改没有连接真实淘宝直播间做长时间端到端验收。文中所有示例均为**合成/模拟数据**，只用于说明结构；测试结果见 [OPTIMIZATION_REPORT.md](../OPTIMIZATION_REPORT.md)。

---

## 1. 背景与目标

2.7.1 已经具备较完整的多实例录制、完整响应 journal、FFprobe/SHA256 核验、启动对账和归档能力。本轮不再增加采集数量或控制按钮，而是解决三个科研口径问题：

1. **录制成功不等于录满预定时长**：7 分钟视频可以通过旧技术核验，但不能代表 20 分钟目标已达成。
2. **“当前商品”缺少证据**：旧逻辑从最后一次商品回调或商品列表第一项取名称/价格，不能证明该商品就是当时屏幕展示或主播正在讲解的商品。
3. **原始响应与派生数据缺少归属等级**：没有明确 `liveId` 或归属冲突的响应，可能与明确匹配的响应混在同一张统计表中。

升级后的目标是让后续研究能够可靠回答：

- 某段录制在技术上是否可用、是否达到计划时长、是否含可用于 ASR 的音轨；
- 某段录制中，平台接口在什么时间返回了哪些商品 ID、SKU、价格与价格角色；
- 这些商品观测来自哪个 API、哪条响应、何种归属证据；
- 视频/音频/网页接口时间之间目前只能近似对齐还是一时无法对齐；
- 某个直播间最终为什么进入或没有进入样本。

**本轮不做**：不运行语义大模型，不做 ASR 文本与商品口播的匹配，不声称 `current_candidate` 就是“正在讲解”，不重写控制台，不迁移到数据库或消息队列。

---

## 2. 数据层次与文件位置

保留原有的四层关系：

```
直播间 live_id
  → 录制段 recording_id
  → 原始响应 journal（事实来源）
  → 派生事件 / 质量 / 时间证据
  → 会话归档（sessions）
```

### 2.1 每次录制尝试（staging）

路径示例：

```
直播研究数据/_staging/browser_9223/room_123456789/第一段/attempt_20261107_120000_ab12cd34/
```

| 文件 | 含义 | 是否新增 |
|---|---|---|
| `data_<时间>_responses.jsonl` | 完整响应 journal，逐行 JSON；每条含完整正文、序号、接收时间、归属等级、来源 API | 已有，增加字段 |
| `data_<时间>_final.json` | 最终 JSON；schema 2，`responses` 仍内嵌以兼容旧解析器，但采用流式写入 | 已有，schema 升级 |
| `product_events.jsonl` | 商品观测事件（每条一个商品或一个商品结构） | 新增 |
| `product_intervals.jsonl` | 仅在相邻当前商品观测之间推断的候选区间 | 新增 |
| `recording_quality.json` | 录制质量三分解摘要 | 新增 |
| `timing_evidence.json` | 墙钟、媒体 PTS、接口时间及对齐状态 | 新增 |
| `attempt_status.json` | 录制失败时的状态、质量、时间、商品事件摘要 | 已有，扩充 |
| `ffmpeg_attempt<N>.log` / `ffmpeg_attempt<N>_diagnostic.json` | FFmpeg 诊断，保留原文件 | 已有 |

失败尝试同样会尽力生成质量、时间和商品事件文件；**失败不等于没有研究证据**。

### 2.2 会话归档（sessions）

路径示例：

```
直播研究数据/sessions/<直播标题_主播名_liveId>/
```

| 文件 | 含义 |
|---|---|
| `raw/data_*_final.json` | 最终 JSON 归档副本 |
| `video/*.flv` | 视频归档副本 |
| `crawler/lives_summary_*.csv` | 段级汇总 CSV（新增研究字段追加在末尾） |
| `crawler/comments_第N段_*.csv` | 弹幕 CSV，表头保持旧格式 |
| `archive_manifest.json` | 归档凭据；新增 `analysis_files` 与 `analysis_summary` |
| `product_events.jsonl` | 本房间所有段的商品事件合集，含 `recording_id` |
| `product_intervals.jsonl` | 本房间所有段的商品候选区间 |
| `recording_quality.json` | 本房间各段质量与汇总计数 |
| `timing_evidence.json` | 本房间各段时间证据与汇总计数 |

### 2.3 采集尝试台账

| 运行方式 | 文件位置 |
|---|---|
| 控制台启动 | 运行控制目录 `collection_attempts_instance_<N>.jsonl` |
| 独立脚本启动（无 `LIVE_STATUS_FILE`） | `_staging/browser_<端口>/collection_attempts_instance_<N>.jsonl` |

---

## 3. 录制质量：三个独立判断

### 3.1 技术有效性（旧口径保持不变）

技术有效性仍由原有规则决定：

- 文件存在；
- FFprobe 可读；
- 存在视频流；
- 实测时长有限且大于零；
- 达到既有最低时长：`min(420 秒, 0.9 × 目标秒数)`；
- 文件身份与 SHA256 可核验。

`technical_validation` 仍为 **schema 1**，所以旧归档、旧进度凭据和旧测试不需要迁移。

### 3.2 目标时长达成（新增）

| 字段 | 说明 |
|---|---|
| `target_duration_seconds` | `max_minutes × 60`；`max_minutes` 缺失时为空，不猜测 |
| `actual_duration_seconds` | FFprobe 实测秒数 |
| `duration_completion_ratio` | 实际 / 目标 |
| `target_duration_met` | 比率 ≥ `0.9` 时为 `true`，低于为 `false`，目标未知为 `null` |
| `target_duration_threshold` | 当前为 `0.9` |

示例（**模拟数据**）：

```json
{
  "schema_version": 1,
  "quality_state": "technical_valid_target_not_met",
  "technical_valid": true,
  "technical_minimum_seconds": 420.0,
  "target_duration_seconds": 1200.0,
  "actual_duration_seconds": 420.0,
  "duration_completion_ratio": 0.35,
  "target_duration_met": false,
  "target_duration_threshold": 0.9,
  "has_video": true,
  "has_audio": null,
  "video_codec": "h264",
  "audio_codec": null,
  "video_duration_seconds": 420.0,
  "audio_duration_seconds": null,
  "video_width": 720,
  "video_height": 1280,
  "video_fps": 25.0,
  "note": "技术有效、目标时长达成与科研可用性是三个独立判断；缺少音轨或编码事实时保持 unknown，不推断可用性"
}
```

解释：该段**技术有效**，但只完成 20 分钟目标的 35%，`target_duration_met` 为 `false`。它仍然保留，不会被删除，也不会被计为“目标完成”。

### 3.3 音视频事实与科研可用性

`recording_quality.json` 还记录：

- `has_video` / `has_audio`
- `video_codec` / `audio_codec`
- `video_duration_seconds` / `audio_duration_seconds`
- `video_width` / `video_height` / `video_fps`

规则：

1. FFprobe 没有测得的事实使用 `null`，不写成 `false`；
2. `has_audio=false` 是明确事实，不等于“不能做任何研究”，但提示该段不能直接用于依赖音频的 ASR/话语音量分析；
3. 有视频和音轨**不等于**宣布该段适用于所有科研任务。科研可用性应按具体研究问题判断，本项目只提供事实字段。

### 3.4 控制台口径

控制台分别显示，不合并成一个百分比：

- 技术有效段数；
- 目标时长达成段数；
- 缺失音轨段数；
- 完整归档段数；
- 已捕获数字人标记全 true / 非全 true 段数。

目前目标时长与音轨计数来自**进度凭据中可核验的段**。只有归档清单、没有进度凭据的段会显示为未知，不会推断为达成。

---

## 4. 响应归属：原始事实与高可信派生分开

### 4.1 四个等级

| 等级 | 含义 | 是否进入严格派生统计 |
|---|---|---|
| `matched` | 响应内容含当前直播间的明确 `liveId` 证据 | 是 |
| `context_only` | 没有 `liveId` 证据，只能依据当前页面/导航上下文 | 否（保留为候选与原始事实） |
| `conflict` | 多 `liveId`，或 `liveId` 与当前直播间冲突 | 否 |
| `unknown` | 既无 `liveId` 也无导航上下文 | 否 |

旧数据中的 `unscoped` 映射为 `context_only`，读取端同时兼容旧值。原始 journal 不删除任何等级。

### 4.2 派生统计策略

- `mtop.roomstudio.live.detail.get`、商品接口等严格房间统计只接受 `matched`；
- `conflict`、`context_only`、`unknown` 原始响应全部保留；
- 每段 `response_attribution_summary` 记录各等级数量和 API 分布；
- 旧版最终 JSON 若没有 `attribution_status` 字段，读取时仍按原有保守规则处理，不强行重新归属。

示例（**模拟数据**）：

```json
{
  "schema_version": 1,
  "total": 128,
  "by_status": {"matched": 96, "context_only": 21, "conflict": 3, "unknown": 8},
  "high_confidence": 96,
  "missing_status_count": 0,
  "policy": "only matched rows enter strict room statistics; raw rows stay in the journal"
}
```

### 4.3 弹幕口径的当前边界

弹幕接口经常没有明确 `liveId`。为了不擅自改变既有弹幕统计，本版本**没有**把 `context_only` 弹幕直接并入 `comments_第N段_*.csv`；它们仍保留在原始响应中，并在归属摘要里单独计数。这样做牺牲了一部分召回，但避免对历史研究口径造成不可见的跳变。后续若要把 `context_only` 弹幕纳入分析，应作为新口径单独导出，而不是改写旧列。

---

## 5. 商品事件：从“一个当前商品”到带证据的事件序列

### 5.1 已确认的实际接口结构

本版本只解析在真实采集响应中观察到的结构，不假设平台一定返回某个字段：

| API | 结构 | 商品角色 |
|---|---|---|
| `mtop.tblive.live.item.callback.query` | `data.curItemList[]` | 满足 `itemExtData.smallCardItemType` 含 `speak`，或请求参数为 `smallcarditemtype=speakingitem`，且该响应只有一个当前项时，记 `current_candidate`；`publishitem` 或其他情况记 `unknown` |
| `mtop.roomstudio.live.detail.get` | `data.curItemList[]` | 只有一个当前项时记 `current_candidate`（`detail_cur_item_list`） |
| `mtop.tblive.live.item.getvideodetailitemlistwithpagination` | `data.itemListv1[].liveItemDO` | `listed` |

真实响应中观察到的商品字段包括：`itemId`、`itemName`、`itemPrice`、`liveItemPrice`（`promotionPrice`、`publicPromotionPrice`、`singlePromotionPrice`、`promotionPriceDesc`、`skuId`）、`extendVal.tradeParams`、`itemExtData.smallCardItemType`。不同直播间、不同时间可能缺失部分字段，缺失时写 `null`。

### 5.2 事件字段

| 字段 | 说明 |
|---|---|
| `schema_version` | 当前为 1 |
| `event_id` | 确定性事件标识（录制 + 响应序号 + API + 路径 + 下标） |
| `run_id` / `recording_id` / `live_id` | 运行、录制段与直播间 |
| `live_id_basis` | `matched_response` / `navigation_context` / `unattributed` |
| `navigation_id` | 页面导航上下文 |
| `response_sequence` | 该响应在本次扫描中的序号 |
| `source_api` | 来源 API |
| `received_at` / `event_received_at` | 接口响应被监听到的墙钟时间（秒）；两字段同值，后者便于按事件语义读取 |
| `event_relative_seconds` | 相对录制起始墙钟的秒数 |
| `item_id` / `sku_id` / `item_name` | 商品身份；`item_id` 保留原始字符串，不用名称代替 ID |
| `item_role` | `current_candidate` / `listed` / `unknown`（本轮不生成 `current_confirmed`） |
| `role_evidence` | 角色判定依据，如 `callback_speaking_item_marker`、`detail_cur_item_list` |
| `display_price` | `itemPrice` 对应金额（CNY） |
| `original_price` | 未观察到明确“原价”字段时保持 `null` |
| `promotion_price` | `liveItemPrice.promotionPrice`（按分转换为元） |
| `promotion_price_desc` | 如“入会价” |
| `price_unit` | `CNY` 或空 |
| `price_observations[]` | 每个价格字段一条：`role`、`raw_value`、`value`、`unit`、`unit_basis`、`evidence_path` |
| `attribution_status` | `matched` 或 `context_only`；冲突与未知不进入事件表 |
| `evidence_path` | 如 `data.curItemList[0]` |
| `raw_reference` | 如 `response:108` |
| `first_observed_at` / `last_observed_at` | 单次观测事件的接收时间 |
| `observation_source` | API + 结构路径 |

### 5.3 价格角色与单位依据

- `itemPrice` 按元记录，依据是既有解析器一直把该字段当元处理；
- `promotionPrice`、`publicPromotionPrice`、`singlePromotionPrice` 按分记录后除以 100；
- `original_price` 只在存在明确原价字段时填写；当前观察中没有这样的字段，因此保持 `null`；
- 不在没有证据时把 `publicPromotionPrice` 或 `singlePromotionPrice` 强行解释为“促销价”或“专享价”，它们以独立 `role` 保留。

示例（**模拟数据**）：

```json
{
  "schema_version": 1,
  "event_id": "rec-1:108:mtop.tblive.live.item.callback.query:data.curItemList[0]:0",
  "run_id": "run-1",
  "recording_id": "rec-1",
  "live_id": "123456789",
  "live_id_basis": "matched_response",
  "navigation_id": "run-1:123456789:1760000000000000000",
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
    {"role": "item_price", "raw_value": "239", "value": 239.0, "unit": "CNY",
     "unit_basis": "item_price_as_yuan", "evidence_path": "data.curItemList[0].itemPrice"},
    {"role": "promotion_price", "raw_value": "18327", "value": 183.27, "unit": "CNY",
     "unit_basis": "live_item_price_in_cents",
     "evidence_path": "data.curItemList[0].liveItemPrice.promotionPrice", "desc": "入会价"}
  ],
  "attribution_status": "matched",
  "attribution_basis": "response_live_id_match",
  "observed_live_id": null,
  "evidence_path": "data.curItemList[0]",
  "raw_reference": "response:108",
  "first_observed_at": 1760000105.5,
  "last_observed_at": 1760000105.5,
  "observation_source": "mtop.tblive.live.item.callback.query@data.curItemList[0]"
}
```

### 5.4 不默认“第一个商品就是当前商品”

规则：

1. 不把 `curItemList[0]` 自动当成当前讲解商品；
2. 商品回调先看请求/响应中的 `speakingitem` 证据；真实历史响应中存在 `publishitem` 回调，这类响应只记为 `unknown`，不冒充当前讲解商品；
3. 同一响应出现多个当前项时，全部降级为 `unknown`，避免制造“唯一当前商品”；
4. `videodetail` 列表项只记 `listed`，不使用列表第一项填充“当前商品名称/价格”；
5. 旧版最终 JSON 若完全没有可识别商品结构，才保留旧提取逻辑作为兼容兜底；
6. 同一商品在不同时间重复出现时，保留为多条观测，不合并不同时间的价格与出现位置。

扫描阶段会先复制扫描 journal 到录制 journal，用于房间上下文和流地址。扫描阶段发生的商品响应仍保留在原始 `responses` 中，但 `product_events.jsonl` 只收录录制开始后（`event_relative_seconds >= 0`）的观测；解析归档时也会再次校验商品事件的 `recording_id` 与 `live_id`，避免把其他房间或扫描上下文的商品当成本录制段证据。

### 5.5 商品区间

`product_intervals.jsonl` 只在两个条件同时满足时生成：

- 两个事件都是 `matched`；
- 事件角色是 `current_candidate`。

区间定义为前一次当前商品观测到下一次不同当前商品观测之间的时间，例如：

```json
{
  "schema_version": 1,
  "recording_id": "rec-1",
  "live_id": "123456789",
  "item_id": "881114573816",
  "sku_id": "5994544129612",
  "item_name": "示例商品",
  "item_role": "current_candidate",
  "start_seconds": 105.5,
  "end_seconds": 300.0,
  "start_event_id": "rec-1:108:...:data.curItemList[0]:0",
  "end_event_id": "rec-1:120:...:data.curItemList[0]:0",
  "attribution_status": "matched",
  "basis": "consecutive_current_item_observations",
  "note": "区间由前后两次当前商品观测界定；不代表屏幕每一秒都已核验"
}
```

限制：

- 只有一次观测时不生成区间，不伪造持续时间；
- 最后一个当前商品不会自动延伸到录制结束；
- 区间是**推断候选**，不是平台原生事件，也不是“主播正在讲解”的证明。

### 5.6 可支持的科研统计

- 每段捕获多少条商品相关响应；
- 每段发现多少个不同商品 ID / SKU；
- 多少商品事件有明确直播间归属；
- 多少商品事件有价格、多少有促销价、多少有多个价格观测；
- 多少商品只有列表证据；
- 商品事件在录制时间轴上的分布；
- 同一商品在不同时间是否重复出现。

这些统计可以由 `product_events.jsonl` 直接计算。**本轮不生成口播与商品的匹配结论**；ASR 接入后应使用事件时间、商品 ID 与证据路径另行研究。

### 5.7 平台身份字段

汇总 CSV 新增原始平台标识列：

| 列 | 说明 |
|---|---|
| `平台liveId` | 当前房间的 liveId |
| `平台shopId` | 平台返回的 shopId；缺失留空 |
| `平台accountId` | 平台返回的 accountId；缺失留空 |
| `平台bizCode` | 平台业务类型；缺失留空 |

旧列 `店铺ID` 为兼容历史分析保留，仍按旧的 `shopId or accountId` 回退。新的研究分析应优先使用 `平台shopId` / `平台accountId`，不要把回退值当成同一身份。采集器不会把不同 `liveId` 合并成 `room_group`；如论文需要按店铺或账号分组，应在分析阶段另建映射表并记录来源。

---

## 6. 时间证据：墙钟、媒体时间、接口时间分开

### 6.1 四种时间

| 时间 | 来源 | 用途 |
|---|---|---|
| 墙钟时间 | FFmpeg 启动、页面刷新、响应接收 | 运行时间线 |
| 媒体时间 | FFprobe 的 PTS/时长 | 视频/音频内部时间 |
| 响应接收时间 | DrissionPage 监听回调的墙钟 | 商品事件相对时间 |
| 事件推断时间 | 由相邻观测推断的商品区间 | 候选分析，不是平台时间 |

它们不能直接当成同一时钟。尤其：**接口接收时间不等于商品实际开始展示时间。**

### 6.2 `timing_evidence.json`

| 字段 | 说明 |
|---|---|
| `record_start_wall_time` / `_iso` | FFmpeg 本次录制尝试启动墙钟 |
| `record_end_wall_time` / `_iso` | 录制结束墙钟 |
| `first_video_data_wall_time` / `_iso` | 首次观测到视频文件的墙钟 |
| `video_duration_seconds` | FFprobe 实测时长 |
| `audio_duration_seconds` | 音频流时长；无音轨为 `null` |
| `first_video_pts` / `first_audio_pts` | 首包 PTS（秒）；无法读取为 `null` |
| `time_basis` | 当前为 `wall_clock_from_ffmpeg_launch` |
| `media_time_basis` | 当前为 `ffprobe_pts_seconds` |
| `alignment_status` | `approximate` 或 `unresolved` |
| `alignment_uncertainty_ms` | 无法估计时为 `null`，不伪造误差 |
| `wall_clock_media_window_delta_seconds` | 墙钟窗口与媒体时长的差值 |
| `mapping` | 候选公式与 `delta_seconds=null`，明确未估计时延 |
| `events[]` | FFmpeg 尝试、首次数据、页面刷新及失败事件 |

示例（**模拟数据**）：

```json
{
  "schema_version": 1,
  "recording_id": "rec-1",
  "live_id": "123456789",
  "record_start_wall_time": 1760000000.0,
  "record_start_wall_time_iso": "2026-10-09T04:53:20.000Z",
  "record_end_wall_time": 1760000600.0,
  "first_video_data_wall_time": 1760000002.0,
  "video_duration_seconds": 600.0,
  "audio_duration_seconds": 599.8,
  "first_video_pts": 0.0,
  "first_audio_pts": 0.04,
  "time_basis": "wall_clock_from_ffmpeg_launch",
  "alignment_status": "approximate",
  "alignment_uncertainty_ms": null,
  "mapping": {"formula": "t_media ≈ t_wall - record_start_wall_time - delta",
              "delta_seconds": null, "delta_source": "not_estimated"},
  "events": [
    {"kind": "ffmpeg_attempt_started", "attempt": 1, "wall_time": 1760000000.0},
    {"kind": "first_video_data", "attempt": 1, "wall_time": 1760000002.0,
     "record_relative_seconds": 2.0},
    {"kind": "page_refresh", "wall_time": 1760000300.0, "record_relative_seconds": 300.0}
  ]
}
```

### 6.3 对齐状态

- `approximate`：有录制起点、首次数据时间和实测媒体时长，可以做近似映射；仍不声称毫秒级准确；
- `unresolved`：缺少必要时间点，不能建立映射；
- 当前不输出虚假的 `alignment_uncertainty_ms`，也不把 `delta` 默认为 0。

### 6.4 页面刷新与 FFmpeg 重试

页面刷新、FFmpeg 接流失败、重连、录制中断都会进入 `events[]`。不同尝试的视频文件分别保留，系统不会自动拼接成“无缺口连续视频”。

---

## 7. 采集尝试台账

`collection_attempts_instance_<N>.jsonl` 每次尝试一行，字段包括：

| 字段 | 说明 |
|---|---|
| `run_id` / `instance_id` / `live_id` | 运行、实例、直播间 |
| `attempt_id` | 单次尝试标识 |
| `stage` | `scan` / `recording` |
| `decision` | 见下表 |
| `failure_reason` | 具体原因代码 |
| `attempt_start_time` / `attempt_end_time` | UTC ISO 时间；另含 epoch 字段 |
| `retry_count` | 该房间截至本次的失败次数 |
| `recording_id` / `segment_index` | 关联录制段 |
| `recording_duration_seconds` | 实测时长 |
| `target_duration_met` / `quality_state` | 质量口径 |
| `detail` | 扫描结果、错误摘要、音轨与商品事件数等 |

当前决策值：

| `decision` | 含义 |
|---|---|
| `recorded` | 成功录制并写入最终 JSON |
| `skipped_not_eligible` | 本来就不符合样本条件（平台标记 false、数字人标记缺失） |
| `failed_scan` | 网络、验证码、日志写入、归属冲突/缺失、无法取得流等 |
| `failed_recording` | 接流、断流、时长核验或最终写入失败 |
| `stopped` / `paused` | 主动停止或暂停，未完成本次尝试 |

这张表用于解释**科研样本如何形成**，不能替代原始响应与媒体证据。

控制台新增“最近采集尝试”表，读取该台账并显示最近 20 条：时间、实例、直播 ID、扫描/录制阶段、结果、中文失败原因、重试次数、录制段和目标达成状态。它用于快速判断“房间不符合条件”与“采集/网络失败”的区别；原始数据仍在 staging 与 journal 中。

---

## 8. 内存与磁盘

### 8.1 journal 是完整事实来源

- 监听阶段把完整正文写入 `data_*_responses.jsonl`；
- 内存中 `state["collected"]` 仍只保留有界预览（默认 800 条），用于状态、扫描和兼容逻辑；
- 正文预览上限仍为 800,000 字符，并记录 `body_truncated`、`body_original_chars`、`preview_truncated`。

### 8.2 最终 JSON 流式写入

2.8.0 的最终 JSON 仍然内嵌 `responses`（避免破坏现有解析器与历史流程），但写入方式改为：

1. 先流式扫描 journal，累计数字人标记、归属计数、商品事件、质量与时间事实；
2. 再逐行把已编码的响应原文写入最终 JSON 的 `responses` 数组；
3. 不把所有响应同时构造成 Python 对象再 `json.dump`，峰值内存只与单行大小相关。

新增元数据：

```json
{
  "schema_version": 2,
  "responses_storage": "jsonl",
  "responses_journal": ".../data_20261107_120000_responses.jsonl",
  "responses_journal_relative": "data_20261107_120000_responses.jsonl",
  "captured_count": 128,
  "journal_sha256": "…64 位…",
  "responses_embedded": true
}
```

解析器优先使用内嵌 `responses`；若为 `null`/缺失且 journal 路径存在，则回退读取 journal。

### 8.3 派生文件

- 商品事件、区间、质量、时间文件按尝试生成；
- 会话级派生文件在归档校验通过后写入；
- 若同名派生文件内容不同，先复制一份 `.bak_<随机>` 再替换，不静默覆盖不同内容；
- 原始 journal、最终 JSON、视频、弹幕不因生成派生文件而被删除或改写。

---

## 9. 与已有数据格式的兼容方式

| 旧格式/行为 | 2.8.0 处理 |
|---|---|
| 最终 JSON 内嵌 `responses` | 继续内嵌；新增 schema 2 元数据 |
| `technical_validation` schema 1 | 保持不变，旧凭据继续通过 `validation_matches` |
| `attribution_status="unscoped"` | 读取时映射为 `context_only`；原字段不改写 |
| 没有 `attribution_status` 的历史响应 | 按原有 `live_ids`/`observed_live_id` 规则处理 |
| 旧归档 `archive_manifest.json` schema 2 | 继续可核验；新增 `analysis_files` 为附加字段 |
| 旧 `responses` 内嵌 JSON | 继续解析；没有新质量字段时按现有事实推导，缺失为 unknown |
| 商品列 `当前商品名称/价格` | 新数据优先取 `current_candidate`；无候选时不填列表第一项，旧结构完全无法识别时才走兼容兜底 |
| 弹幕 CSV 表头 | 保持 `用户/内容/时间` 不变 |

**没有删除或改写历史真实数据。** 本轮开发使用的仓库 checkout 中 `直播研究数据/sessions` 为空；测试全部写入临时目录或 `_control/_selfchecks` 合成目录。

---

## 10. 当前限制

1. **未做真实淘宝长时端到端验证**：本轮只有离线/合成测试，不能把测试通过表述为真实采集已验证。
2. **商品角色是候选**：`current_candidate` 仍不是“主播正在讲解”的证明；`current_confirmed` 本轮不生成。
3. **口播与商品未匹配**：没有 ASR 文本、语义模型或屏幕视觉证据的联合判断。
4. **价格角色覆盖有限**：仅覆盖实际观察到的 `itemPrice` 与 `liveItemPrice` 内若干字段；其他价格字段缺失时保持 null，不猜测。
5. **SKU 未枚举**：只记录响应中出现的 SKU，不保证覆盖商品全部 SKU。
6. **时间对齐是近似**：`delta` 未估计，`alignment_uncertainty_ms` 为 null；页面刷新和 FFmpeg 重连已保留为事件，但未做媒体级重建。
7. **弹幕 `context_only` 未并入 CSV**：为保护历史口径，当前只在归属摘要中计数；如需新口径应单独导出。
8. **归档派生文件未纳入 `inspect_archive` 的逐文件复核**：manifest 已记录其 SHA256，但现有归档完整性核验仍聚焦 JSON/视频/弹幕/汇总。
9. **控制台质量计数依赖进度凭据**：只有归档清单的房间不会推断目标达成或音轨状态。
10. **旧恢复路径不补写质量字段**：启动对账生成的凭据可能没有质量字段，控制台显示 unknown，而不是猜测。
11. **旧 `店铺ID` 列仍保留回退逻辑**：新列 `平台shopId` / `平台accountId` 已分开保存，但继续使用旧列的历史脚本仍可能把 accountId 当作店铺 ID。
12. **兼容期仍有磁盘重复**：完整响应在 journal 与最终 JSON 各存一份；本轮只优化了写入内存，尚未切换为“仅摘要 + journal 引用”。
13. **采集与归档尚未解耦**：仍沿用既有 staging 批量归档队列，不是可独立重跑的采集/归档任务系统。

---

## 11. 后续建议

1. **一次真实小规模验收**：按明确验收标准（扫描、接流、录制、final JSON、归档、商品事件、质量/时间文件）跑一次真实短时采集，并记录失败原因分布。
2. **把 `context_only` 弹幕作为独立新口径导出**：新增文件而不是改旧 CSV，便于与历史统计对照；同时抽查商品事件与屏幕/人工标注。
3. **建立用户侧时间关联接口**：`product_events.jsonl` 已提供 `received_at`、`event_relative_seconds`，`timing_evidence.json` 已提供近似映射；ASR 与语义匹配由用户自行处理，采集器不声称已完成口播关联。

以上建议尚未实现，不应在论文中描述为已完成能力。
