# 猎聘寻访 Agent 设计优化计划

## 1. 背景

当前项目已经跑通了「JD -> Agent 搜索计划 -> 猎聘搜索 -> 卡片观察 -> 抓详情 -> LLM 匹配 -> 复盘下一轮」的闭环，但从真实试跑和代码审视来看，它还不应该继续朝「全自动黑盒判断」方向推进。

我们发现的核心问题：

1. 预设分数会制造假的确定性。
   - 本地试跑中，一个销售岗跑了 5 轮、读取 92 张候选卡、抓取 9 份详情，最终只有 1 个 B。
   - 所有轮次的 `prequalified_count` 都是 0。
   - 直接原因之一是 `DEFAULT_NEGATIVE_TERMS` 把「销售」设为默认噪音词，但该岗位本身就是销售岗，导致正向岗位词被误伤。
2. AI 一次性输出复杂匹配条件容易抓偏重点。
   - 条件越多，越容易把补充偏好、噪音规避、搜索词、硬性要求混在一起。
   - 后续搜索和匹配会围绕错误理解持续放大。
3. 当前 UI 主要展示 Agent 结果，但缺少真正的人工校准入口。
   - 人类无法在 Agent 开始前确认「它到底按什么标准找人」。
   - 单步/监督模式目前偏轮次暂停，不是岗位理解层面的确认。
4. 匹配结果仍偏结论型。
   - A/B/C/D 可以保留，但不应该是核心。
   - 更重要的是命中证据、缺口、风险、待确认问题。

因此，本轮优化目标是把系统从 score-driven 改为 calibration-driven / evidence-driven。

## 2. 新设计原则

1. AI 只起草，人类确认后才能行动。
2. 匹配基准保持简洁，只保留人真正需要看的内容。
3. 不再把「分数」作为产品判断依据。
4. 所有候选人判断必须回到已确认的匹配词与岗位要求。
5. 每次人工修改都要版本化，方便追溯哪一轮开始使用哪版标准。
6. Agent 的价值是减少翻页、试词、读简历、整理证据和复盘成本，而不是替代最终判断。

## 2.1 前期诊断问题对照

本计划必须覆盖前期项目审视中发现的 6 个设计问题，不能只落成一个 UI 确认框。

| 前期问题 | 本计划中的处理方式 | 落地优先级 |
| --- | --- | --- |
| 预筛规则误伤岗位本身 | 去除分数产品化，卡片阶段改为岗位感知的 `fetch / maybe / noise` 判断；负面词来自人工确认的岗位要求和排除描述，不再全局固定 | Phase 3，但销售岗误伤需要提前修 |
| 单链路搜索容易空转 | 增加搜索假设组合：核心行业词、目标公司/竞品词、相邻可迁移场景词；每轮绑定搜索假设，而不是只记录 query | Phase 2/4 |
| `no_wait` 实际仍阻塞 | 修正 `_wait_for_policy()`，让收割轮提交匹配后立即进入下一轮，后台结果持续回写 | Phase 2 |
| 缺少人工 30 秒校准入口 | 新增「匹配词与岗位要求」人工确认；后续增加重新校准、修改搜索条件、批准抓取名单 | Phase 1/4 |
| 候选人多来源被丢失 | 新增 `candidate_sources`，保存每次命中的轮次、query、排名、卡片信号 | Phase 4 |
| 匹配结果不是证据包 | 匹配输出改为 evidence / missing / risks / questions，不再只依赖 A/B/C/D | Phase 2 |

此外，效率度量和合规边界也要进入设计，不应等到产品后期才补。

## 3. 新增模块：匹配词与岗位要求

模块名称建议：

**匹配词与岗位要求**

也可以在 UI 上叫：

**寻访基准**

该模块只包含两块内容。

### 3.1 岗位关键技能 / 背景词

形式：可编辑多行文本或 tag 列表。

用途：

1. 生成搜索关键词候选。
2. 判断候选人卡片是否值得抓详情。
3. 在简历详情中寻找证据。

示例：

```text
LNG
BOG
螺杆压缩机
天然气
油气设备
项目型销售
大客户销售
```

设计约束：

