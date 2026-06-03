"""Prompt loader: read .txt prompt templates from disk with built-in fallback.

Put prompt files under ``liepin_agent/prompts/txt/{name}.txt``.
If a file is missing, the built-in fallback is used so the app never breaks.
Variables are filled with Python ``str.format(**kwargs)``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict


class PromptLoader:
    """Load prompt templates from ``.txt`` files with automatic fallback."""

    _instance: PromptLoader | None = None

    def __new__(cls, prompts_dir: Path | None = None) -> PromptLoader:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, prompts_dir: Path | None = None) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._prompts_dir = prompts_dir or Path(__file__).parent / "txt"
        self._cache: Dict[str, str] = {}
        self._built_ins: Dict[str, str] = {}

    def get(self, name: str, **kwargs: object) -> str:
        """Return the prompt template *name* with variables substituted.

        If the ``.txt`` file exists on disk it is read and cached.
        Otherwise the built-in fallback is used.
        """
        template = self._load_template(name)
        return template.format(**kwargs)

    def raw(self, name: str) -> str:
        """Return the raw template for *name* without variable substitution."""
        return self._load_template(name)

    def _load_template(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]

        file_path = self._prompts_dir / f"{name}.txt"
        if file_path.exists():
            text = file_path.read_text(encoding="utf-8")
            self._cache[name] = text
            return text

        text = self._built_in(name)
        self._cache[name] = text
        return text

    def clear_cache(self) -> None:
        """Drop the in-memory cache so the next ``get()`` re-reads from disk.

        Useful for hot-reloading prompts without restarting the app.
        """
        self._cache.clear()

    # ------------------------------------------------------------------
    # Built-in fallbacks (copied from the original brain.py)
    # ------------------------------------------------------------------
    def _built_in(self, name: str) -> str:
        try:
            return _BUILT_IN_PROMPTS[name]
        except KeyError:
            raise KeyError(f"Unknown prompt name: {name!r}")


# Singleton accessor for convenience
_prompt_loader: PromptLoader | None = None


def get_prompt_loader() -> PromptLoader:
    global _prompt_loader
    if _prompt_loader is None:
        _prompt_loader = PromptLoader()
    return _prompt_loader


def prompt(name: str, **kwargs: object) -> str:
    """Convenience shortcut: ``prompt("build_criteria", jd=..., notes=...)``."""
    return get_prompt_loader().get(name, **kwargs)


def system_prompt() -> str:
    """Return the agent system prompt."""
    return get_prompt_loader().get("system_prompt")


# ------------------------------------------------------------------------------
# Built-in prompts – kept here so the app works even when .txt files are deleted.
# ------------------------------------------------------------------------------
_BUILT_IN_PROMPTS: Dict[str, str] = {
    "system_prompt": """你是资深猎头，在猎聘平台寻找候选人。

## 猎聘搜索经验（重要）
1. 词的选择：项目/产品词 > 技术词 > 岗位名称词
   - ✓ "无刷电机 小家电" — 精准
   - ✗ "电机工程师" — 太泛，噪音大
2. 词的数量：2-3 个词 AND 组合最佳
   - 1 个词太泛
   - 4 个以上词结果通常很少或为空
3. 词的粒度：
   - 太粗（"电机"）→ 各种电机混入
   - 太细（"无刷电机 FOC SVPWM Maxwell"）→ 结果为空
   - 刚好（"无刷电机 小家电"）→ 精准
4. 活跃度：默认 30 天内活跃（不要设太窄）
5. 迭代策略：
   - 结果太少 → 减少关键词或换同义词
   - 噪音太多 → 加限定词或用排除词（-xxx）
   - 没找到人 → 考虑跨行业可迁移技能

## 猎聘语法
- 空格 = AND（同时包含）
- 减号 = 排除（如 -实习 -助理）
- scope：全部经历 / 目前职位 / 过往职位

根据 JD 和匹配条件，自主决定搜索词。你比任何预设词库都更懂行业术语。

只输出 JSON，不要 Markdown。
""",
    "build_criteria": """请从 JD 中提取匹配条件，输出简洁 JSON：
{{
  "core_requirement": "一句话核心要求，如：小家电无刷电机领域，必须有无刷电机经验，CAD优先",
  "position_filter": "猎聘职位栏收口词，1-2个词。必须从JD中的真实岗位名称提取，如：车间主任、生产经理、产品经理、算法工程师。不要编造，不要写'产品' unless JD确实是产品岗",
  "search_direction": "一句话描述AI对本岗位寻访方向的理解，不是搜索关键词，是策略方向，用户可编辑修正",
  "target_companies": ["从JD或补充说明中提取的目标/对标公司名列表。如客户说'必须XX公司出身'或JD写'有XX公司背景优先'，则填入。无则留空数组"]
}}

