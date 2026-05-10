# 猎聘寻访 Agent 工作台实施计划

项目目录：`E:\Myproject\liepin-agent-workbench`

## 1. 项目定位

新项目不是旧工作台的功能重排，而是一个以智能体为核心的桌面端寻访系统。

核心目标：

1. 让 Agent 围绕一个岗位 Session 自主完成搜索、观察、抽样、抓详情、匹配、复盘、调整策略。
2. 把旧项目中稳定的猎聘浏览器自动化、简历解析、匹配评分、Excel 导出逻辑迁移过来。
3. 不延续旧项目的多 Tab 流水线 UI，也不延续之前的本地 API 方案。
4. SQLite 作为主存储，Excel 只作为导出物。
5. 每一步 Agent 决策都可见、可解释、可暂停、可恢复。

非目标：

1. MVP 不做外部 HTTP API。
2. MVP 不做自动打招呼。
3. MVP 不做公司调研。
4. MVP 不做多招聘平台。
5. MVP 不做复杂工作流编辑器。
6. MVP 不做多 Agent 系统，先用一个主 Agent 加多个结构化决策节点。

## 2. 总体架构

```text
PySide6 Desktop App
    |
    +-- Session UI
    +-- Agent Timeline
    +-- Candidate Pool
    +-- Strategy Control Panel
    |
    v
AgentRuntime
    |
    +-- Planner          生成第一轮和后续搜索策略
    +-- Observer         分析搜索结果池质量
    +-- CandidatePicker  决定是否抓详情、抓谁、抓多少
    +-- Reviewer         根据匹配结果复盘并决定下一步
    |
    v
Tool Services
    |
    +-- LiepinBrowserService   浏览器生命周期、登录状态、风控检测
    +-- LiepinSearchService    搜索、筛选、翻页、结果卡片解析
    +-- ResumeFetchService     候选人详情抓取
    +-- MatchService           候选人匹配评分
    +-- ExportService          Excel 导出
    |
    v
Runtime Infrastructure
    |
    +-- BrowserQueue     串行执行浏览器动作
    +-- MatchQueue       并发执行 LLM 匹配
    +-- EventBus         UI 和 Agent 状态更新
    +-- SQLiteStore      任务、轮次、候选人、决策、匹配结果
```

关键设计：

1. 浏览器动作必须串行。
2. LLM 匹配必须异步并发，但受限速和超时控制。
3. Agent 决策层可以等待匹配结果，但 UI 和后台任务不能被阻塞。
4. 搜索卡片摘要和完整简历详情必须分开处理。
5. Agent 的每个动作必须落库，重启后可以继续。

## 3. MVP 用户流程

### 3.1 新建寻访任务

用户输入：

1. JD 文本。
2. 可选补充说明：客户偏好、排除公司、目标城市、薪资范围、特殊要求。
3. 搜索预算：最大轮次、最大详情抓取数、单轮抓取上限、最大运行时间。
4. 运行模式：自动、单步、监督。

系统生成：

1. 岗位摘要。
2. 硬性门槛。
3. 可替代条件。
4. 排除项。
5. 第一轮搜索假设。

### 3.2 Agent 运行闭环

```text
理解岗位
-> 生成搜索假设
-> 执行搜索
-> 读取搜索结果卡片摘要
-> 分析搜索结果质量
-> 决定本轮动作
-> 可选抓取候选人详情
-> 异步提交匹配
-> 等待足够匹配结果或继续后台处理
-> 复盘本轮
-> 下一轮搜索或停止
```

### 3.3 输出

MVP 输出：

1. 候选人池。
2. 每位候选人的来源轮次、抓取状态、匹配档位、匹配理由。
3. Agent 搜索复盘。
4. Excel 导出。

## 4. Agent 运行模式

### 4.1 自动模式

Agent 自动执行搜索、观察、抽样、抓详情和匹配。

需要人工确认的动作：

1. 首次打开猎聘浏览器。
2. 登录状态异常。
3. 遇到验证码或风控迹象。
4. 放宽城市到全国。
5. 活跃度放宽到 30 天以上或不限。
6. 单轮计划抓取详情超过阈值，默认 15 人。
7. 总抓取详情超过预算。

### 4.2 单步模式

