# 设计优化落地 TODO

执行原则：

1. 这个 TODO 是完整落地清单，不是 Phase 1 做完就停。
2. 后续一旦开始实现，应从上到下连续推进，直到全部完成、测试通过、或遇到必须人工决策的阻塞。
3. Phase 只用于排序和降低风险，不作为停工点。
4. 每完成一项就更新状态，避免做到哪一步不清楚。

状态标记：

- `[ ]` 未开始
- `[~]` 进行中
- `[x]` 已完成
- `[!]` 阻塞

## A. 数据模型与存储

- [x] 新增 Session 状态：`criteria_draft`、`criteria_confirmed`。
- [x] 新增 `match_criteria_versions` 表。
- [x] `match_criteria_versions` 支持字段：`id`、`session_id`、`version`、`status`、`keywords_text`、`requirements_text`、`source_jd_text`、`source_user_notes`、`ai_raw_response_json`、`created_by`、`confirmed_by`、`created_at`、`confirmed_at`。
- [x] 为旧 `match_criteria` 设计兼容读取逻辑，避免现有数据直接失效。
- [x] `search_rounds` 新增 `criteria_version_id`。
- [x] `search_rounds` 新增 `search_hypothesis_type`。
- [x] `search_rounds` 新增 `search_hypothesis_text`。
- [x] `match_results` 新增 `criteria_version_id`。
- [x] `match_results` 新增 `evidence_json`。
- [x] `match_results` 新增 `unknowns_json`。
- [x] `match_results` 新增 `questions_json`。
- [x] `match_results` 新增 `confidence`。
- [x] 新增 `candidate_sources` 表。
- [x] `candidate_sources` 支持字段：`id`、`candidate_id`、`session_id`、`round_id`、`criteria_version_id`、`query`、`position_filter`、`search_hypothesis_type`、`search_hypothesis_text`、`result_index`、`card_decision`、`card_signals_json`、`card_risks_json`、`created_at`。
- [x] SQLite 初始化逻辑支持新增表和新增列的幂等迁移。
- [x] 新增 Store 方法：创建 AI 草案版本。
- [x] 新增 Store 方法：读取最新草案版本。
- [x] 新增 Store 方法：读取最新已确认版本。
- [x] 新增 Store 方法：确认 criteria version。
- [x] 新增 Store 方法：人工更新 criteria version。
- [x] 新增 Store 方法：保存 candidate source。
- [x] 新增 Store 方法：读取候选人多来源历史。
- [x] 新增 Store 方法：统计 Session 效率指标。
- [x] 新增 Store 方法：统计搜索假设效果指标。

## B. 匹配词与岗位要求模块

- [x] 新建「匹配词与岗位要求」领域模型。
- [x] AI 草案输出只包含 `keywords_text` 和 `requirements_text`。
- [x] 限制 AI 草案关键词数量，默认 5 到 12 个。
- [x] 禁止 AI 草案输出评分权重、复杂硬性/软性结构。
- [x] 新建任务后不直接进入可运行状态，而是进入 `criteria_draft`。
- [x] 新建任务后自动调用 AI 生成匹配基准草案。
- [x] 草案生成失败时提供可人工填写的空草案。
- [x] 人工确认后任务进入 `criteria_confirmed`。
- [x] 如果已确认版本被编辑，必须重新确认后才能继续运行。
- [x] 支持任务运行中「重新校准」并生成新版本。
- [x] 重新校准后的新版本只影响后续轮次，不污染历史轮次。

## C. Agent Brain 与 Prompt

- [x] 调整 `LLMAgentBrain.build_criteria`，输出简洁草案而不是复杂 criteria JSON。
- [x] 保留旧 criteria JSON 到新基准文本的兼容转换。
- [x] 调整 `initial_plan` 输入，使用已确认 `keywords_text` 和 `requirements_text`。
- [x] 调整 `observe_round` 输入，使用已确认寻访基准。
- [x] 调整 `decide_fetch` 输入，使用已确认寻访基准和卡片证据。
- [x] 调整 `review_round` 输入，使用已确认寻访基准、搜索假设和匹配证据。
- [x] 搜索计划输出 `search_hypothesis_type`。
- [x] 搜索计划输出 `search_hypothesis_text`。
- [x] 搜索假设类型支持 `core_background`。
- [x] 搜索假设类型支持 `target_company`。
- [x] 搜索假设类型支持 `transferable_scene`。
- [x] Agent 不允许自行发明新的岗位要求，只允许组合、放宽、收紧已确认关键词。
- [x] Agent 可建议「需要重新校准」，但不能擅自修改已确认基准。
- [x] Agent 复盘要说明当前搜索假设是继续、放弃、收紧还是换路线。