1. 不要混入「学历」「年龄」「沟通能力」等泛条件，除非它们是岗位最关键背景。
2. 不要让 AI 生成过多词，默认 5 到 12 个足够。
3. 支持人工增删改。

### 3.2 岗位要求描述

形式：可编辑自然语言文本。

用途：

1. 作为 LLM 匹配时的主判断标准。
2. 作为 Agent 复盘时判断搜索方向是否有效的依据。
3. 作为导出和候选人解释的上下文。

示例：

```text
候选人需要有天然气、LNG 或相关油气设备销售经验。优先考虑接触过压缩机、BOG 增压、制冷或流体机械设备的人。重点看是否具备项目型大客户销售经验、行业客户资源和从线索到成交的完整销售闭环。
```

设计约束：

1. 不做复杂权重。
2. 不拆成十几个硬性/软性字段。
3. 描述应短、清晰、可由人快速审阅。

## 4. 新任务流程

目标流程：

```text
新建任务
-> 粘贴 JD 和补充说明
-> AI 生成「匹配词与岗位要求」草案
-> 人工编辑
-> 点击「确认寻访基准」
-> Agent 才开始搜索
-> 后续搜索、抓详情、匹配和复盘都基于已确认版本
```

如果中途发现方向错了：

```text
点击「重新校准」
-> 修改匹配词与岗位要求
-> 保存为新版本
-> Agent 从下一轮开始使用新版本
```

## 5. 状态机调整

当前 Session 状态包含：

```text
draft -> ready -> running -> paused -> waiting_approval -> completed
```

建议调整为：

```text
draft
-> criteria_draft
-> criteria_confirmed
-> running
-> paused
-> waiting_approval
-> completed
-> failed
-> cancelled
```

状态含义：

1. `draft`：任务刚创建，还未生成匹配基准。
2. `criteria_draft`：AI 已生成草案，等待人工确认。
3. `criteria_confirmed`：人工已确认，可以开始寻访。
4. `running`：Agent 正在运行。

运行限制：

1. `criteria_draft` 不能启动搜索。
2. 只有 `criteria_confirmed`、`paused`、`waiting_approval` 可以继续执行。
3. 如果用户编辑了已确认基准但未重新确认，任务应回到 `criteria_draft` 或显示「有未确认修改」。

## 6. 数据模型调整

新增表：`match_criteria_versions`

建议字段：

```text
id
session_id
version
status                 draft / confirmed / archived
keywords_text          人类可编辑的关键词文本
requirements_text      人类可编辑的岗位要求描述
source_jd_text         生成该版本时使用的 JD 快照
source_user_notes      生成该版本时使用的补充说明快照
ai_raw_response_json   AI 原始输出，方便排错
created_by             ai / human
confirmed_by           human / empty
created_at
confirmed_at
```

现有 `match_criteria` 可以保留兼容，或者迁移为该表的第一版。

建议不要再把复杂 JSON 作为主要编辑对象。JSON 可以作为内部派生物，但 UI 主体必须是人类可读文本。

`search_rounds` 建议新增：

```text
criteria_version_id
```

用于追踪每轮搜索使用的是哪版寻访基准。

`match_results` 建议新增：

```text
criteria_version_id
evidence_json
unknowns_json
questions_json
confidence
```

## 7. 去除分数驱动

### 7.1 卡片阶段

当前：

```text
pre_score: 0-100
prequalified_count: pre_score >= 65
```

建议改为：

```text
card_decision: fetch / maybe / noise
card_signals_json: 命中的关键词或背景信号
card_risks_json: 明显噪音或排除原因
card_reason: 简短原因
```

UI 展示：

1. `值得抓详情`
2. `信息不足`
3. `明显噪音`

不要在 UI 主路径展示 0-100 分。

### 7.2 详情匹配阶段

A/B/C/D 可以保留，但不作为唯一判断。

更重要的输出：

```json
{
  "tier": "B",
  "matched_evidence": [
    {
      "criterion": "LNG / 天然气背景",
      "evidence": "简历中出现：负责 LNG 加气站项目客户开发",
      "strength": "strong"
    }
  ],
  "missing_or_unclear": [
    "未看到 BOG 增压或压缩机设备销售经验"
  ],
  "risks": [
    "最近一段经历更偏泛能源销售，设备深度需确认"
  ],
  "questions_to_verify": [
    "是否实际卖过天然气压缩机或相关流体设备？",
    "是否有油气客户资源？"
  ],
  "recommendation": "建议沟通确认设备经验"
}
```

