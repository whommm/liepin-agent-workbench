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

## 最重要原则：猎聘是【简历全文检索】
你输入的每个词，都会去候选人简历的正文里全文匹配。所以关键词要
【预测这个词在目标候选人简历里会怎么写】，而不是照搬 JD 的招聘语言。
JD 用"招聘画像语言"，候选人简历用"履历语言"，两者经常不一样。

## 先翻译，再组合
寻访前必须为每个核心要求做"术语翻译"——把它换成简历里更可能出现的同义词：
- "潮玩" → 简历里更可能是：盲盒、手办、积木、IP衍生品、玩具设计、潮玩
- "插画" → 简历里更可能是：插画、手绘、原画、视觉设计、美宣
- "渲染" → 太泛（游戏/UI/建筑都用），要么删掉，要么换成"3D渲染/产品渲染"
- "小家电" → 简历里可能写：生活电器、个护、厨房电器、小家电
- "结构设计" → 简历里可能写：结构、机械结构、产品设计、工业设计

永远从"翻译结果"里选词，不要直接用 JD 原词。

## 猎聘搜索经验
1. 词的选择优先级：品类/行业词 > 公司/品牌词 > 技能词 > 岗位大词
   岗位大词（工程师/总监/设计师）噪音极大，尽量不单独用。
2. 词的数量：稀疏/冷门市场用 1 个宽词试水；常规市场 2-3 个词 AND。
   宁可先宽后窄，不要一上来就压满。4 个以上词 AND 几乎一定零产出。
3. query 与 position_filter 是【叠加 AND】关系：
   - 两者同时生效，等于又加一道硬过滤。
   - query 已含职位方向时，position_filter 必须留空。
   - 只有 query 是行业/品类宽词时，才用 position_filter 收口岗位。
4. scope（搜索范围）：默认"全部经历"；target_company 假设时用"目前/过往公司"。
5. 活跃度：默认 30 天内活跃，不要设太窄。

## 寻访阶段策略（按轮次推进）
- 探测期（前 1-2 轮）：用最宽的品类词看市场水深和分布，不追求命中。
- 收口期：看到目标人群分布后，加限定词收紧。
- 定向期：用对标公司 filters.company + scope 精准捞人。
- 迁移期：核心人才枯竭时，找相邻行业里具备可迁移技能的人。

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
  "target_companies": ["从JD或补充说明中提取的目标/对标公司名列表。如客户说'必须XX公司出身'或JD写'有XX公司背景优先'，则填入。无则留空数组"],
  "gender_requirement": "从JD中识别性别要求。如'限男性''要求女性''男士优先''女士优先'等填具体性别；'男女不限''性别不限'填'不限'；未提及填''"
}}

要求：
1. 只提取寻访真正需要的关键技能、行业、业务或场景背景，不要把 JD 里所有词都搬出来。
2. core_requirement 要短、清楚，聚焦硬门槛。
3. position_filter 务必准确：它是猎聘网站左侧职位栏的过滤条件，写错了会直接过滤掉目标候选人。
4. search_directions 是对岗位的理解方向，每方向一句话，给出不同切入角度（如收紧/放宽/跨行业）。
5. target_companies 只在明确提到对标/目标公司时填写，不要凭空编造。
6. gender_requirement 必须认真识别：JD里写"限男""要求男性""男士优先"就填"男"；写"限女""要求女性""女士优先"就填"女"；写"男女不限""性别不限"就填"不限"；没提就留空。这个字段直接影响搜索筛选，不要忽略。

【JD】
{jd}