要求：
1. 只提取寻访真正需要的关键技能、行业、业务或场景背景，不要把 JD 里所有词都搬出来。
2. core_requirement 要短、清楚，聚焦硬门槛。
3. position_filter 务必准确：它是猎聘网站左侧职位栏的过滤条件，写错了会直接过滤掉目标候选人。
4. search_directions 是对岗位的理解方向，每方向一句话，给出不同切入角度（如收紧/放宽/跨行业）。
5. target_companies 只在明确提到对标/目标公司时填写，不要凭空编造。

【JD】
{jd}

【补充说明】
{notes}
""",
    "initial_plan": """请为猎聘找人生成第一轮搜索计划，输出 JSON：
{{
  "query": "搜索栏关键词，2-3个短词，用空格分隔（AND逻辑）。优先放项目/产品词 + 技术词，避免单泛词。如果上轮搜索零产出，本轮query必须减少到2个词，放宽条件",
  "position_filter": "职位栏收口词。1-2个词，从JD真实岗位名称提取。如觉得需要调整可以换词，没把握就留空",
  "scope": "全部经历/目前职位/过往职位",
  "match_mode": "all/any",
  "filters": {{"city": [], "active_days": 30, "education": "", "age": "", "company": ""}},
  "intent": "本轮搜索目的",
  "expected_signal": ["期待在候选人卡片中看到的具体信号，至少3条"],
  "risk": "本轮可能噪音及规避方式",
  "search_hypothesis_type": "core_background/target_company/transferable_scene/long_tail",
  "search_hypothesis_text": "本轮验证的搜索假设"
}}

## 生成要求
1. query 用 AND 组合（空格分隔），2-3 个词最佳。搜索栏优先放项目/产品词或业务场景词，而非职位大词。
2. **position_filter 慎用**：query 和 position_filter 是叠加关系（AND），同时填写会导致双重过滤、结果变窄。如果 query 中已经包含精准职位方向（如"压力容器工程师"、"换热器设计工程师"），position_filter 必须留空。只有 query 是宽泛词（如"换热器"、"ASME"）时，才用 position_filter 收口（如"设计工程师"）。
3. 职位栏只放岗位收口词，不要放长句。
4. 第一轮不要设定城市筛选（filters.city 留空 []）。
5. 活跃度默认 30 天内活跃，不要设太窄。
6. 年龄：如果 JD 写了年龄上限（如"40岁以内"），填 filters.age = "40"（系统会自动加 3 岁缓冲）。不写则留空。
7. **工作年限不要填入 filters**，猎聘上的年限信息不准确，由后续匹配模型判断。只有 age 可以填。
8. 如需排除噪音，可在 query 中用减号（如 `产品经理 -助理`）。
9. expected_signal 必须具体、可观察。
10. 如果匹配条件中包含 **selected_direction**（用户确认的寻访方向），必须严格按该方向生成搜索计划，不要偏离。
11. 如果匹配条件中包含 target_companies（目标/对标公司列表）或 selected_direction 中提到"锁定XX公司"、"对标公司"等，search_hypothesis_type 应优先使用 target_company，并在 filters.company 填入其中一家公司名（第一轮选最核心的一家），同时 scope 设为"目前公司"或"过往公司"。

【JD】
{jd}

【补充说明】
{notes}

【匹配条件】
{criteria}
""",
    "observe_round": """请观察本轮猎聘搜索结果池，输出 JSON：
{{
  "round_quality": "empty/low/uncertain/medium/high",
  "raw_count": 数字,
  "deduped_count": 数字,
  "estimated_relevant_count": 数字,
  "noise_patterns": ["噪音类型"],
  "positive_signals": ["正向信号"],
  "recommended_round_type": "skip_detail/sample_detail/validate_detail/harvest_detail",
  "reason": "判断依据，包含质量评估、噪音归因、策略建议"
}}

## 质量分级标准
- **empty**：搜索结果为空或仅0-2人。→ 关键词过窄或条件冲突。
- **low**：结果数≥8但卡片无有效信号，或 relevant_count=0。→ 建议 skip_detail。
- **uncertain**：有少量潜在信号，但不确定是真实匹配还是标题党。→ 建议 sample_detail（可抓 6-10 人）。
- **medium**：有多个有效信号，卡片层面出现目标行业/技能/公司。→ 建议 validate_detail（可抓 12-20 人）。
- **high**：大量强信号，目标人才密集。→ 建议 harvest_detail（可抓 20-40 人，充分利用预算）。