## 8. AI Prompt 调整

### 8.1 岗位基准生成 Prompt

AI 只输出：

```json
{
  "keywords_text": "每行一个关键词",
  "requirements_text": "一段简洁岗位要求描述"
}
```

要求：

1. 不输出评分权重。
2. 不输出复杂硬性/软性结构。
3. 不输出超过 12 个关键词。
4. 不把 JD 中所有词都搬出来。
5. 只提取对寻访和匹配真正有用的关键技能、行业、业务、产品、客户或场景背景。

### 8.2 搜索计划 Prompt

输入改为：

1. 已确认关键词。
2. 已确认岗位要求描述。
3. 已用 query。
4. 上轮搜索结果观察。

禁止 Agent 自己发明新的岗位要求。

允许 Agent 做的事：

1. 组合关键词。
2. 放宽或收紧搜索词。
3. 解释当前搜索假设。
4. 推荐是否需要人工重新校准。

### 8.3 匹配 Prompt

输入改为：

1. 已确认关键词。
2. 已确认岗位要求描述。
3. 候选人简历文本。

输出必须引用证据，不允许只给结论。

## 9. UI 改造

### 9.1 新建任务后增加确认页

新建任务弹窗仍然收集：

1. 任务名称。
2. JD。
3. 补充说明。
4. 预算。

创建后不立刻运行，而是在右侧或中心区域显示：

```text
匹配词与岗位要求

[岗位关键技能 / 背景词编辑框]
[岗位要求描述编辑框]

[重新生成草案] [确认寻访基准并开始]
```

### 9.2 任务列表状态展示

新增状态文案：

1. `待确认基准`
2. `已确认，待开始`
3. `运行中`
4. `等待人工确认`

### 9.3 候选人表格调整

把当前「预评分」列改为：

```text
卡片判断
```

可显示：

1. 值得抓详情
2. 信息不足
3. 明显噪音

详情区显示：

1. 命中证据。
2. 缺口。
3. 风险。
4. 待确认问题。
5. 原始简历文本。

### 9.4 策略面板调整

当前策略面板应显示当前使用的寻访基准版本：

```text
寻访基准 v2
关键词：...
岗位要求：...
本轮搜索假设：...
```

并提供：

```text
[重新校准]
```

## 10. Runtime 调整

### 10.1 启动前基准检查

`AgentRuntime.start_session()` 或 UI 的 `continue_session()` 需要检查：

1. 是否存在 confirmed criteria version。
2. 不存在则先生成草案并等待确认。
3. 不允许直接进入 `run_search_round`。

### 10.2 轮次绑定基准版本

创建 round 时写入当前 confirmed `criteria_version_id`。

后续：

1. 搜索计划使用该版本。
2. 卡片观察使用该版本。
3. 详情匹配使用该版本。
4. 复盘使用该版本。

### 10.3 修正 `no_wait`

当前 `_wait_for_policy()` 中 `no_wait` 实际等同于等全部结果。

目标行为：

```text
no_wait: 提交匹配后立即返回
wait_min_results: 等至少 N 个或超时
wait_all: 等全部或超时
```

这项可以和寻访基准模块分开做，但属于效率优化优先项。

## 11. 搜索假设组合

当前 Agent 的搜索策略主要体现为「上一轮 query -> 下一轮 query」。这比人工翻页强，但仍然容易空转，因为它没有明确区分自己正在验证哪种寻访路径。

建议引入搜索假设模型。

### 11.1 假设类型

每个岗位至少维护三类假设：

1. `core_background`

   核心行业、业务、产品、技术或客户场景。

   示例：`LNG / BOG / 螺杆压缩机 / 天然气设备`

2. `target_company`

   目标公司、竞品公司、上下游公司、相似客户群。

   示例：`开山 / 阿特拉斯 / 艾默生 / 油气设备厂商`

