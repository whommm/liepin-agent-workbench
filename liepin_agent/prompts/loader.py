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

## 猎聘城市筛选只支持直辖市和地级市
filters.city 只能填直辖市 + 地级市，绝对不能填县级市、县、区。一旦填了
猎聘城市弹窗找不到选项的城市（如义乌、昆山、晋江、顺德、龙岗、宝安），
整轮搜索会直接报错中断。常见映射：
- 义乌/东阳/永康/兰溪 → 金华；诸暨/嵊州 → 绍兴；慈溪/余姚/宁海 → 宁波；
  瑞安/乐清 → 温州；桐乡/海宁/平湖 → 嘉兴
- 昆山/张家港/常熟/太仓 → 苏州；江阴/宜兴 → 无锡
- 晋江/石狮/南安 → 泉州；福清/长乐 → 福州
- 顺德/南海/三水/高明 → 佛山；增城/从化 → 广州；龙岗/宝安/南山/福田 等 → 深圳
- 寿光/青州/诸城 → 濰坊；即墨/胶州/平度/莱西 → 青岛；龙口/莱阳/莱州 → 烟台
- 浏阳/宁乡 → 长沙；仙桃/天门/潜江 → 武汉
"浙江金华义乌"这类带省市前缀的写法，只取地级市那一层（金华）。
city_requirement 描述里可以保留原地点，但 city_scope / filters.city 必须归一化。

根据 JD 和匹配条件，自主决定搜索词。你比任何预设词库都更懂行业术语。

只输出 JSON，不要 Markdown。
""",
    "greeting_system_prompt": """你是一个资深猎头顾问，擅长撰写简洁专业的候选人打招呼消息。

要求：
1. 开头固定格式："您好，我是猎头顾问，目前有个base{{city}}的{{job_title}}机会，"
2. 中间用1-2句话简洁介绍岗位核心亮点，不要罗列职责
3. 薪资统一写"薪资待遇优厚"，不要出现具体数字或范围
4. 结尾固定："方便的话能发一份您的简历看看吗？"
5. 总字数控制在60-100字
6. 语气专业、友好、有吸引力，像真人猎头写的
7. 不要出现公司名称，除非用户特别说明
8. 不要出现"根据您的简历"等个性化表述，因为这是首次联系

只输出打招呼文本，不要解释，不要加引号。
""",
    "greeting_user_prompt": """请根据以下岗位信息，生成一段猎头打招呼文本：

岗位名称：{job_title}
工作城市：{city}
岗位描述：{job_description}
{salary_line}
生成要求：{style_hint}

请生成：
""",
    "build_criteria": """请从 JD 中提取匹配条件，输出简洁 JSON：
{{
  "core_requirement": "一句话核心要求，如：小家电无刷电机领域，必须有无刷电机经验，CAD优先",
  "position_filter": "猎聘职位栏收口词，1-2个词。必须从JD中的真实岗位名称提取，如：车间主任、生产经理、产品经理、算法工程师。不要编造，不要写'产品' unless JD确实是产品岗",
  "search_direction": "一句话描述AI对本岗位寻访方向的理解，不是搜索关键词，是策略方向，用户可编辑修正",
  "target_companies": ["从JD或补充说明中提取的目标/对标公司名列表。如客户说'必须XX公司出身'或JD写'有XX公司背景优先'，则填入。无则留空数组"],
  "city_requirement": "从JD和补充说明中提取的城市/地点要求描述。如'必须base深圳'、'深圳/广州均可'、'接受远程'等。如果没有明确要求，写'无明确要求'",
  "city_scope": ["从JD中提取的城市名列表，必须是直辖市或地级市级别，如深圳、广州、东莞、杭州、金华。县级市/区要先映射到所属地级市：义乌→金华、昆山→苏州、晋江→泉州、顺德→佛山、慈溪→宁波。如果没有明确要求，留空数组"],
  "criteria_items": [
    {{
      "type": "must/preferred/dealbreaker/verify",
      "criterion": "单条、可独立判断的岗位要求",
      "weight": 0.0,
      "acceptable_alternatives": ["可接受的替代背景"],
      "search_aliases": ["适合猎聘搜索的短词"],
      "time_window_years": null,
      "observability": "card/resume/conversation/background_check",
      "evidence_policy": "什么简历事实才算满足",
      "source_quote": "JD或补充说明中的依据原文",
      "confidence": 0.0
    }}
  ],
  "personas": [
    {{
      "name": "人才原型名称",
      "description": "该类人才的背景描述",
      "titles": ["可能的职位名称"],
      "skills": ["核心技能或场景词"],
      "company_patterns": ["目标公司或公司类型"],
      "transfer_rationale": "为什么可直接匹配或可迁移",
      "priority": 0.0
    }}
  ]
}}