### 噪音类型识别（常见）
1. 岗位错配、职级错配、行业错配
2. 技能漂移（JD要算法，结果全是数据分析）
3. 公司偏差、地域偏差

### 正向信号识别
- 目标行业/细分领域出现频率
- 核心技能词在简历摘要中的出现率
- 目标公司/竞品公司出现数量

【本轮搜索计划】
{plan}

【匹配标准】
{criteria}

【页面信息】
{page_meta}

【候选人卡片样本】
{cards}
""",
    "decide_fetch": """请决定本轮是否抓取候选人详情，输出 JSON：
{{
  "action": "skip_detail/fetch_details",
  "round_type": "skip_detail/sample_detail/validate_detail/harvest_detail",
  "candidate_ids": ["候选人ID"],
  "fetch_limit": 数字,
  "sampling_strategy": {{"high_confidence": 数字, "diversity": 数字, "uncertain": 数字}},
  "match_wait_policy": {{"mode": "no_wait/wait_min_results/wait_all", "min_results": 数字, "timeout_seconds": 数字}},
  "reason": "详细说明：为什么这么抓、抽样逻辑、预期验证什么"
}}

## 抓取策略指南

### 各轮次抓取上限
- **skip_detail**：明显低质轮、空结果轮、或预算耗尽时不抓。
- **sample_detail（探测轮）**：抓 6-10 个。目的是快速验证搜索假设是否成立。必须混合：高置信 + 多样性（不同公司/背景） + 不确定样本。不要只抓表面最匹配的。
- **validate_detail（验证轮）**：抓 12-20 个。目的是在 medium 质量池中验证真实匹配度。优先抓卡片信号最明确的，同时保留边缘样本防止漏判。宁可多抓也不要漏抓。
- **harvest_detail（收割轮）**：抓 20-40 个。目的是在高密度池中批量获取匹配结果。大幅放宽抽样范围，优先抓未被抓过的新面孔。用户明确不在乎成本，请充分利用预算。

### 抽样原则
1. **系统预评分已禁用，不要参考 pre_score / card_decision**。这些字段是旧规则程序的残留，可能严重误杀人才。请完全基于候选人卡片的实际内容进行判断。
2. **高置信样本**：当前职位、公司、摘要中明显体现目标技能/行业/背景的候选人。
3. **多样性样本**：来自不同公司、不同职级段、不同业务线的候选人，避免同一公司抓多人。特别关注"跨行业可迁移"人才。
4. **不确定样本**：有一项独特亮点（如目标公司背景、罕见项目经验、特殊行业交叉背景）的候选人，即使表面匹配度不高也建议抓取验证。宁可错抓不要漏抓。
5. **candidate_ids 必须来自候选人卡片列表**，不能编造。

### match_wait_policy 选择
- **wait_min_results**：sample/validate 轮默认。等待至少 min_results 个匹配完成再进入复盘。
- **wait_all**：小规模抓取时（≤5人）使用，确保所有结果都进入复盘。
- **no_wait**：harvest 轮可使用，后台异步匹配不影响下一轮计划。

## 限制
- 剩余总预算：{budget}
- 如果 remaining_budget ≤ 0，必须 action="skip_detail"。

【观察结论】
{observation}

【候选人卡片】
{cards}
""",
    "review_round": """请复盘本轮猎聘寻访，并决定停止或下一轮搜索，输出 JSON：
{{
  "action": "continue/stop",
  "summary": "复盘结论：本轮产出评估、噪音归因、策略调整方向",
  "next_plan": {{
    "query": "下一轮搜索栏关键词。注意：如果本轮零产出，query必须减到2个词，去掉最窄的那个限定词",
    "position_filter": "职位栏收口词。如认为当前职位栏过滤效果不佳，可主动调整；觉得没问题就保持原样，也可以留空",
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
    "noise_root_cause": "关键词太宽/关键词太窄/维度错误/行业误匹配/职级错配/正常噪音",
    "iteration_strategy": "收紧/放宽/换维度/跨行业mapping/长尾狙击/停止"
  }}
}}

## 复盘要求
### 0. 重要区分
- 【本轮匹配结果】仅反映本轮抓取后的匹配产出。如果本轮 match_results 为空，可能是因为匹配尚未完成或超时，**不要据此推断"连续多轮零产出"**。
- 历史各轮的搜索效果请参考【已用 query】列表，不要混淆"本轮效果"与"历史累积效果"。