3. `transferable_scene`

   可迁移场景，不是完全同岗，但可能有相似客户、设备、销售模式或技术背景。

   示例：`制冷设备 / 流体机械 / 工业设备项目销售`

### 11.2 每轮搜索绑定假设

`search_rounds` 建议新增：

```text
search_hypothesis_type
search_hypothesis_text
```

每轮搜索必须说清楚：

1. 本轮验证哪条假设。
2. 为什么这个假设值得试。
3. 本轮成功信号是什么。
4. 本轮失败后应该放弃、收紧还是换路线。

这样复盘时不是简单说「换关键词」，而是能判断哪条寻访路径有效。

### 11.3 策略效果统计

每条假设累计统计：

```text
round_count
raw_candidate_count
detail_fetch_count
ab_count
noise_count
duplicate_count
last_used_at
agent_summary
```

UI 后续可以展示：

```text
核心行业词：2 轮，A/B 1，人选少但质量较高
目标公司词：1 轮，结果太窄
可迁移场景：2 轮，候选多但需要人工判断迁移风险
```

## 12. 候选人多来源历史

当前 `candidate_summaries` 对同一 `session_id + dedupe_key` 做唯一约束，重复候选人会直接返回旧 id。这能防止重复入库，但会丢掉「这个人被哪些搜索策略反复命中」的信号。

建议新增表：`candidate_sources`

字段：

```text
id
candidate_id
session_id
round_id
criteria_version_id
query
position_filter
search_hypothesis_type
search_hypothesis_text
result_index
card_decision
card_signals_json
card_risks_json
created_at
```

设计价值：

1. 同一个人多次被不同 query 命中，本身是强信号。
2. 可以追踪哪个搜索假设最容易发现有效候选人。
3. 导出时能告诉人「此候选人来自哪些搜索路径」。
4. 未来做历史岗位复用和候选人再发现时有基础数据。

保存逻辑：

1. `candidate_summaries` 继续负责候选人主档。
2. 每次搜索命中都写入 `candidate_sources`。
3. 如果候选人已存在，只新增 source，不覆盖旧候选人主档。

## 13. 效率度量

这个项目的目标是提高人才寻访效率，因此需要从一开始记录效率指标。否则只能凭感觉判断 Agent 是否有用。

### 13.1 Session 级指标

建议统计：

```text
total_runtime_minutes
search_round_count
raw_candidate_count
unique_candidate_count
detail_fetch_count
matched_count
ab_count
ab_per_detail_fetch
ab_per_round
detail_fetch_to_ab_rate
manual_intervention_count
```

### 13.2 搜索假设级指标

每种假设统计：

```text
raw_count
unique_count
detail_fetch_count
ab_count
noise_count
duplicate_count
```

### 13.3 人工节省指标

可以先粗略估算：

```text
cards_read_by_agent
details_summarized_by_agent
manual_confirmations
estimated_manual_minutes_saved
```

估算不必一开始很精确，但要让用户看到系统是不是在减少重复劳动。

### 13.4 UI 展示建议

顶部状态栏或任务总结里展示：

```text
读卡片 92 | 抓详情 9 | A/B 1 | 每抓详情产出 0.11 个 A/B
```

如果一轮结果差，要显示：

```text
本轮 30 张卡片，0 个建议抓详情，建议重新校准关键词。
```

## 14. 合规与风险边界

本项目定位应保持为「寻访辅助」和「证据整理」，而不是自动雇佣决策工具。

设计边界：

1. 系统不自动淘汰候选人。
2. 系统不自动联系候选人。
3. 系统不把分数作为最终决策。
4. 系统必须保留人工确认和人工最终判断。
5. 系统必须记录 AI 使用的基准版本、证据和输出。

UI 文案建议避免：

```text
合格 / 不合格
录用 / 淘汰
最终评分
```

建议使用：

```text
优先沟通
建议确认
信息不足
暂不推荐
```

如果未来加入自动触达、批量筛除、团队协作或对候选人产生实际决策影响，需要再补：

1. 偏差评估。
2. 审计日志。
3. 候选人通知策略。
4. 数据保留与删除策略。
5. 敏感信息处理策略。

## 15. 迁移策略

为了避免一次改动过大，建议分 4 步落地。