每一轮搜索后暂停，展示 Agent 观察和建议。

用户可以：

1. 批准本轮详情抓取。
2. 修改关键词。
3. 修改筛选条件。
4. 跳过本轮。
5. 停止任务。

### 4.3 监督模式

Agent 只给策略建议，用户手动确认主要搜索条件。

适合：

1. 新岗位校准。
2. 高价值岗位。
3. 账号风控敏感期。
4. 客户要求非常模糊时。

## 5. 每轮搜索后的分流策略

Agent 不能固定每轮都抓几个详情。每轮搜索后先进入观察与分流。

### 5.1 轮次类型

1. `skip_detail`

   明显错误轮。结果少、职位不对、行业不对、噪音严重。

   动作：不抓详情，直接调整关键词或筛选条件。

2. `sample_detail`

   探测轮。结果有希望但方向不确定。

   动作：抓 2 到 4 个代表性样本。样本要覆盖不同公司、职位、城市、摘要类型。

3. `validate_detail`

   验证轮。卡片质量不错，需要确认详情里是否真的匹配。

   动作：抓 5 到 10 个高预评分候选人，提交匹配，等待足够结果后复盘。

4. `harvest_detail`

   收割轮。该关键词方向已被验证有效。

   动作：扩大抓取量，比如 15 到 30 个，但受总预算、风控和重复率限制。

### 5.2 候选人抽样原则

不能只抓 Top N。应混合三类样本：

1. 高置信样本：卡片上已经明显相关。
2. 多样性样本：覆盖不同公司、职位、城市、年限。
3. 不确定样本：卡片信息不足但有关键潜力词。

抽样目标：

1. 判断这一轮关键词是否真的有效。
2. 避免被前几条候选人误导。
3. 发现隐藏在卡片摘要之外的有效人选。

## 6. 异步匹配设计

匹配调用 LLM API 需要时间，因此必须异步执行。

### 6.1 两条任务队列

```text
BrowserQueue
  并发数：1
  任务：搜索、翻页、应用筛选、打开详情、关闭详情

MatchQueue
  并发数：默认 3，可配置 1 到 8
  任务：候选人快速预检、完整 LLM 匹配
```

### 6.2 Agent 决策栅栏

匹配异步执行，但 Agent 可以设置等待条件。

等待策略：

1. `no_wait`

   收割轮使用。详情抓完后匹配后台继续跑，Agent 可以执行下一轮搜索。

2. `wait_min_results`

   探测轮和验证轮使用。等待至少 N 个匹配结果，或最多等待 T 秒。

   示例：等待至少 5 个结果，或最多等待 180 秒。

3. `wait_all`

   最终汇总使用。等待关键候选人匹配完成后输出结论。

### 6.3 匹配分级

不要所有候选人都直接调用完整 LLM 匹配。

1. 快速预检

   本地规则判断硬门槛、排除词、核心关键词命中、简历有效性。

2. 完整匹配

   对预检通过、预检不确定、或 Agent 指定的候选人调用 LLM。

3. 直接拒绝

   明确命中排除项或简历无效时，不浪费 LLM 调用。

## 7. 状态机设计

### 7.1 Session 状态

```text
draft
-> ready
-> running
-> paused
-> waiting_approval
-> completed
-> failed
-> cancelled
```

### 7.2 Round 状态

```text
planned
-> searching
-> observed
-> detail_decision_made
-> fetching_details
-> matching
-> reviewed
-> skipped
-> failed
```

### 7.3 Candidate 状态

```text
summary_seen
-> pre_scored
-> detail_queued
-> detail_fetching
-> detail_fetched
-> detail_failed
-> quick_checked
-> match_queued
-> matching
-> matched
-> shortlisted
-> rejected
-> deferred
```

`deferred` 很重要。候选人不是不合适，而是当前信息不足、预算不够、需要后续对比。

## 8. UI 布局

