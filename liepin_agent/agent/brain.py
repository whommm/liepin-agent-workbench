"""Agent decision brains.

The product path uses ``LLMAgentBrain``. ``RuleBasedAgentBrain`` exists for
tests and emergency fallback only; it is not wired as the default runtime brain.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ..core.config import ConfigManager
from ..domain.models import (
    CandidateSummary,
    FetchDecision,
    Observation,
    RoundReview,
    SearchPlan,
)
from ..domain.states import RoundType
from ..tools.llm_client import LLMClient
from .candidate_picker import CandidatePicker
from .observer import Observer
from .planner import Planner
from .reviewer import Reviewer


AGENT_SYSTEM_PROMPT = """你是资深猎头，在猎聘平台寻找候选人。

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
"""


class RuleBasedAgentBrain:
    """Deterministic brain used by tests."""

    def __init__(self):
        self.planner = Planner()
        self.observer = Observer()
        self.picker = CandidatePicker()
        self.reviewer = Reviewer(self.planner)

    def build_criteria(self, jd_text: str, user_notes: str) -> Dict[str, object]:
        return self.planner.build_criteria(jd_text, user_notes)

    def initial_plan(
        self, jd_text: str, user_notes: str, criteria: Dict[str, object]
    ) -> SearchPlan:
        return self.planner.initial_plan(jd_text, user_notes, criteria)

    def observe_round(
        self,
        plan: SearchPlan,
        candidates: List[CandidateSummary],
        criteria: Dict[str, object],
        page_meta: Dict[str, object] | None = None,
    ) -> Observation:
        return self.observer.observe(
            candidates, plan.expected_signal or criteria.get("core_terms", [])
        )

    def decide_fetch(
        self,
        observation: Observation,
        candidates: List[CandidateSummary],
        remaining_detail_budget: int,
    ) -> FetchDecision:
        return self.picker.decide(observation, candidates, remaining_detail_budget)

    def review_round(
        self,
        previous_plan: SearchPlan,
        jd_text: str,
        used_queries: List[str],
        match_results: List[Dict[str, object]],
        noise_patterns: List[str],
        target_met: bool,
        should_stop: bool,
        stop_reason: str,
        criteria: Dict[str, object] | None = None,
    ) -> RoundReview:
        return self.reviewer.review(
            previous_plan=previous_plan,
            jd_text=jd_text,
            used_queries=used_queries,
            match_results=match_results,
            noise_patterns=noise_patterns,
            target_met=target_met,
            should_stop=should_stop,
            stop_reason=stop_reason,
        )

    def apply_user_command(
        self,
        user_command: str,
        current_plan: SearchPlan,
        criteria: Dict[str, object] | None = None,
    ) -> SearchPlan:
        return current_plan


class LLMAgentBrain:
    """LLM-backed Agent brain for real sourcing decisions.

    LLM 调用失败时直接抛异常，不再静默 fallback 到规则引擎，
    避免用户被低质量 fallback 结果误导。
    """

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    @classmethod
    def from_config(
        cls, config_manager: Optional[ConfigManager] = None
    ) -> "LLMAgentBrain":
        manager = config_manager or ConfigManager()
        config = manager.config
        return cls(
            LLMClient(
                api_base_url=config.api_base_url,
                api_key=config.api_key,
                model_name=config.model_name,
                timeout=config.timeout,
                provider=config.llm_provider or "openai",
            )
        )

    def build_criteria(self, jd_text: str, user_notes: str) -> Dict[str, object]:
        prompt = """请从 JD 中提取匹配条件，输出简洁 JSON：
{{
  "core_requirement": "一句话核心要求，如：小家电无刷电机领域，必须有无刷电机经验，CAD优先",
  "position_filter": "猎聘职位栏收口词，1-2个词。必须从JD中的真实岗位名称提取，如：车间主任、生产经理、产品经理、算法工程师。不要编造，不要写'产品' unless JD确实是产品岗",
  "search_direction": "一句话描述AI对本岗位寻访方向的理解，不是搜索关键词，是策略方向，用户可编辑修正"
}}

要求：
1. 只提取寻访真正需要的关键技能、行业、业务或场景背景，不要把 JD 里所有词都搬出来。
2. core_requirement 要短、清楚，聚焦硬门槛。
3. position_filter 务必准确：它是猎聘网站左侧职位栏的过滤条件，写错了会直接过滤掉目标候选人。
4. search_directions 是对岗位的理解方向，每方向一句话，给出不同切入角度（如收紧/放宽/跨行业）。

【JD】
{jd}