### 1. 区域限制策略（重要）
如果当前搜索计划设置了城市筛选（filters.city 非空），且搜索结果极少或为空（raw_count < 5 或 estimated_relevant_count < 3），**优先尝试去掉城市限制（filters.city 设为空数组 []），保持搜索关键词（query）完全不变，扩大地理范围后再搜一轮**。只有在去掉城市限制后仍然搜不到人，才考虑修改关键词。

### 2. 停止条件（如果 should_stop 为 true，必须 action=stop）
- 已达到目标 A/B 数量
- 连续多轮低产出且**完全没有任何改进迹象**（如重复相同关键词、反复换词但噪音根因一致）
- 预算耗尽
- 已穷尽**所有**合理搜索假设（core_background / target_company / transferable_scene / long_tail 四大方向均已尝试）

### 3. 鼓励多搜索（重要）
- **默认允许最多搜索 20 轮**，所以不要轻易停止。只要还有未尝试的关键词组合、未验证的搜索假设、未探索的 transferable_scene，就应该 continue。
- 即使连续几轮低产出，只要每一轮都在尝试不同的搜索维度（换词、换行业、换假设类型），就是有价值的探索，**不要因为短期低产出自乱阵脚**。
- 猎头寻访本来就是多轮试探过程，前 5-8 轮在摸清水下结构是完全正常的。
- 只有在连续低产出**且**策略明显僵化（如一直在同一关键词上微调）时，才考虑停止。

### 4. 噪音归因与迭代策略
| 噪音根因 | 表现 | 迭代策略 |
|---------|------|---------|
| **区域过窄** | **结果极少且 filters.city 非空** | **先放区域**：去掉 city 限制，保持 query 不变，扩大地理范围 |
| 关键词太宽 | 结果量大但匹配度低 | **收紧**：增加 AND 条件，加限定词 |
| 关键词太窄 | 结果极少或为空（且已尝试过去掉 city） | **放宽**：减少关键词，换同义词，或换 transferable_scene |
| 维度错误 | 结果都是错配岗位 | **换维度**：调整 position_filter |
| 行业误匹配 | 目标行业占比低 | **跨行业mapping**：用核心技能替代行业词 |
| 职级错配 | junior/senior 混错 | **调整**：加年限词或用 scope 区分 |
| 长尾不足 | 核心人才被大词淹没 | **长尾狙击**：用专业工具/细分技术词 |

### 4. 搜索假设迭代路径
- 不要重复 used_queries
- 围绕 JD 核心要求自主调整搜索词，可以换同义词、加限定词、减词
- 如果同一假设方向已验证成功，沿相邻场景扩展
- 如果同一假设方向已验证失败，切换假设类型
- **四大假设类型（core_background / target_company / transferable_scene / long_tail）都至少尝试一轮之前，不要轻易判定"已穷尽"**
- 当 core_background 和 target_company 都耗尽时，优先尝试 transferable_scene，其次 long_tail

### 5. next_plan 字段要求
- query：2-3 个词 AND 组合，空格分隔，明确是收紧/放宽/换维度。**如果本轮搜索零产出，下一轮query必须减少到2个词，优先去掉最细分/最罕见的那个词**
- **position_filter：query 和 position_filter 是叠加过滤（AND）。如果 query 已包含精准职位词（如"压力容器工程师"），position_filter 必须留空，避免双重过滤。只有 query 偏宽泛时才用 position_filter 收口。**
- filters：只保留 city / active_days / education / age / company，**严禁填充 work_years**
- scope：target_company 优先用"目前/过往公司"；transferable_scene 用"全部经历"
- company：target_company 假设时，如需精准定向某家对标公司，可在 filters 中填 company="公司名"
- expected_signal：具体、可观察

【should_stop】{should_stop}
【stop_reason】{stop_reason}
【target_met】{target_met}
【上一轮计划】
{plan}
【已用 query】（历史搜索记录，按轮次顺序）
{used_queries}

【本轮匹配结果】（仅含本轮详情抓取后已完成匹配的候选人，不代表历史各轮产出）
{matches}

【噪音】
{noise}
【JD】
{jd}