要求：
1. 只提取寻访真正需要的关键技能、行业、业务或场景背景，不要把 JD 里所有词都搬出来。
2. core_requirement 要短、清楚，聚焦硬门槛。
3. position_filter 务必准确：它是猎聘网站左侧职位栏的过滤条件，写错了会直接过滤掉目标候选人。
4. search_directions 是对岗位的理解方向，每方向一句话，给出不同切入角度（如收紧/放宽/跨行业）。
5. target_companies 只在明确提到对标/目标公司时填写，不要凭空编造。
6. city_requirement 必须准确反映 JD 中的地点要求。如果有多个城市，city_scope 填入所有城市名。
7. 【猎聘城市筛选只支持直辖市和地级市】绝不能直接输出县级市、县、区名（如义乌、昆山、晋江、顺德、龙岗、宝安）。
   发现这类地点时必须映射到所属地级市：义乌/东阳/永康/诸暨/慈溪/余姚/瑞安/乐清/桐乡/海宁 →所属地级市；昆山/张家港/常熟/太仓/江阴/宜兴 →苏州/无锡；晋江/石狮/南安 →泉州；顺德/南海 →佛山。
   "浙江金华义乌"等带省市前缀的写法，取地级市那一层（金华），不要写到义乌。
8. criteria_items 拆成 3-10 条可独立判断的条件。不要把所有要求塞进一条。must 只用于真正硬门槛；信息不足时应允许 unknown。
9. observability 必须按实际可观察渠道填写。客户资源、出差意愿、离职动机等不能要求简历必须写，应该标为 conversation；学历真实性等需要背调的标为 background_check。
10. personas 输出 2-5 类：至少包含直接对口和相邻可迁移，避免所有搜索只围绕一个人才画像。

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
  "filters": {{"city": [], "active_days": 30, "education": "", "company": ""}},
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
- 年龄、性别等受保护属性不得由模型自动转成平台硬筛选，只保留为待人工确认信息。
- work_years 绝不填入 filters，由后续匹配模型判断。
- position_filter 1-2 词，必须从 JD 真实岗位名提取，不要编造（如 JD 不是产品岗就别写"产品"）。
- 若 criteria 强制要求本轮就要收城市，filters.city 必须只填直辖市或地级市（北京/上海/深圳/广州/杭州/成都/苏州/南京/武汉/东莞/西安/长沙/重庆/天津/宁波/青岛/厦门/无锡/佛山/福州/济南/合肥/郑州/大连/金华 …）。猎聘不支持县级市/区，发现义乌/昆山/晋江/顺德/龙岗 这类必须改成所属地级市（义乌→金华、昆山→苏州、晋江→泉州、顺德→佛山、龙岗→深圳），否则城市弹窗找不到选项会让整轮搜索直接报错中断。

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
  "reason": "质量判断、噪音归因和下一步建议"
}}

## 判断规则
- 数量必须读取 pool_stats，不要把 representative_samples 的数量当成全池数量。
- representative_samples 是确定性分层样本：strong_signal、uncertain、diversity。
- 卡片没写某项要求表示 unknown，不能据此判定不匹配或建议跳过。
- empty：全池为空；low：有足量卡片但样本几乎没有任何可验证信号；
  uncertain：有潜在线索但详情不足；medium：多个样本出现目标行业/技能/公司；
  high：强信号密集且跨样本重复出现。