新 UI 不采用多功能 Tab，而采用寻访作战室布局。

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 岗位 / 当前阶段 / 浏览器状态 / 轮次 / 已抓详情 / A-B 数 / 耗时 / 暂停继续     │
├───────────────┬─────────────────────────────────────┬────────────────────────┤
│ Session 列表   │ Agent 时间线                         │ 当前策略与控制台        │
│               │                                     │                        │
│ + 新建任务     │ 第1轮搜索                             │ 搜索栏                  │
│ 进行中         │ 观察、统计、决策理由                   │ 职位栏                  │
│ 已暂停         │                                     │ 城市/年限/活跃度         │
│ 历史任务       │ 第2轮抽样                             │ 预算                    │
│               │ 抓取、匹配、复盘                       │ [批准] [暂停] [单步]    │
├───────────────┴─────────────────────────────────────┴────────────────────────┤
│ 候选人池：姓名 / 公司 / 职位 / 城市 / 来源轮次 / 预评分 / 匹配档位 / 状态       │
│ 点击候选人打开右侧详情抽屉：简历、命中证据、风险点、Agent 备注、原始链接         │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 8.1 顶部状态条

展示：

1. 当前岗位。
2. 当前阶段。
3. 浏览器状态。
4. 登录状态。
5. 已搜索轮次。
6. 已见候选人。
7. 已抓详情。
8. A/B 候选人数。
9. 匹配队列进度。
10. 暂停、继续、停止、导出。

### 8.2 左侧 Session 列表

展示：

1. 新建任务按钮。
2. 进行中任务。
3. 暂停任务。
4. 最近完成任务。
5. 搜索框。

每个任务卡片展示：

1. 岗位名。
2. 当前状态。
3. A/B 数量。
4. 最近更新时间。

### 8.3 中间 Agent 时间线

时间线是信任核心。

事件类型：

1. 岗位理解。
2. 搜索计划。
3. 搜索执行。
4. 结果观察。
5. 抓取决策。
6. 详情抓取。
7. 匹配结果。
8. 本轮复盘。
9. 人工确认。
10. 错误与恢复。

每个事件展示：

1. Agent 做了什么。
2. 为什么这么做。
3. 用了哪些数据。
4. 风险是什么。
5. 下一步是什么。

### 8.4 右侧策略与控制台

展示当前轮：

1. 搜索栏 query。
2. 职位栏 position_filter。
3. 匹配范围 scope。
4. 匹配模式 match_mode。
5. 城市、年限、学历、活跃度。
6. 本轮计划动作。
7. 抓详情数量。
8. 等待匹配策略。

控制：

1. 批准本轮。
2. 修改条件。
3. 跳过本轮。
4. 暂停。
5. 停止。
6. 强制复盘。

### 8.5 候选人池

表格列：

1. 姓名。
2. 当前公司。
3. 当前职位。
4. 城市。
5. 年限。
6. 学历。
7. 来源轮次。
8. 卡片预评分。
9. 详情状态。
10. 匹配档位。
11. 推荐状态。
12. 风险标签。

支持筛选：

1. A/B/C/D。
2. 来源轮次。
3. 详情已抓取/未抓取。
4. 当前状态。
5. 公司。
6. 城市。

## 9. 数据库设计

SQLite 是主存储。

### 9.1 `search_sessions`

字段：

1. `id`
2. `title`
3. `jd_text`
4. `user_notes`
5. `status`
6. `mode`
7. `max_rounds`
8. `max_detail_fetches`
9. `max_runtime_minutes`
10. `target_ab_count`
11. `created_at`
12. `updated_at`
13. `started_at`
14. `finished_at`

### 9.2 `match_criteria`

字段：

1. `id`
2. `session_id`
3. `criteria_json`
4. `created_by`
5. `confirmed`
6. `created_at`

### 9.3 `search_rounds`

字段：

1. `id`
2. `session_id`
3. `round_index`
4. `status`
5. `query`
6. `position_filter`
7. `scope`
8. `match_mode`
9. `filters_json`
10. `intent`
11. `round_type`
12. `raw_count`
13. `deduped_count`
14. `prequalified_count`
15. `detail_fetch_count`
16. `matched_count`
17. `ab_count`
18. `started_at`
19. `finished_at`

### 9.4 `candidate_summaries`

字段：

1. `id`
2. `session_id`
3. `round_id`
4. `profile_url`
5. `dedupe_key`
6. `name`
7. `age`
8. `current_title`
9. `current_company`
10. `city`
11. `work_years`
12. `education`
13. `summary_text`
14. `result_index`
15. `pre_score`
16. `pre_score_reasons_json`
17. `status`
18. `created_at`