【补充说明】
{notes}
""",
    "initial_plan": """请为猎聘找人生成第一轮搜索计划，输出 JSON：
{{
  "query": "搜索栏关键词。第一轮是探测轮，优先2个词AND；冷门/稀疏岗位可只用1个品类宽词。词必须来自下面术语翻译结果，不要直接用JD原词",
  "position_filter": "职位栏收口词。1-2个词，从JD真实岗位名称提取。没把握就留空。见下方双重过滤警告",
  "scope": "全部经历/目前职位/过往职位（第一轮默认全部经历）",
  "match_mode": "all/any",
  "filters": {{"city": [], "active_days": 30, "education": "", "age": "", "company": ""}},
  "intent": "本轮搜索目的（第一轮目的通常是探测市场水深，不追求命中）",
  "expected_signal": ["先列出你为每个核心要求做的术语翻译，再列出期待在卡片中看到的具体信号，至少5条"],
  "risk": "本轮可能噪音及规避方式",
  "search_hypothesis_type": "core_background/target_company/transferable_scene/long_tail",
  "search_hypothesis_text": "本轮验证的搜索假设"
}}

## 生成要求（第一轮 = 探测轮）

### 1. 必须先做术语翻译
阅读 JD 和匹配条件，把每个核心要求翻译成"简历里实际会出现的同义词"。
在 expected_signal 开头用「翻译：JD词 → 候选词1/候选词2」的形式列出你的判断，
让用户能看到你的翻译逻辑。query 里的词必须从翻译结果里选，不要直接用 JD 原词。

### 2. query 构造（探测导向）
- 第一轮目标是摸清市场结构，宁可宽一点，不要一上来就压满。
- 常规岗位：2 个词 AND（品类词 + 核心技能词）。
- 冷门/稀疏岗位（潮玩、细分工业、传统行业）：第一轮可只用 1 个品类宽词试水。
- 严禁第一轮就用 3 个以上硬技能词 AND 叠加——这是零产出的头号原因。

### 3. 双重过滤警告（重要）
query 与 position_filter 是【叠加 AND】关系，两者同时填会让结果大幅变窄甚至归零。规则：
- 如果 query 已含职位方向词（如"平面设计""压力容器""换热器"）→ position_filter 必须留空。
- 只有 query 是行业/品类宽词（如"潮玩""盲盒""ASME"）时 → 才可用 position_filter 收口岗位。
- 拿不准就留空 position_filter。

### 4. 其他字段
- 第一轮不设 city（留空 []），先看全国分布。
- active_days 默认 30 天。
- age：JD 写了年龄上限（如"40岁以内"）就填 filters.age="40"（系统自动加3岁缓冲）；
  写了区间（如"25-35岁"）填"25-35"；没写就留空。
- gender：匹配条件里有明确性别要求才填，否则留空。
- work_years 绝不填入 filters，由后续匹配模型判断。
- position_filter 1-2 词，必须从 JD 真实岗位名提取，不要编造（如 JD 不是产品岗就别写"产品"）。

### 5. 假设类型
第一轮通常固定用 core_background（核心背景验证）。
仅当匹配条件里明确给了 target_companies 或 selected_direction 要求定向对标公司时，
才用 target_company（此时公司名走 filters.company + scope="目前/过往公司"，不进 query）。

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
    "query": "下一轮搜索栏关键词。零产出时优先换语义层，不要简单减词",
    "position_filter": "职位栏收口词。query含职位词时必须留空（双重过滤警告）",
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
    "noise_root_cause": "关键词太宽/关键词太窄/词不在简历出现/维度错误/行业误匹配/职级错配/区域过窄/正常噪音",
    "iteration_strategy": "收紧/换语义层/换维度/定向公司/跨行业mapping/长尾狙击/放区域/停止"
  }}
}}

## 复盘要求

### 0. 重要区分
- 【本轮匹配结果】仅反映本轮抓取后的匹配产出。如果本轮 match_results 为空，
  可能是因为匹配尚未完成或超时，**不要据此推断"连续多轮零产出"**。
- 历史各轮搜索效果请参考【已用 query】列表，不要混淆"本轮效果"与"历史累积效果"。

### 1. 先判断本轮属于哪种情境（决策分叉点）