- 除空结果、预算耗尽或存在可由卡片直接证明的硬冲突外，优先 sample/validate，
  不要因为信息不足直接 skip_detail。
- estimated_relevant_count 是基于分层样本的保守估计，要说明不确定性。

【本轮搜索计划】
{plan}

【完整匹配标准（硬条件不得忽略）】
{criteria}

【页面聚合信息】
{page_meta}

【压缩结果池：全池统计 + 最多 12 个代表样本】
{cards}
""",
    "decide_fetch": """请决定本轮是否抓取候选人详情，输出 JSON：
{{
  "action": "skip_detail/fetch_details",
  "round_type": "skip_detail/sample_detail/validate_detail/harvest_detail",
  "candidate_ids": ["候选人ID"],
  "fetch_limit": 数字,
  "sampling_strategy": {{"high_confidence": 数字, "diversity": 数字, "uncertain": 数字}},
  "match_wait_policy": {{"mode": "wait_min_results/wait_all", "min_results": 数字, "timeout_seconds": 数字}},
  "reason": "为何抓取、三个分层各抓多少、要验证什么"
}}

## 核心原则
- 卡片阶段判断的是“是否值得用详情消除不确定性”，不是最终建议状态。
- 卡片缺少技能、求职意向或项目事实只表示 unknown，不是硬冲突。
- 只有重复/无效卡片、预算为 0，或卡片能直接证明已确认硬条件冲突时才跳过。
- ranked_candidates 是紧凑排序表；routing 只是路由标签，不是匹配结论。
- disputed_candidates 只是需要重点仲裁的有争议子集，不代表其余人不应抓取。
- candidate_ids 只能来自 ranked_candidates，不能编造；fetch_limit 必须等于其长度。

## 分层抓取
- sample_detail：混合强信号、不确定和多样性样本，用详情校准卡片判断。
- validate_detail：以强信号为主，保留边缘样本，避免只验证模型已经相信的人。
- harvest_detail：尽量覆盖所有新的高潜候选人，但仍等待足够匹配结果再复盘。
- 不要使用 no_wait；复盘需要真实匹配结果，默认 wait_min_results，小样本可 wait_all。
- 如果压缩结果中的 omitted_count 大于 0，不得把未展示候选人判为不合适；
  本次只对展示的候选人作决定，并在 reason 中说明未覆盖数量。

## 预算
- 剩余详情预算：{budget}
- 预算小于等于 0 时必须 skip_detail；否则 candidate_ids 不得超过预算。

【观察结论】
{observation}

【压缩候选排序 + 有争议子集】
{cards}
""",
    "review_round": """请复盘本轮猎聘寻访，并决定停止或下一轮搜索，输出 JSON：
{{
  "action": "continue/stop",
  "summary": "本轮产出、证据缺口、根因和下一步只调整什么",
  "next_plan": {{
    "query": "下一轮搜索关键词",
    "position_filter": "职位栏收口词；query 含职位词时留空",
    "scope": "全部经历/目前职位/过往职位",
    "match_mode": "all/any",
    "filters": {{}},
    "intent": "下一轮目的",
    "expected_signal": [],
    "risk": "风险",
    "search_hypothesis_type": "core_background/target_company/transferable_scene/long_tail",
    "search_hypothesis_text": "要验证的假设"
  }},
  "evidence": {{
    "viable_count": 数字,
    "recommendation_state_counts": {{}},
    "match_count": 数字,
    "noise_root_cause": "关键词太宽/关键词太窄/词不在简历出现/维度错误/行业误匹配/职级错配/区域过窄/正常噪音/结果待完成",
    "iteration_strategy": "收紧/换语义层/换维度/定向公司/跨行业mapping/长尾狙击/放区域/保持验证/停止"
  }}
}}