### 9.5 `candidate_details`

字段：

1. `id`
2. `candidate_id`
3. `resume_text`
4. `resume_summary`
5. `raw_payload_json`
6. `capture_status`
7. `error_message`
8. `fetched_at`

### 9.6 `match_results`

字段：

1. `id`
2. `candidate_id`
3. `session_id`
4. `round_id`
5. `tier`
6. `core_met_count`
7. `core_total`
8. `dealbreaker_hit`
9. `summary`
10. `risks`
11. `recommendation`
12. `detail`
13. `raw_response`
14. `status`
15. `created_at`

### 9.7 `agent_events`

字段：

1. `id`
2. `session_id`
3. `round_id`
4. `event_type`
5. `title`
6. `message`
7. `payload_json`
8. `created_at`

### 9.8 `agent_decisions`

字段：

1. `id`
2. `session_id`
3. `round_id`
4. `decision_type`
5. `action`
6. `input_snapshot_json`
7. `decision_json`
8. `reason`
9. `risk`
10. `created_at`

### 9.9 `task_runs`

字段：

1. `id`
2. `session_id`
3. `round_id`
4. `task_type`
5. `status`
6. `progress_current`
7. `progress_total`
8. `message`
9. `error_message`
10. `started_at`
11. `finished_at`

## 10. Agent 决策结构

所有 Agent 输出必须结构化，不能只输出自然语言。

### 10.1 搜索计划

```json
{
  "action": "run_search",
  "query": "文创 潮玩",
  "position_filter": "产品",
  "scope": "全部经历",
  "match_mode": "all",
  "filters": {
    "city": ["深圳", "广州", "东莞"],
    "work_years": "5年以上",
    "education": "本科",
    "active_days": 7
  },
  "intent": "验证文创潮玩产品方向是否存在可用人选",
  "expected_signal": ["文创产品", "潮玩", "IP衍生品", "从0到1", "量产"],
  "risk": "可能混入内容产品或互联网产品经理"
}
```

### 10.2 观察结论

```json
{
  "round_quality": "medium",
  "raw_count": 42,
  "deduped_count": 37,
  "estimated_relevant_count": 12,
  "noise_patterns": ["互联网内容产品", "纯运营产品"],
  "positive_signals": ["潮玩", "IP衍生品", "文创产品"],
  "recommended_round_type": "validate_detail",
  "reason": "卡片层面有一定目标行业信号，但需要详情确认是否做过产品开发和量产"
}
```

### 10.3 抓取决策

```json
{
  "action": "fetch_details",
  "round_type": "validate_detail",
  "candidate_ids": ["..."],
  "fetch_limit": 8,
  "sampling_strategy": {
    "high_confidence": 5,
    "diversity": 2,
    "uncertain": 1
  },
  "match_wait_policy": {
    "mode": "wait_min_results",
    "min_results": 5,
    "timeout_seconds": 180
  },
  "reason": "需要用详情匹配结果验证关键词方向"
}
```

### 10.4 本轮复盘

```json
{
  "action": "continue",
  "next_query": "IP衍生品 文创衍生品",
  "next_position_filter": "产品",
  "keep_filters": true,
  "summary": "文创潮玩方向有效，但需要进一步收紧到 IP 衍生品，减少互联网内容产品噪音",
  "evidence": {
    "matched_count": 6,
    "a_count": 1,
    "b_count": 3,
    "common_reject_reasons": ["缺少量产经验", "偏内容运营"]
  }
}
```

## 11. 模块结构

建议目录：

```text
liepin-agent-workbench/
  pyproject.toml
  README.md
  docs/
    IMPLEMENTATION_PLAN.md
    PRODUCT_SPEC.md
    MIGRATION_NOTES.md

  app/
    main.py
    bootstrap.py
    ui/
      main_window.py
      session_sidebar.py
      top_status_bar.py
      agent_timeline.py
      strategy_panel.py
      candidate_table.py
      candidate_detail_drawer.py
      settings_dialog.py

  agent/
    runtime.py
    planner.py
    observer.py
    candidate_picker.py
    reviewer.py
    schemas.py
    prompts.py

  services/
    browser_queue.py
    match_queue.py
    event_bus.py
    settings_service.py

  tools/
    liepin_browser.py
    liepin_search.py
    resume_fetcher.py
    resume_extractor.py
    matcher.py
    exporter.py
    llm_client.py

  domain/
    models.py
    states.py
    pre_score.py
    dedupe.py
    stop_conditions.py

  storage/
    sqlite_store.py
    migrations.py
    repositories.py

  tests/
    test_agent_decision_flow.py
    test_candidate_state_machine.py
    test_round_observer.py
    test_match_queue.py
```