【匹配条件】
{criteria}
""",
    "apply_user_command": """用户发送了一条实时指令，要求调整当前搜索计划。请根据指令内容对下一轮搜索计划进行专业调整，输出 JSON：
{{
  "query": "调整后搜索栏关键词",
  "position_filter": "职位栏收口词",
  "scope": "全部经历/目前职位/过往职位",
  "match_mode": "all/any",
  "filters": {{"city": [], "active_days": 30, "education": "", "age": "", "company": ""}},
  "intent": "调整后的搜索目的",
  "expected_signal": [],
  "risk": "调整后的风险",
  "search_hypothesis_type": "core_background/target_company/transferable_scene/long_tail",
  "search_hypothesis_text": "调整后的搜索假设"
}}

## 调整原则
1. **query 中的关键词必须优先来自【已确认匹配词与岗位要求】，不得自行发明新词**。只能对已确认词进行组合、拆分、减少或添加排除词。
2. 如果用户指令与已确认匹配词冲突，以用户指令为准，但需在 risk 中说明。
3. 保持猎聘 AND 语法合规，空格分隔。
4. 不要重复已用过的 query。
5. 如需定向某家对标公司，可在 filters.company 填入公司名，配合 scope="目前公司"或"过往公司"。
6. 工作年限不要填入 filters，由后续匹配模型判断。

【用户指令】
{user_command}

【当前搜索计划】
{current_plan}

【已确认匹配词与岗位要求】
{criteria}
""",
    "generate_web_search_queries": """你是一位资深猎头顾问，正在为猎聘寻访任务制定联网搜索策略。

当前寻访遇到了困难，需要通过搜索引擎获取外部情报来辅助优化搜索关键词。

## 当前信息
【JD】
{jd}

【当前搜索关键词】
{current_query}

【已尝试过的关键词】
{used_queries}

【噪音模式】（搜索结果中混入的不相关类型）
{noise_patterns}

【已匹配候选人概况】
{matches}

【匹配条件】
{criteria}

## 任务
请生成 2-3 个精准的搜索引擎查询，用于获取以下类型的情报：
1. 行业术语、技能同义词、岗位别称（帮助优化关键词）
2. 目标公司、竞品公司、行业领先企业（帮助定向搜索）
3. 可迁移行业、相邻领域（帮助拓宽搜索范围）

## 输出要求
请输出 JSON：
{{
  "queries": ["查询1", "查询2", "查询3"],
  "rationale": "为什么查这些，预期获得什么情报"
}}

注意：
- 查询要具体、有针对性，不要泛泛的"岗位要求"。
- 优先查与当前噪音模式相关的情报（如噪音是"销售岗位"，则查"该领域非销售的技术方向"）。
- 查询用中文，每个查询不超过 20 字。
- 不要重复已尝试过的关键词作为查询。
""",
    "enhance_plan": """你是一位资深猎头顾问，正在猎聘平台寻访人才。当前搜索遇到了瓶颈，我已经联网搜索了相关行业情报。

请根据以下联网情报，对下一轮搜索计划进行专业修正或增强。

## 当前困境
【JD】
{jd}

【已用关键词】
{used_queries}

【噪音模式】
{noise_patterns}

【当前搜索计划】
{current_plan}

【匹配条件】
{criteria}

## 联网情报
{web_search_results}

## 任务
1. 分析联网情报中与寻访相关的关键信息：行业术语、目标公司、可迁移方向、技能同义词等。
2. 判断当前困境的根因是否可以通过情报解决。
3. 如果情报有用，输出修正后的搜索计划；如果情报无用或无关，明确拒绝增强。

## 输出要求
请输出 JSON，不要 Markdown：

{{
  "should_enhance": true/false,
  "enhancement_rationale": "为什么根据情报做了这个调整，或为什么情报无用",
  "query": "修正后的搜索栏关键词（2-3个词）",
  "position_filter": "职位栏收口词",
  "scope": "全部经历/目前职位/过往职位",
  "match_mode": "all/any",
  "filters": {{"city": [], "active_days": 30, "education": "", "age": "", "company": ""}},
  "intent": "本轮搜索目的",
  "expected_signal": ["期待看到的具体信号，至少3条"],
  "risk": "本轮可能噪音及规避方式",
  "search_hypothesis_type": "core_background/target_company/transferable_scene/long_tail",
  "search_hypothesis_text": "本轮验证的搜索假设"
}}

注意：
- 如果情报没有提供有用的信息，should_enhance=false，其他字段可留空。
- query 必须遵守猎聘 AND 语法（空格分隔），2-3 个词最佳。
- 不要重复 used_queries 中的关键词组合。
- 工作年限不要填入 filters，由后续匹配模型判断。只有 age 可以填。
- 如需排除噪音，可在 query 中用减号（如 `产品经理 -助理`）。
""",
}