## 证据边界
- matches.aggregate 是本轮完整计数；representative_matches 只是证据样本。
- 不会提供 raw_response、简历 detail 或数据库原始行，不得臆造未提供的事实。
- status_counts 出现 pending/failed，或 match_count 为 0 但本轮抓取已提交时，标为
  “结果待完成”，不能算作零产出，也不能因此提前停止或大幅改策略。
- 缺失字段是 unknown，不是未满足。优先从 top_evidence_gaps 提炼下一轮验证点。
- 判断产出使用 recommendation_state_counts：优先沟通、高潜待确认、可迁移探索都属于有效候选池，信息不足不能直接视为不匹配。

## 历史隔离
- strategy_history 只保留最近 query 和有界 RoundDigest；旧轮原文不会重复注入。
- omitted_query_count/omitted_round_digest_count 表示还有更早历史。
- 不要重复 recent_queries。完整重复校验由代码执行，不需要索取更早原文。
- 每轮原则上只改变一个主要变量，并明确本轮假设如何被证实或证伪。

## 下一轮策略
- 结果少：优先换语义层（品类词、行业词、公司词）或放宽一个筛选，不要只做同义微调。
- 结果多但噪音高：定位泛化词、岗位/职级错配或行业偏差，只收紧一个维度。
- city 非空且结果稀少：优先只去掉 city，保持 query 不变。
- target_company：公司名放 filters.company；query 写业务词，不把公司名重复放进 query。
- query 通常 1-3 个简历中会出现的词。filters 仅允许 city、active_days、education、company，严禁 work_years；年龄和性别不得自动转成平台硬筛选。
- filters.city 只能填直辖市/地级市：发现义乌、昆山、晋江、顺德、龙岗、宝安 这类县级市/区名必须映射到所属地级市（义乌→金华、昆山→苏州、晋江→泉州、顺德→佛山、龙岗→深圳），猎聘城市弹窗选不到县级市会直接报错中断。
- 只有达到明确停止条件、预算耗尽或不同假设均已验证失败时才 stop。

【should_stop】{should_stop}
【stop_reason】{stop_reason}
【target_met】{target_met}

【上一轮计划】
{plan}

【有界策略历史】
{used_queries}

【本轮匹配聚合与代表证据】
{matches}

【噪音摘要】
{noise}

【JD 摘要】
{jd}

【完整匹配标准（硬条件不得截断）】
{criteria}
""",
    "apply_user_command": """用户发送了一条实时指令，要求调整当前搜索计划。请根据指令内容对下一轮搜索计划进行专业调整，输出 JSON：
{{
  "query": "调整后搜索栏关键词",
  "position_filter": "职位栏收口词",
  "scope": "全部经历/目前职位/过往职位",
  "match_mode": "all/any",
  "filters": {{"city": [], "active_days": 30, "education": "", "company": ""}},
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
6. 工作年限、年龄和性别不要填入 filters，由后续匹配与人工确认处理。
7. filters.city 只能填直辖市/地级市。用户或 JD 提到县级市/区（义乌、昆山、晋江、顺德、龙岗、宝安…）时，必须先映射到所属地级市（义乌→金华、昆山→苏州、晋江→泉州、顺德→佛山、龙岗→深圳），否则猎聘城市弹窗选不到该城市会让整轮搜索直接报错中断。

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
  "filters": {{"city": [], "active_days": 30, "education": "", "company": ""}},
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
- 工作年限、年龄和性别不要填入 filters，由后续匹配与人工确认处理。
- filters.city 只能填直辖市/地级市。JD 或情报里出现县级市/区（义乌、昆山、晋江、顺德、龙岗、宝安…）时，必须先映射到所属地级市（义乌→金华、昆山→苏州、晋江→泉州、顺德→佛山、龙岗→深圳），否则猎聘城市弹窗选不到该城市会直接报错中断。
- 如需排除噪音，可在 query 中用减号（如 `产品经理 -助理`）。
""",
}