#### 情境 A：本轮零产出或结果极少（raw_count < 5）
根因几乎都是【词太窄 / 用了简历里不出现的词 / 市场稀疏】。
按以下优先级调整，**不要停留在同一批词上反复微调**：
1. 【换语义层】最优先。不要只减词，要把当前词换成品类词/行业词/公司词。
   例：'潮玩 插画 渲染' 失败 → 不要减成 '潮玩 插画'，
   而是换品类词 '盲盒' 或 '手办'，或换假设走 target_company（对标公司）。
2. 【缩到 1 词】用最宽的核心品类词单独试水，先确认市场到底有没有人。
3. 【换假设类型】core_background 连续失败就转：
   - target_company：找对标/竞品公司的人（公司名走 filters.company，不进 query）
   - transferable_scene：拆出核心技能，找有此技能的其他行业（如潮玩要插画 → 找动漫/广告/游戏插画师）
   - long_tail：用细分专业工具词狙击（如 'ZBrush 手办'）

#### 情境 B：结果量大但噪音高（raw_count ≥ 8 且 relevant 低）
| 根因 | 表现 | 对策 |
|------|------|------|
| 某词被泛化 | 该词命中大量错配行业 | 删该词，或加 -排除词 |
| 岗位/职级错配 | 结果都是错配岗位或层级 | 调 position_filter 或换 scope |
| 行业占比低 | 目标行业候选人少 | 跨行业 mapping，用核心技能替代行业词 |
| 长尾被淹没 | 少数目标人才混在大词里 | 长尾狙击，用细分技术词收紧 |

#### 情境 C：区域过窄（结果少且 filters.city 非空）
优先去掉 city 限制（设为空数组 []），保持 query 完全不变，扩大地理范围再搜一轮。
只有去掉 city 后仍搜不到，才回退到情境 A 的换词策略。

### 2. 假设类型与 query 构造法（每轮必须明确当前用哪种）
- core_background：品类词 + 核心技能词。如 '盲盒 原画'。scope 用"全部经历"。
- target_company：【公司名不进 query！】用 filters.company="对标公司名" +
  scope="目前公司"或"过往公司"，query 放该公司的主营业务词。
- transferable_scene：拆出 JD 的核心技能，找具备此技能的其他行业。
  如潮玩要插画技能 → query '插画 包装设计' 找广告/快消的人。
- long_tail：用细分专业词/工具词狙击。如 'ZBrush 潮玩'。

### 3. 停止条件（如果 should_stop 为 true，必须 action=stop）
- 已达到目标 A/B 数量
- 连续多轮低产出且**完全没有任何改进迹象**（重复相同关键词、反复换词但噪音根因一致、策略明显僵化）
- 预算耗尽
- 已穷尽**所有**合理搜索假设（四大方向均已尝试）

### 4. 鼓励多搜索（重要）
- 默认允许最多搜索 20 轮，不要轻易停止。
- 只要每一轮都在尝试不同的搜索维度（换词、换行业、换假设类型），就是有价值的探索。
- 猎头寻访本来就是多轮试探，前 5-8 轮在摸清水下结构完全正常。
- 只有连续低产出**且**策略明显僵化（一直在同一批词上微调）时才考虑停止。

### 5. next_plan 字段要求
- query：2-3 个词 AND 组合（稀疏市场可 1 词）。明确本轮是收紧/换语义层/换维度/定向。
  **零产出时优先换语义层，不要简单减词。**
- 双重过滤警告：query 含职位方向词时 position_filter 必须留空；
  只有 query 是行业/品类宽词时才用 position_filter 收口。拿不准就留空。
- filters：只保留 city / active_days / education / age / company / gender，**严禁 work_years**。
- scope：target_company 用"目前/过往公司"；其余默认"全部经历"。
- expected_signal：具体、可观察。
- 不要重复 used_queries（包括词的顺序不同但词集合相同的组合）。

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
  "filters": {{"city": [], "active_days": 30, "education": "", "age": "", "company": "", "gender": ""}},
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