## 12. 旧项目迁移清单

从旧项目迁移但需要重构：

1. `src/core/liepin_browser.py`

   迁移为 `tools/liepin_browser.py`。

   保留：

   - 浏览器启动。
   - profile 管理。
   - 登录检测。
   - debug snapshot。

   调整：

   - 去掉 UI 回调。
   - 增加风控/验证码检测接口。
   - 所有浏览器动作通过 BrowserQueue 调用。

2. `src/core/liepin_search_service.py`

   迁移为 `tools/liepin_search.py`。

   保留：

   - 搜索框定位。
   - 筛选器应用。
   - 结果卡片解析。
   - 翻页。
   - 打开详情页。

   拆分：

   - `run_search_round()`：只搜索和返回卡片摘要。
   - `extract_result_cards()`：只解析当前页。
   - `fetch_candidate_detail()`：只抓一个候选人详情。

3. `src/core/liepin_resume_extractor.py`

   迁移为 `tools/resume_extractor.py`。

   调整：

   - 返回结构化 `CandidateDetail`。
   - 对搜索页误跳转、空简历、权限限制给出明确错误类型。

4. `src/core/batch_match_service.py`

   迁移为 `tools/matcher.py`。

   调整：

   - 拆出快速预检。
   - 完整 LLM 匹配支持单候选人任务。
   - 结果实时写 SQLite。

5. `src/core/llm_client.py`

   迁移为 `tools/llm_client.py`。

   调整：

   - 支持结构化 JSON 输出校验。
   - 增加重试、超时、速率限制。

6. `src/core/candidate_excel_service.py`

   迁移为 `tools/exporter.py`。

   调整：

   - 只负责从 SQLite 查询并导出 Excel。
   - 不再作为主数据源。

不迁移：

1. `src/api`
2. `src/core/workflow_facade.py`
3. 旧 `src/ui` 大部分控件
4. 旧 `MainWindow`
5. 旧多 Tab 流程

## 13. 停止条件

硬停止：

1. 达到最大轮次。
2. 达到最大详情抓取数。
3. 达到最大运行时间。
4. 浏览器登录失效。
5. 验证码或风控迹象。
6. 连续浏览器失败超过阈值。
7. 用户停止。

智能停止：

1. A/B 候选人数达到目标。
2. 连续 2 轮 A/B 产出低于阈值。
3. 连续 2 轮重复率过高。
4. 新关键词边际收益明显下降。
5. Agent 判断当前 JD 信息不足，需要人工校准。

默认阈值：

1. 最大轮次：6。
2. 最大详情抓取：50。
3. 单轮详情抓取：15。
4. 最大运行时间：90 分钟。
5. 目标 A/B 数：10。
6. 验证轮等待匹配：至少 5 个结果或最多 180 秒。

## 14. 风险与处理

### 14.1 搜索结果卡片信息不足

处理：

1. 保留不确定样本抽取。
2. 不只依赖卡片预评分。
3. 详情抓取预算中保留 10% 到 20% 给潜在样本。

### 14.2 Agent 过早收敛

处理：

1. 前两轮默认偏探测。
2. 必须记录替代搜索假设。
3. 如果 A/B 数不足，强制尝试至少一个相邻场景词。

### 14.3 LLM 匹配太慢

处理：

1. 异步 MatchQueue。
2. 快速预检减少完整匹配次数。
3. 决策栅栏只等足够结果。
4. 单候选人超时不阻塞整体。

### 14.4 浏览器风控

处理：

1. 搜索和详情抓取加入随机等待。
2. 连续打开详情数量上限。
3. 失败率异常立刻暂停。
4. 检测验证码、登录跳转、异常空白页。

