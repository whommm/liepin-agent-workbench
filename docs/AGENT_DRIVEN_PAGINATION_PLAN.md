# Agent 驱动续翻分页实现计划

> 状态：待实施（设计已评审，三条关键决策已确认）
> 日期：2026-07-17

## 背景

当前搜索翻页由本地启发式策略 `AdaptivePaginationPolicy`
（`liepin_agent/core/search/_models.py:82`）直接决定，LLM 完全不参与：

- 至少翻 `search_min_pages_per_round`（默认 3）页才允许早停；
- 连续 `search_low_yield_page_patience`（默认 2）页"低收益"即提前停止；
- 硬上限 `search_max_pages_per_round`（默认 10，`config.py` 校验 `le=10` 且
  `_models.py:99` 写死 `min(10, ...)`）；
- 当 `min_pages == max_pages`（如当前线上配置都为 3）时自适应逻辑被完全架空，
  每轮固定翻 3 页，产出再好也会被"达到分页硬上限"砍掉。

此外，逐页产出统计存于 `LiepinSearchService.last_pagination_stats`
（`_executor_mixin.py:93`），但没有任何消费方——agent 拿不到"每页收获如何"
的反馈，只能看到拍平后的候选人列表。

**目标**：把"翻多少页"的决策权交还给 agent——搜索在检查点暂停并返回逐页
统计，由 LLM 基于真实产出决定"再翻 N 页"或"停止"，浏览器从原游标续翻。

## 已确认的设计决策

1. **检查点节奏**：不做固定"每 K 页问一次"，由 brain 每次申报"再翻 N 页"
   （一次决策调用答一个数，LLM 调用最少）。首个检查点固定为第 3 页。
2. **安全帽保留**：10 页硬上限不变，作为账号风控的最后防线；brain 申报的
   页数一律钳制到 `硬上限 - 当前页码` 以内。
3. **低收益策略降级为信号**：`AdaptivePaginationPolicy` 不再直接终止分页，
   其逐页判定（低收益、重复率、潜力密度）作为统计特征喂给 brain 参考。

## 方案选型回顾（为什么是这个形态）

- **放弃 C2（LLM 逐页内联决策）**：`assess` 在浏览器 worker 线程内同步执行，
  内联 LLM 调用会阻塞浏览器线程并与 `BrowserQueue` 的 180s 超时
  （`services/browser_queue.py:45`）耦合；且每页一次调用对
  `llm_rpm_limit=5` 的配额压力过大。
- **采用检查点式续翻**：LLM 决策发生在 browser 队列之外的 runtime 线程，
  与现有 `observe_round`/`decide_fetch`/`review_round` 的穿插模式完全一致
  （`runtime.py:391→465→525`）；两次浏览器任务之间页面自然保持，
  游标无需特殊保活。

## 可行性要点（已验证）

- 浏览器为常驻持久化 context + 单 worker 线程
  （`core/liepin_browser.py:207`），两次 `browser_queue.run` 之间页面不动；
  详情抓取开独立标签页、用完关闭并 `set_active_page` 切回
  （`core/search/_detail_mixin.py:53,87,285`），不干扰搜索页。
- 页码是从 DOM 实时读取的（`_pagination_mixin.py:248`），续翻不依赖内存
  中的页码假设。
- 注意：翻页后 URL 为 `h.liepin.com/search/getConditionItem#session`，
  **页码与搜索上下文均在 SPA 内存中，URL 不含 `curPage`**——这决定了
  恢复策略只能"重建搜索 + 点击推进"，不能直接 URL 跳页。
- `_persist_round_candidates`（`runtime.py:963`）是无状态逐候选人打分入库，
  分批调用安全。

## 详细改动

### 1. `SearchCursor` 游标对象（`core/search/_models.py`）

新增内存态 dataclass（不落库、不跨进程）：

```python
@dataclass
class SearchCursor:
    query: str
    filters: Dict[str, Any]
    match_mode: str
    scope: str
    position_filter: str
    page_num: int                     # 当前停留的结果页码
    seen_keys: Set[str]               # 跨批次去重键
    history: List[PageYieldStats]     # 逐页产出统计
    total_results: Optional[int]      # 来自 page_meta
    exhausted: bool = False           # 已无下一页
```