【补充说明】
{notes}
""".format(jd=jd_text or "", notes=user_notes or "")
        data = self._chat_json(prompt)
        return {
            "position_filter": str(data.get("position_filter") or "").strip(),
            "core_requirement": str(data.get("core_requirement") or "").strip(),
            "requirements_text": str(data.get("core_requirement") or "").strip(),
            "keywords_text": str(data.get("core_requirement") or "").strip(),
            "search_direction": str(data.get("search_direction") or "").strip(),
        }

    def initial_plan(
        self, jd_text: str, user_notes: str, criteria: Dict[str, object]
    ) -> SearchPlan:
        prompt = """请为猎聘找人生成第一轮搜索计划，输出 JSON：
{{
  "query": "搜索栏关键词，2-3个短词，用空格分隔（AND逻辑）。优先放项目/产品词 + 技术词，避免单泛词。如果上轮搜索零产出，本轮query必须减少到2个词，放宽条件",
  "position_filter": "职位栏收口词。必须与匹配条件中的position_filter保持一致，不要擅自更改",
  "scope": "全部经历/目前职位/过往职位",
  "match_mode": "all/any",
  "filters": {{"city": [], "active_days": 30, "education": "", "age": ""}},
  "intent": "本轮搜索目的",
  "expected_signal": ["期待在候选人卡片中看到的具体信号，至少3条"],
  "risk": "本轮可能噪音及规避方式",
  "search_hypothesis_type": "core_background/target_company/transferable_scene/long_tail",
  "search_hypothesis_text": "本轮验证的搜索假设"
}}

## 生成要求
1. query 用 AND 组合（空格分隔），2-3 个词最佳。搜索栏优先放项目/产品词或业务场景词，而非职位大词。
2. 职位栏只放岗位收口词，不要放长句。
3. 第一轮不要设定城市筛选（filters.city 留空 []）。
4. 活跃度默认 30 天内活跃，不要设太窄。
5. 年龄：如果 JD 写了年龄上限（如"40岁以内"），填 filters.age = "40"（系统会自动加 3 岁缓冲）。不写则留空。
6. **工作年限不要填入 filters**，猎聘上的年限信息不准确，由后续匹配模型判断。只有 age 可以填。
7. 如需排除噪音，可在 query 中用减号（如 `产品经理 -助理`）。
8. expected_signal 必须具体、可观察。
9. 如果匹配条件中包含 **selected_direction**（用户确认的寻访方向），必须严格按该方向生成搜索计划，不要偏离。

【JD】
{jd}

【补充说明】
{notes}

