请复盘本轮猎聘寻访，并决定停止或下一轮搜索，输出 JSON：
{{
  "action": "continue/stop",
  "summary": "复盘结论，包含：本轮产出评估、噪音归因、策略调整方向",
  "next_plan": {{
    "query": "下一轮搜索栏关键词",
    "position_filter": "职位栏",
    "scope": "全部经历/目前职位/过往职位",
    "match_mode": "all/any",
    "filters": {{}},
    "intent": "下一轮目的",
    "expected_signal": [],
    "risk": "风险",
    "search_hypothesis_type": "core_background/target_company/transferable_scene/long_tail",
    "search_hypothesis_text": "下一轮验证的搜索假设"
  }},
  "evidence": {{
    "ab_count": "数字",
    "match_count": "数字",
    "noise_root_cause": "噪音根因分类：关键词太宽/关键词太窄/维度错误/行业误匹配/职级错配/正常噪音",
    "iteration_strategy": "迭代策略：收紧/放宽/换维度/跨行业mapping/长尾狙击/停止"
  }}
}}

## 复盘要求

### 1. 停止条件（如果 should_stop 为 true，必须 action=stop）
- 已达到目标 A/B 数量。
- 连续多轮低产出且无明显改进空间。
- 预算耗尽。
- 已穷尽合理搜索假设。

### 2. 噪音归因与迭代策略
如果本轮产出不理想，必须从以下维度归因，并选择对应迭代策略：

| 噪音根因 | 表现 | 迭代策略 |
|---------|------|---------|
| 关键词太宽 | 结果量大但匹配度低，大量无关岗位混入 | **收紧**：增加 AND 条件，加入业务场景词或行业术语 |
| 关键词太窄 | 结果极少或为空 | **放宽**：减少关键词数量，改用 OR 扩展同义词，或换 transferable_scene |
| 维度错误 | 结果都是某类错配岗位（如搜管理岗全是技术骨干） | **换维度**：调整 position_filter 或加入管理/团队关键词 |
| 行业误匹配 | 目标行业占比低，或漏掉跨行业可迁移人才 | **跨行业mapping**：用核心技能词替代行业词，识别可迁移技能 |
| 职级错配 | junior 简历混入 senior 搜索，或反之 | **调整**：加入年限/团队规模/职级关键词，或用 scope 区分目前/过往 |
| 长尾不足 | 核心人才被大词淹没，精准候选人未出现 | **长尾狙击**：用专业工具/认证/细分技术词替换通用词 |

### 3. 搜索假设迭代路径
- **不要重复 used_queries**。
- **不要发明新的岗位要求**，只能围绕已确认匹配词组合、放宽或收紧。
- **next_plan.query 中的关键词必须严格来自【已确认匹配词与岗位要求】中的匹配词，不得自行添加、扩展或发明新的关键词**。你只能对这些已确认词进行组合、拆分、减少数量或添加排除词（如 `-软件`），禁止引入未确认的新词。
- 如果同一假设方向已验证成功（AB 产出好），沿相邻场景继续扩展（如从"BMS"扩展到"电池包""电芯研发"）。
- 如果同一假设方向已验证失败（连续低产出），切换假设类型（如从 core_background 切换到 transferable_scene 或 long_tail）。
- 当 core_background 和 target_company 都耗尽时，优先尝试 transferable_scene（跨行业mapping）。

### 4. next_plan 字段要求
- **query**：必须符合猎聘 AND 语法，空格分隔。明确写出你是收紧、放宽还是换维度。
- **scope**：根据假设类型选择。target_company 优先用"目前公司"或"过往公司"；transferable_scene 必须用"全部经历"以捕获跨行业人才。
- **expected_signal**：必须具体、可观察，且与 query 逻辑一致。
- **search_hypothesis_type**：本轮选择的假设类型，必须在枚举范围内。

【should_stop】{should_stop}
【stop_reason】{stop_reason}
【target_met】{target_met}
【上一轮计划】
{plan}
【已用 query】
{used_queries}
【匹配结果】
{matches}
【噪音】
{noise}
【JD】
{jd}

【已确认匹配词与岗位要求】
{criteria}