由 runtime 持有并在工具调用间传递（工具保持无状态，便于测试）。
进程重启/会话恢复时游标直接丢弃：本轮按已入库候选收尾即可，
`known_candidate_keys` 重放保证不会重复入库。

### 2. `search()` 检查点与续翻（`core/search/_executor_mixin.py`）

- 新增参数 `checkpoint_pages: int = 3`：初始搜索翻满 `min(checkpoint_pages,
  page_cap)` 页后返回（现有逐页提取、去重、统计逻辑不变）。
- `AdaptivePaginationPolicy.assess` 的结果不再 `break`，改为记录到
  `PageYieldStats`（或并行日志字段）作为信号。
- 导出 `SearchCursor` 随结果返回。
- 新方法 `resume_pagination(cursor, additional_pages, ...)`：
  1. `run_with_page` 内先校验游标：URL 仍是搜索工作区
     （`_is_search_page_url`）且 DOM 页码 == `cursor.page_num`；
  2. 校验失败走恢复（见第 3 节）；
  3. 校验通过则从当前页继续翻 `additional_pages` 页，复用同一套
     提取/去重/统计逻辑，`seen_keys`、`history` 累计更新。
- `search()` 与 `resume_pagination` 的内部循环抽成共享的
  `_paginate_from_current_page(...)`，避免两份翻页逻辑。

### 3. 游标恢复（`core/search/`）

恢复仅在"两批之间 SPA 状态丢失"（刷新/崩溃/会话过期）时触发，正常间隙
只有几秒（一次 LLM 调用），属小概率路径：

1. **校验**：URL 是搜索页 + DOM 页码符合预期 + 可选的卡片指纹（当前页
   首张卡片 dedupe key 在 `seen_keys` 中）。
2. **恢复**：用 `cursor` 中的 query/filters/match_mode/scope/position_filter
   重新执行 `_execute_search` + `_apply_filters_on_page`（均为现有原语），
   然后连续 `go_to_next_result_page()` 推进到 `cursor.page_num`
   （页码不在 URL 里，只能点击推进；推进步数设上限）。
3. **兜底**：恢复失败抛 `SearchCursorLostError`，runtime 捕获后记录事件并
   按已入库批次收尾本轮，不视为会话错误。

### 4. `RealLiepinTool` 接口（`tools/real_liepin.py`）

- `run_search_round` 返回值改为 `SearchRoundResult`（新 dataclass：
  `candidates: List[CandidateSummary]`、`cursor: SearchCursor`、
  `page_stats: List[dict]`）。`last_pagination_stats` 由此正式成为返回值
  的一部分（目前被丢弃）。
- 新增 `continue_search_round(cursor, additional_pages) -> SearchRoundResult`，
  调用 `resume_pagination` 并做同样的 `CandidateSummary` 映射。
- 候选卡分类器（`_pagination_classifier`）逻辑保留，其输出继续写入
  `PageYieldStats.potential_count/validate_count` 作为 brain 信号。

### 5. `brain.decide_pagination`（`agent/brain.py` + `prompts/txt/`）

- 新增 prompt `prompts/txt/decide_pagination.txt`（参照 `observe_round.txt`
  的加载方式，在 `prompts/loader.py` 注册）。
- 输入（紧凑 JSON）：plan 摘要、criteria 核心词/负向词、逐页
  `PageYieldStats`、`total_results`/`has_next_page`、本批新增卡片的抽样
  （标题/公司/城市，上限 ~10 条）、当前页码与硬上限。
- 输出 JSON：`{"action": "continue"|"stop", "additional_pages": int,
  "reason": str}`。
- 钳制：`additional_pages ∈ [1, 硬上限 - cursor.page_num]`；超出按边界取。
- `RuleBasedAgentBrain` 兜底实现：最新页 `new_unique >= min_new_unique` 且
  重复率低于阈值则 `continue` 2 页，否则 `stop`。
- LLM 调用失败走现有 `_record_fallback` 模式回退到规则 brain，**不直接停**。

### 6. Runtime 批间循环（`agent/runtime.py`）

在 `_persist_round_candidates` 之后、`observe_round` 之前插入：