### 14.5 旧代码 selector 复杂

处理：

1. 先原样迁移可用 selector。
2. 建立 debug snapshot。
3. 每个页面动作都输出可诊断错误。
4. UI 提供“导出诊断”按钮。

### 14.6 数据重复

处理：

1. 优先用 profile_url 去重。
2. 无 URL 时用姓名、公司、职位组合。
3. 保留多来源关系，不丢失不同轮次来源。

## 15. 开发阶段

### Phase 0：项目骨架

任务：

1. 创建新项目目录。
2. 建立 PySide6 桌面应用骨架。
3. 建立 SQLite 初始化和 migrations。
4. 建立基础配置文件。
5. 建立日志目录和 debug artifacts 目录。

验收：

1. 可以启动空窗口。
2. 可以创建数据库。
3. 可以保存和读取设置。

### Phase 1：数据模型与 UI 骨架

任务：

1. 实现 Session、Round、Candidate、AgentEvent、MatchResult 模型。
2. 实现 Session 列表。
3. 实现顶部状态条。
4. 实现 Agent 时间线。
5. 实现候选人表格。
6. 实现右侧策略面板。

验收：

1. 可以新建 Session。
2. 可以展示模拟 Agent 事件。
3. 可以展示模拟候选人。
4. UI 不依赖猎聘浏览器也能跑。

### Phase 2：LLM 与 Agent 决策骨架

任务：

1. 迁移 LLMClient。
2. 实现结构化 JSON 输出解析。
3. 实现 Planner。
4. 实现 Observer。
5. 实现 CandidatePicker。
6. 实现 Reviewer。
7. 用模拟搜索结果跑通 Agent 决策闭环。

验收：

1. 输入 JD 后生成第一轮搜索计划。
2. 给一组模拟结果卡片，Agent 能输出 `skip_detail`、`sample_detail`、`validate_detail` 或 `harvest_detail`。
3. 决策事件写入 SQLite 并显示在时间线。

### Phase 3：猎聘搜索卡片接入

任务：

1. 迁移浏览器启动和登录检测。
2. 迁移搜索执行。
3. 迁移筛选条件应用。
4. 迁移结果卡片解析。
5. 实现 `run_search_round()`。

验收：

1. 可以打开猎聘并确认登录状态。
2. 可以执行一轮搜索。
3. 可以只抓搜索结果卡片摘要，不打开详情。
4. 搜索结果写入 `candidate_summaries`。
5. Agent 可以基于真实卡片结果做观察。

### Phase 4：详情抓取与候选人状态机

任务：

1. 迁移详情页打开与关闭。
2. 迁移简历解析。
3. 实现候选人状态流转。
4. 实现详情抓取队列任务。
5. 实现抓取失败分类。

验收：

1. Agent 可以选择候选人抓详情。
2. 抓取结果写入 `candidate_details`。
3. UI 实时更新候选人状态。
4. 抓取失败不会中断整个 Session。

### Phase 5：异步匹配队列

任务：

1. 迁移匹配 prompt 和解析逻辑。
2. 实现快速预检。
3. 实现 MatchQueue。
4. 实现决策栅栏。
5. 实现匹配结果实时落库。

验收：

1. 详情抓取成功后可以自动入匹配队列。
2. 匹配结果逐条更新 UI。
3. Agent 可以等待至少 N 个匹配结果后复盘。
4. 单候选人匹配失败不会阻塞其他候选人。

### Phase 6：完整 Agent 闭环

任务：

1. 串联搜索、观察、抓详情、匹配、复盘、下一轮。
2. 实现停止条件。
3. 实现暂停、继续、单步批准。
4. 实现自动模式和单步模式。
5. 实现 Session 恢复。

验收：

1. 对真实 JD 能跑完整多轮寻访。
2. 每轮决策都有时间线记录。
3. 达到停止条件后自动完成。
4. 程序重启后可以恢复未完成 Session。

### Phase 7：导出与打磨

任务：

1. 实现 Excel 导出。
2. 实现候选人详情抽屉。
3. 实现候选人筛选。
4. 实现 debug snapshot 导出。
5. 完善设置页。

验收：