## D. Runtime 流程

- [x] `continue_session` 启动前检查是否存在已确认寻访基准。
- [x] 没有已确认基准时禁止启动搜索。
- [x] `AgentRuntime.run_session` 读取最新 confirmed criteria version。
- [x] 创建 round 时绑定 `criteria_version_id`。
- [x] 创建 round 时绑定搜索假设类型和描述。
- [x] 卡片观察、抓取决策、详情匹配、复盘都使用同一版 criteria version。
- [x] 支持从 `criteria_confirmed` 状态启动任务。
- [x] 支持从 `criteria_draft` 状态回到 UI 等待人工确认。
- [x] 支持重新校准后从下一轮使用新版基准。
- [x] 修正 `_wait_for_policy()`：`no_wait` 立即返回。
- [x] `wait_min_results` 保持等待至少 N 个或超时。
- [x] `wait_all` 保持等待全部或超时。
- [x] 后台匹配结果继续回写，但不得阻塞收割轮下一轮搜索。
- [x] 后台匹配在任务取消或失败后不得继续写入破坏性状态。

## E. 去除分数驱动

- [x] 降低 `pre_score` 在产品路径中的地位。
- [x] 卡片阶段新增 `card_decision`：`fetch` / `maybe` / `noise`。
- [x] 卡片阶段新增 `card_signals_json`。
- [x] 卡片阶段新增 `card_risks_json`。
- [x] 卡片阶段新增 `card_reason`。
- [x] 把全局固定负面词改为岗位感知规则。
- [x] 修复销售岗中「销售」被默认当噪音词的问题。
- [x] `prequalified_count` 改为更有意义的 `fetch_recommended_count` 或等价统计。
- [x] Agent 抓详情依据改为卡片信号、人工确认基准和搜索假设，不再依赖固定分数阈值。
- [x] UI 不再主展示 0-100 预评分。
- [x] 导出不再把预评分作为核心字段。

## F. 匹配证据包

- [x] 调整 `MatchResult` 领域模型，支持证据、缺口、追问、置信度。
- [x] 调整 `RealMatchService` prompt，要求输出 evidence package。
- [x] 匹配输出字段包含 `tier`。
- [x] 匹配输出字段包含 `matched_evidence`。
- [x] 匹配输出字段包含 `missing_or_unclear`。
- [x] 匹配输出字段包含 `risks`。
- [x] 匹配输出字段包含 `questions_to_verify`。
- [x] 匹配输出字段包含 `recommendation`。
- [x] 匹配输出字段包含 `confidence`。
- [x] 解析失败时返回「需人工复核」证据包，而不是伪造正常判断。
- [x] 匹配解释必须引用已确认匹配词或岗位要求。
- [x] 匹配解释必须尽量引用简历原文证据。
- [x] A/B/C/D 只作为标签，不作为唯一判断依据。

## G. UI 改造

- [x] 新建任务后展示「匹配词与岗位要求」编辑区。
- [x] 编辑区包含「岗位关键技能 / 背景词」多行输入。
- [x] 编辑区包含「岗位要求描述」多行输入。
- [x] 新增按钮「重新生成草案」。
- [x] 新增按钮「确认寻访基准」。
- [x] 新增按钮「确认寻访基准并开始」。
- [x] 未确认基准时禁用「开始」或给出明确提示。
- [x] 任务列表展示 `待确认基准`。
- [x] 任务列表展示 `已确认，待开始`。
- [x] 策略面板显示当前寻访基准版本。
- [x] 策略面板显示当前关键词。
- [x] 策略面板显示当前岗位要求。
- [x] 策略面板显示当前搜索假设。
- [x] 策略面板新增「重新校准」入口。
- [x] 候选人表格把「预评分」列改成「卡片判断」。
- [x] 候选人详情显示命中证据。
- [x] 候选人详情显示缺口。
- [x] 候选人详情显示风险。
- [x] 候选人详情显示待确认问题。
- [x] 候选人详情显示多来源历史。
- [x] 详细日志显示 criteria version id。
- [x] 详细日志显示搜索假设类型。
- [x] UI 文案避免「合格 / 淘汰 / 最终评分」。
- [x] UI 文案使用「优先沟通 / 建议确认 / 信息不足 / 暂不推荐」。

## H. 候选人多来源历史