```
result = run_search_round(...)
persist(result.candidates)
while not result.cursor.exhausted and result.cursor.page_num < hard_cap:
    decision = brain.decide_pagination(...)
    emit PAGINATION_DECISION event
    if decision.action == "stop": break
    batch_pages = min(decision.additional_pages, 每批上限)  # 见配置
    try:
        result = continue_search_round(result.cursor, batch_pages)
    except SearchCursorLostError:
        emit event; break
    persist(result.candidates)
# observe_round 拿到全量候选人，后续流程不变
```

- 决策未消费完的 `additional_pages` 余量带入下一次循环继续扣减。
- 每批之间调用 `_respect_control_flags`——取消/暂停粒度比现状更细，
  中途取消时已入库批次不受影响。
- 轮次统计（`raw_count`/`deduped_count`）按全量更新；`observe_round`
  与 `decide_fetch` 的输入为全量候选，逻辑不变。
- 新增 `AgentEventType.PAGINATION_DECISION`（`domain/states.py:56`），
  事件载荷含 brain 的 reason 与逐页统计，UI 时间线展示；
  批次进度复用 `SEARCH_EXECUTED` 事件。

### 7. 配置（`core/config.py`）

新增：

- `search_agent_pagination_enabled: bool = True`——总开关，`False` 回退到
  现有 `AdaptivePaginationPolicy` 直接决策的旧路径（回滚保险）。
- `search_pagination_checkpoint_pages: int = 3`（首个检查点页数）。
- `search_pagination_batch_max_pages: int = 3`（单个浏览器任务最多翻几页，
  控制 180s 超时与取消响应）。

保留：

- `search_max_pages_per_round`（默认 10）语义不变，仍是硬帽。

语义变更（向后兼容读取，不再直接驱动行为）：

- `search_min_pages_per_round` → 由 `search_pagination_checkpoint_pages` 取代；
- `search_low_yield_page_patience` / `search_min_new_unique_per_page` /
  `search_min_promising_per_page` / `search_duplicate_rate_threshold` →
  仅作为信号计算参数与规则兜底参数使用。

### 8. 测试

- `tests/test_adaptive_pagination.py` 现有假页面 harness 扩展：
  - 初始批次在检查点返回并导出 cursor；
  - `resume_pagination` 正常续翻、累计去重；
  - 游标校验失败 → 重建搜索 + 点击推进恢复；
  - 恢复失败 → `SearchCursorLostError`。
- brain：`decide_pagination` 的 JSON 解析、页数钳制、规则兜底、LLM 失败回退。
- runtime：假 tool + 假 brain 验证多批循环（continue→continue→stop）、
  累计入库、游标丢失收尾、`search_agent_pagination_enabled=False` 旧路径。
- 回归：`uv run python -m pytest` 不引入新失败
  （`tests/test_resilience.py`、`tests/test_excel_greeting.py` 存在已知的
  master 上既有失败/不稳定用例，不作为阻塞）。

## 实施顺序

1. `SearchCursor` + `search()` 检查点/续翻/恢复 + 单测（核心，独立可验）。
2. `RealLiepinTool` 返回契约改造 + runtime 批间循环 + 单测。
3. `brain.decide_pagination` + prompt + 规则兜底 + 单测。
4. 配置接线 + 事件类型 + 本文档同步更新。
5. 全量回归 + 真实账号手工验证一轮（重点观察：续翻事件、游标恢复、
   10 页安全帽触发）。

## 风险与回滚

| 风险 | 应对 |
|---|---|
| 两批之间 SPA 状态丢失 | 校验 → 重建搜索+点击推进 → 失败收尾（第 3 节） |
| 长暂停后猎聘会话过期 | 同上，resume 校验捕获 |
| LLM 决策成本 | 每轮 +1~3 次小 prompt 调用；决策 prompt 紧凑、抽样上限 |
| 行为回退 | `search_agent_pagination_enabled=False` 恢复旧策略路径 |

## 明确不做（本期）

- C2 式逐页内联 LLM 决策（见"方案选型回顾"）。
- 跨轮次/跨进程续翻游标持久化。
- 放开 10 页硬帽（如未来需要，单独评审风控影响）。