### Phase 1：基准草案与确认

目标：

1. 新增匹配词与岗位要求数据结构。
2. 新建任务后先生成草案。
3. UI 可编辑草案。
4. 点击确认后才能开始任务。

验收：

1. 没确认时不能开始搜索。
2. 确认后任务能按原流程继续。
3. 已确认文本能持久化并重启后读取。

### Phase 2：搜索和匹配改用确认基准

目标：

1. Planner / Agent Brain 改为读取 `keywords_text` 和 `requirements_text`。
2. RealMatchService prompt 改为基于确认基准。
3. 匹配输出增加证据、缺口、风险、待确认问题。
4. 修正 `no_wait`，避免收割轮被后台匹配阻塞。
5. 搜索计划输出本轮 `search_hypothesis_type` 和 `search_hypothesis_text`。

验收：

1. AI 不再输出复杂 criteria JSON 作为主判断标准。
2. 匹配结果能清楚说明命中了哪条岗位要求。
3. 人工修改关键词后，下一轮搜索明显使用新关键词。

### Phase 3：去除分数产品化

目标：

1. 替换 `pre_score` 的 UI 展示。
2. 卡片阶段输出 `fetch / maybe / noise`。
3. `prequalified_count` 改为更有意义的 `fetch_recommended_count` 或类似字段。

验收：

1. 销售岗不会因为「销售」这个词被当作噪音误伤。
2. 候选人池不再展示 0-100 预评分。
3. Agent 抓详情依据是证据和人工确认基准，而不是固定分数阈值。

### Phase 4：策略版本化、重新校准与来源历史

目标：

1. 支持任务运行中重新校准。
2. 每轮绑定基准版本。
3. 候选人匹配结果绑定基准版本。
4. 新增 `candidate_sources`。
5. 增加搜索假设级效果统计。

验收：

1. 能看出第几轮使用 v1，第几轮使用 v2。
2. 修改基准后不会污染历史匹配解释。
3. 导出时包含使用的寻访基准版本。
4. 重复候选人能看到多次来源。
5. 能看到不同搜索假设的产出质量。

## 16. 测试计划

新增单元测试：

1. AI 草案 JSON 解析。
2. criteria draft -> confirmed 状态流转。
3. 未确认基准时禁止启动 session。
4. 人工修改后保存版本。
5. no_wait 不阻塞下一步。
6. 销售岗关键词不被默认负面词误伤。
7. 重复候选人会新增 `candidate_sources` 记录。
8. 搜索轮次会绑定 `search_hypothesis_type`。

新增集成测试：

1. 新建任务 -> 生成草案 -> 确认 -> 跑一轮模拟搜索。
2. 修改基准 -> 下一轮使用新基准。
3. 匹配结果输出 evidence / unknowns / questions。
4. 同一候选人被两轮命中后，主档不重复，来源记录增加。

人工验收：

1. 用销售岗测试。
2. 用产品岗测试。
3. 用技术岗测试。
4. 检查 AI 生成的匹配词是否简洁。
5. 检查匹配解释是否引用简历证据。
6. 检查任务总结是否能看出 Agent 是否真的节省人工时间。

## 17. 优先级

最高优先级：

1. 匹配词与岗位要求确认模块。
2. Agent 启动前强制确认。
3. 匹配 prompt 改为证据驱动。
4. 修复销售岗被默认噪音词误伤。

第二优先级：

1. 去除预评分 UI。
2. 卡片阶段改为 `fetch / maybe / noise`。
3. no_wait 真正异步。
4. 搜索计划绑定搜索假设。

第三优先级：

1. 基准版本化。
2. 候选人多来源记录。
3. 策略效果统计。
4. 合规和审计信息完善。

## 18. 本轮落地建议

下一步建议先做一个最小闭环：

```text
新增 criteria version 存储
-> 新建任务后生成 AI 草案
-> UI 展示两个可编辑框
-> 人工确认
-> 原 AgentRuntime 读取确认后的文本继续运行
```

这一步先不急着大改候选人表和评分体系，避免一次改动太大。确认基准跑通后，再逐步把卡片观察、匹配 prompt 和 UI 列展示从分数迁移到证据。