1. 可导出候选人池。
2. 可查看每个候选人的匹配理由和来源轮次。
3. 可导出诊断信息。
4. MVP 可用于真实岗位试跑。

## 16. 测试计划

单元测试：

1. Agent JSON 解析。
2. 状态机流转。
3. 候选人去重。
4. 卡片预评分。
5. 停止条件。
6. MatchQueue 超时和失败。

集成测试：

1. 模拟搜索结果 -> Agent 决策。
2. 模拟候选人详情 -> 匹配队列。
3. Session 暂停和恢复。
4. Excel 导出。

人工验收：

1. 猎聘登录检测。
2. 搜索框定位。
3. 筛选条件应用。
4. 结果卡片解析。
5. 详情页抓取。
6. 风控异常暂停。

## 17. 配置项

设置页需要支持：

1. LLM API Base URL。
2. API Key。
3. 模型名称。
4. LLM 超时时间。
5. 匹配并发数。
6. 搜索等待范围。
7. 详情抓取等待范围。
8. 最大搜索轮次。
9. 最大详情抓取数。
10. 单轮抓取上限。
11. 默认城市策略。
12. 默认活跃度。
13. 浏览器 profile 路径。
14. 导出目录。

敏感信息：

1. API Key 只保存在本地配置。
2. UI 默认隐藏。
3. 日志不输出完整密钥。

## 18. UI 技术选择

推荐使用 PySide6。

理由：

1. 更适合复杂桌面工作台。
2. 表格、分割面板、详情抽屉、任务状态、右键菜单更成熟。
3. 后续可扩展性比 CustomTkinter 更好。
4. 可以更清晰地组织 Model/View。

不建议继续用旧项目 CustomTkinter UI：

1. 当前旧 UI 已经是功能 Tab 集合。
2. 候选人表格和时间线会继续变复杂。
3. Agent 工作台需要更强的布局和状态管理。

## 19. 第一版开工顺序

建议严格按下面顺序做：

1. 建新项目骨架。
2. 做 SQLite 模型和迁移。
3. 做 UI 空壳和模拟数据。
4. 做 Agent 决策 schema。
5. 用假搜索结果跑通 Agent 闭环。
6. 迁移浏览器和搜索卡片解析。
7. 迁移详情抓取。
8. 迁移匹配队列。
9. 串完整真实闭环。
10. 做导出和打磨。

不要一开始就迁移全部旧代码。先让新项目的骨架和 Agent 交互模型正确，再逐个接入真实工具。

## 20. 最小可用版本验收标准

MVP 完成的定义：

1. 用户能新建一个寻访 Session。
2. Agent 能基于 JD 生成第一轮搜索计划。
3. 系统能执行猎聘搜索并只读取卡片摘要。
4. Agent 能判断本轮搜索质量和轮次类型。
5. Agent 能选择候选人抓详情。
6. 详情抓取后进入异步匹配队列。
7. Agent 能等待足够匹配结果后复盘。
8. Agent 能决定下一轮搜索或停止。
9. UI 能展示完整时间线、候选人池、当前策略和任务状态。
10. 所有数据写入 SQLite。
11. 程序重启后可以查看历史 Session。
12. 可以导出 Excel。

## 21. 后续扩展

MVP 稳定后再考虑：

1. 自动打招呼。
2. 公司调研。
3. 候选人沟通记录。
4. 多招聘平台。
5. Agent 策略模板库。
6. 历史岗位复用。
7. 搜索策略效果排行榜。
8. 人工标注反馈训练。
9. 团队协作和多账号。

## 22. 当前决策

已经确定：

1. 新项目独立放在 `E:\Myproject\liepin-agent-workbench`。
2. 不延续旧 API 方案。
3. 不在旧项目目录继续堆 UI。
4. 新项目以 Agent Session 为中心。
5. SQLite 是主存储。
6. Excel 是导出物。
7. 浏览器任务串行。
8. 匹配任务异步并发。
9. Agent 使用决策栅栏等待足够匹配结果。
10. 搜索结果卡片和详情抓取分层处理。

待进一步确认：

1. 新项目名称是否使用 `liepin-agent-workbench`。
2. MVP 是否直接使用 PySide6。
3. 第一版默认模型和并发配置。
4. 是否保留旧项目的 config.json 格式，方便迁移配置。