【匹配条件】
{criteria}
""".format(
            jd=jd_text or "",
            notes=user_notes or "",
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
        )
        return self._plan_from_data(self._chat_json(prompt), criteria)

    def observe_round(
        self,
        plan: SearchPlan,
        candidates: List[CandidateSummary],
        criteria: Dict[str, object],
        page_meta: Dict[str, object] | None = None,
    ) -> Observation:
        # 不再按系统预评分截断，全部候选人交给 LLM 做智能观察
        cards = [
            self._candidate_card(item)
            for item in (candidates or [])
        ]
        prompt = """请观察本轮猎聘搜索结果池，输出 JSON：
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
""".format(
            plan=json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
            page_meta=json.dumps(page_meta or {}, ensure_ascii=False, indent=2),
            cards=json.dumps(cards, ensure_ascii=False, indent=2),
        )
        data = self._chat_json(prompt)
        round_type = str(
            data.get("recommended_round_type") or RoundType.SAMPLE_DETAIL.value
        )
        if round_type not in {item.value for item in RoundType}:
            round_type = RoundType.SAMPLE_DETAIL.value
        return Observation(
            round_quality=str(data.get("round_quality") or "uncertain"),
            raw_count=int(data.get("raw_count") or len(candidates)),
            deduped_count=int(data.get("deduped_count") or len(candidates)),
            estimated_relevant_count=int(data.get("estimated_relevant_count") or 0),
            noise_patterns=self._string_list(data.get("noise_patterns"))[:8],
            positive_signals=self._string_list(data.get("positive_signals"))[:8],
            recommended_round_type=round_type,
            reason=str(data.get("reason") or "Agent 已完成本轮观察。"),
        )

    def decide_fetch(
        self,
        observation: Observation,
        candidates: List[CandidateSummary],
        remaining_detail_budget: int,
    ) -> FetchDecision:
        # 不再按系统预评分截断，全部候选人交给 LLM 决定抓取策略
        cards = [
            self._candidate_card(item)
            for item in (candidates or [])
        ]
        valid_ids = {item.id for item in candidates}
        prompt = """请决定本轮是否抓取候选人详情，输出 JSON：
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
""".format(
            budget=remaining_detail_budget,
            observation=json.dumps(observation.to_dict(), ensure_ascii=False, indent=2),
            cards=json.dumps(cards, ensure_ascii=False, indent=2),
        )
        data = self._chat_json(prompt)
        action = str(data.get("action") or "skip_detail")
        candidate_ids = [
            item
            for item in self._string_list(data.get("candidate_ids"))
            if item in valid_ids
        ]
        # 不再硬上限 15，完全信任 LLM 的抓取决策（用户明确不在乎成本）
        fetch_limit = min(
            int(data.get("fetch_limit") or len(candidate_ids)),
            remaining_detail_budget,
        )
        candidate_ids = candidate_ids[:fetch_limit]
        if not candidate_ids:
            action = "skip_detail"
        round_type = str(data.get("round_type") or observation.recommended_round_type)
        if round_type not in {item.value for item in RoundType}:
            round_type = observation.recommended_round_type
        policy = (
            data.get("match_wait_policy")
            if isinstance(data.get("match_wait_policy"), dict)
            else {}
        )
        if action == "fetch_details" and not policy:
            policy = {
                "mode": "wait_min_results",
                "min_results": min(5, len(candidate_ids)),
                "timeout_seconds": 300,
            }
        return FetchDecision(
            action=action,
            round_type=round_type,
            candidate_ids=candidate_ids,
            fetch_limit=len(candidate_ids),
            sampling_strategy=data.get("sampling_strategy")
            if isinstance(data.get("sampling_strategy"), dict)
            else {},
            match_wait_policy=policy,
            reason=str(data.get("reason") or observation.reason),
        )

    def review_round(
        self,
        previous_plan: SearchPlan,
        jd_text: str,
        used_queries: List[str],
        match_results: List[Dict[str, object]],
        noise_patterns: List[str],
        target_met: bool,
        should_stop: bool,
        stop_reason: str,
        criteria: Dict[str, object] | None = None,
    ) -> RoundReview:
        prompt = """请复盘本轮猎聘寻访，并决定停止或下一轮搜索，输出 JSON：
{{
  "action": "continue/stop",
  "summary": "复盘结论：本轮产出评估、噪音归因、策略调整方向",
  "next_plan": {{
    "query": "下一轮搜索栏关键词。注意：如果本轮零产出，query必须减到2个词，去掉最窄的那个限定词",
    "position_filter": "职位栏。必须与【匹配条件】中的position_filter保持完全一致，严禁改成其他词（如把生产管理岗改成'产品'）",
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

### 1. 停止条件（如果 should_stop 为 true，必须 action=stop）
- 已达到目标 A/B 数量
- 连续多轮低产出且无明显改进空间
- 预算耗尽
- 已穷尽合理搜索假设

### 2. 噪音归因与迭代策略
| 噪音根因 | 表现 | 迭代策略 |
|---------|------|---------|
| 关键词太宽 | 结果量大但匹配度低 | **收紧**：增加 AND 条件，加限定词 |
| 关键词太窄 | 结果极少或为空 | **放宽**：减少关键词，换同义词，或换 transferable_scene |
| 维度错误 | 结果都是错配岗位 | **换维度**：调整 position_filter |
| 行业误匹配 | 目标行业占比低 | **跨行业mapping**：用核心技能替代行业词 |
| 职级错配 | junior/senior 混错 | **调整**：加年限词或用 scope 区分 |
| 长尾不足 | 核心人才被大词淹没 | **长尾狙击**：用专业工具/细分技术词 |

### 3. 搜索假设迭代路径
- 不要重复 used_queries
- 围绕 JD 核心要求自主调整搜索词，可以换同义词、加限定词、减词
- 如果同一假设方向已验证成功，沿相邻场景扩展
- 如果同一假设方向已验证失败，切换假设类型
- 当 core_background 和 target_company 都耗尽时，优先尝试 transferable_scene

### 4. next_plan 字段要求
- query：2-3 个词 AND 组合，空格分隔，明确是收紧/放宽/换维度。**如果本轮搜索零产出，下一轮query必须减少到2个词，优先去掉最细分/最罕见的那个词**
- filters：只保留 city / active_days / education / age，**严禁填充 work_years**
- scope：target_company 优先用"目前/过往公司"；transferable_scene 用"全部经历"
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
""".format(
            should_stop=should_stop,
            stop_reason=stop_reason,
            target_met=target_met,
            plan=json.dumps(previous_plan.to_dict(), ensure_ascii=False, indent=2),
            used_queries=json.dumps(used_queries or [], ensure_ascii=False),
            matches=json.dumps(match_results or [], ensure_ascii=False, indent=2),
            noise=json.dumps(noise_patterns or [], ensure_ascii=False),
            jd=jd_text or "",
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
        )
        data = self._chat_json(prompt)
        action = "stop" if should_stop else str(data.get("action") or "continue")
        if should_stop:
            return RoundReview(
                action="stop",
                summary=str(
                    stop_reason or data.get("summary") or "Agent 已达到停止条件。"
                ),
                next_plan=None,
                evidence=data.get("evidence")
                if isinstance(data.get("evidence"), dict)
                else {},
            )
        next_plan = None
        if action != "stop" and isinstance(data.get("next_plan"), dict):
            next_plan = self._plan_from_data(data["next_plan"], {})
            if next_plan.query in set(used_queries or []):
                action = "stop"
        return RoundReview(
            action=action if action in {"continue", "stop"} else "continue",
            summary=str(data.get("summary") or stop_reason or "Agent 已完成本轮复盘。"),
            next_plan=next_plan,
            evidence=data.get("evidence")
            if isinstance(data.get("evidence"), dict)
            else {},
        )

    def apply_user_command(
        self,
        user_command: str,
        current_plan: SearchPlan,
        criteria: Dict[str, object] | None = None,
    ) -> SearchPlan:
        prompt = """用户发送了一条实时指令，要求调整当前搜索计划。请根据指令内容对下一轮搜索计划进行专业调整，输出 JSON：
{{
  "query": "调整后搜索栏关键词",
  "position_filter": "职位栏收口词",
  "scope": "全部经历/目前职位/过往职位",
  "match_mode": "all/any",
  "filters": {{"city": [], "active_days": 30, "work_years": "", "education": "", "age": ""}},
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

【用户指令】
{user_command}

【当前搜索计划】
{current_plan}

【已确认匹配词与岗位要求】
{criteria}
""".format(
            user_command=user_command,
            current_plan=json.dumps(current_plan.to_dict(), ensure_ascii=False, indent=2),
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
        )
        data = self._chat_json(prompt)
        if isinstance(data, dict) and data.get("query"):
            return self._plan_from_data(data, criteria)
        return current_plan

    def _chat_json(self, prompt: str) -> Dict[str, object]:
        raw = self.llm_client.chat(prompt, system_message=AGENT_SYSTEM_PROMPT)
        return self._parse_json(raw)

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, object]:
        text = (raw or "").strip()
        block = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if block:
            text = block.group(1)
        elif not (text.startswith("{") and text.endswith("}")):
            match = re.search(r"(\{.*\})", text, flags=re.DOTALL)
            if match:
                text = match.group(1)
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Agent JSON 必须是对象")
        return data

    @staticmethod
    def _plan_from_data(
        data: Dict[str, object], criteria: Dict[str, object]
    ) -> SearchPlan:
        filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
        if "city" not in filters and criteria.get("city_scope"):
            filters["city"] = criteria.get("city_scope")
        if "active_days" not in filters:
            filters["active_days"] = 30
        return SearchPlan(
            query=str(data.get("query") or "").strip(),
            position_filter=str(
                data.get("position_filter") or criteria.get("position_filter") or ""
            ),
            scope=str(data.get("scope") or "全部经历"),
            match_mode=str(data.get("match_mode") or "all"),
            filters=filters,
            intent=str(data.get("intent") or "Agent 生成的搜索计划"),
            expected_signal=LLMAgentBrain._string_list(
                data.get("expected_signal") or criteria.get("core_terms")
            )[:12],
            risk=str(data.get("risk") or ""),
            search_hypothesis_type=str(
                data.get("search_hypothesis_type") or "core_background"
            ),
            search_hypothesis_text=str(
                data.get("search_hypothesis_text") or data.get("intent") or ""
            ),
        )

    @staticmethod
    def _candidate_card(candidate: CandidateSummary) -> Dict[str, object]:
        return {
            "id": candidate.id,
            "name": candidate.name,
            "title": candidate.current_title,
            "company": candidate.current_company,
            "city": candidate.city,
            "work_years": candidate.work_years,
            "education": candidate.education,
            "summary": candidate.summary_text,
            "raw_text": candidate.raw_text or candidate.summary_text,
            "pre_score": candidate.pre_score,
            "pre_score_reasons": candidate.pre_score_reasons,
            "card_decision": candidate.card_decision,
            "card_signals": candidate.card_signals,
            "card_risks": candidate.card_risks,
            "card_reason": candidate.card_reason,
            "result_index": candidate.result_index,
        }

    @staticmethod
    def _string_list(value: object) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [
                item.strip() for item in re.split(r"[、,，;\n]+", value) if item.strip()
            ]
        return []