- [x] 搜索保存候选人时，即使候选人已存在，也写入 `candidate_sources`。
- [x] `candidate_sources` 记录当前 query。
- [x] `candidate_sources` 记录当前 position_filter。
- [x] `candidate_sources` 记录当前搜索假设。
- [x] `candidate_sources` 记录当前 result_index。
- [x] `candidate_sources` 记录卡片判断和卡片信号。
- [x] 候选人详情页展示来源次数。
- [x] 候选人详情页展示来源轮次列表。
- [x] 导出时包含来源 query 列。
- [x] 导出时包含来源假设列。

## I. 效率度量与任务总结

- [x] Session 统计 `total_runtime_minutes`。
- [x] Session 统计 `search_round_count`。
- [x] Session 统计 `raw_candidate_count`。
- [x] Session 统计 `unique_candidate_count`。
- [x] Session 统计 `detail_fetch_count`。
- [x] Session 统计 `matched_count`。
- [x] Session 统计 `ab_count`。
- [x] Session 统计 `ab_per_detail_fetch`。
- [x] Session 统计 `ab_per_round`。
- [x] Session 统计 `detail_fetch_to_ab_rate`。
- [x] Session 统计 `manual_intervention_count`。
- [x] 搜索假设统计 `raw_count`。
- [x] 搜索假设统计 `unique_count`。
- [x] 搜索假设统计 `detail_fetch_count`。
- [x] 搜索假设统计 `ab_count`。
- [x] 搜索假设统计 `noise_count`。
- [x] 搜索假设统计 `duplicate_count`。
- [x] 顶部状态栏展示关键效率指标。
- [x] 任务完成事件写入效率总结。
- [x] 导出文件包含任务效率总结 sheet。

## J. 导出改造

- [x] 导出包含确认后的关键词。
- [x] 导出包含岗位要求描述。
- [x] 导出包含 criteria version。
- [x] 导出包含候选人匹配证据。
- [x] 导出包含缺口和待确认问题。
- [x] 导出包含多来源历史。
- [x] 导出包含任务效率总结。
- [x] 导出弱化或移除预评分字段。

## K. 合规与风险边界

- [x] 保持系统定位为寻访辅助，不写成自动雇佣决策。
- [x] 不自动淘汰候选人。
- [x] 不自动联系候选人。
- [x] 不把分数作为最终决策。
- [x] 保留人工确认和人工最终判断。
- [x] 日志记录每次 AI 使用的基准版本。
- [x] 日志记录 AI 输出证据。
- [x] 敏感配置继续写入 `.env`，避免 API key 进入 `config.json`。
- [x] 导出内容避免泄露 API key 或内部调试敏感信息。

## L. 测试

- [x] 单测：AI 草案 JSON 解析。
- [x] 单测：criteria draft -> confirmed 状态流转。
- [x] 单测：未确认基准时禁止启动 session。
- [x] 单测：人工修改后保存版本。
- [x] 单测：`no_wait` 不阻塞下一步。
- [x] 单测：销售岗关键词不被默认负面词误伤。
- [x] 单测：重复候选人会新增 `candidate_sources` 记录。
- [x] 单测：搜索轮次会绑定 `search_hypothesis_type`。
- [x] 单测：匹配证据包解析。
- [x] 单测：匹配证据包解析失败降级为人工复核。
- [x] 集成测试：新建任务 -> 生成草案 -> 确认 -> 跑一轮模拟搜索。
- [x] 集成测试：修改基准 -> 下一轮使用新基准。
- [x] 集成测试：匹配结果输出 evidence / unknowns / questions。
- [x] 集成测试：同一候选人被两轮命中后主档不重复、来源记录增加。
- [x] 集成测试：导出包含基准、证据、多来源和效率总结。
- [x] 回归测试：现有测试全部通过。

## M. 人工验收

- [!] 用销售岗测试，确认「销售」不再被误伤。
- [!] 用产品岗测试，确认客服/纯运营等噪音仍能识别。
- [!] 用技术岗测试，确认技能词能正确进入搜索和匹配。
- [!] 检查 AI 生成的匹配词是否简洁。
- [!] 检查人工修改后 Agent 是否遵循新基准。
- [!] 检查匹配解释是否引用简历证据。
- [!] 检查候选人多来源历史是否准确。
- [!] 检查任务总结是否能看出 Agent 是否真的节省人工时间。
- [!] 检查取消/暂停/重启恢复是否仍正常。

## N. 最终完成标准

- [x] 所有 TODO 完成或明确记录为暂缓项。
- [x] 所有自动化测试通过。
- [!] 至少完成销售岗、产品岗、技术岗三类人工验收。需要在真实猎聘账号和真实岗位上人工执行，当前已用自动化测试覆盖核心逻辑。
- [x] 文档同步更新。
- [x] README 更新新流程。
- [x] 不遗留「做到 Phase 1 就停」的半成品状态。

