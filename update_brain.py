import re

path = r'D:\liepin-agent-workbench\liepin_agent\agent\brain.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace build_criteria
old_build = '''    def build_criteria(self, jd_text: str, user_notes: str) -> Dict[str, object]:
        prompt = """请从 JD 中提取"匹配词与岗位要求"草案，输出 JSON：
{{
  "keywords_text": "每行一个关键词，5-12个",
  "requirements_text": "一段简洁岗位要求描述",
  "position_filter": "职位栏收口词"
}}

要求：
1. 只提取寻访真正需要的关键技能、行业、业务、产品、客户或场景背景。
2. 不输出评分权重。
3. 不拆复杂硬性/软性结构。
4. 不要把 JD 里的所有词都搬出来。
5. requirements_text 要短、清楚、方便人类编辑确认。

【JD】
{jd}

【补充说明】
{notes}
""".format(jd=jd_text or "", notes=user_notes or "")
        data = self._chat_json(
            prompt, self.fallback.build_criteria(jd_text, user_notes)
        )
        keywords = str(data.get("keywords_text") or "").strip()
        if not keywords:
            keywords = "\\n".join(self._string_list(data.get("core_terms"))[:12])
        keyword_terms = self._string_list(keywords)[:12]
        return {
            "position_filter": str(data.get("position_filter") or "产品"),
            "core_terms": keyword_terms,
            "negative_terms": self._string_list(data.get("negative_terms"))[:12],
            "hard_requirements": self._string_list(data.get("hard_requirements"))[:12],
            "city_scope": self._string_list(data.get("city_scope"))[:8],
            "keywords_text": "\\n".join(keyword_terms),
            "requirements_text": str(data.get("requirements_text") or "").strip()
            or self.fallback.build_criteria(jd_text, user_notes).get(
                "requirements_text", ""
            ),
        }'''

new_build = '''    def build_criteria(self, jd_text: str, user_notes: str) -> Dict[str, object]:
        prompt = """请从 JD 中提取"匹配词与岗位要求"草案，输出 JSON：
{{
  "position_filter": "职位栏收口词，1-2个词",
  "position_type": "岗位类型：技术/管理/销售/职能/复合",
  "hard_requirements": ["硬性指标，如学历底线、年限要求、资质证书"],
  "city_scope": ["目标城市列表"],
  "direct_keywords": ["直接关键词：岗位名称变体、核心技能、目标公司"],
  "indirect_keywords": ["间接关键词：项目经验、业务场景、行业术语"],
  "long_tail_keywords": ["长尾关键词：专业工具、认证、细分技术、专有名词"],
  "keywords_text": "每行一个关键词，8-15个，按直接→间接→长尾分层排列",
  "requirements_text": "一段简洁岗位要求描述，聚焦真正需要的关键能力",
  "negative_terms": ["应排除的噪音词，如'实习'、'应届'、'助理'等"]
}}

## 提取要求（按岗位类型差异化）

### 通用规则
1. **只提取寻访真正需要的关键技能、行业、业务、产品、客户或场景背景**。不要把 JD 里的所有词都搬出来。
2. **区分直接/间接/长尾三层关键词**：
   - 直接词：JD 表面能看到的岗位名称、技能名称。
   - 间接词：从职责本质推导出的业务场景词、项目词。
   - 长尾词：专业工具、认证、细分技术，转化率通常是大词的 2.8 倍。
3. **职称穷尽**：同一岗位的不同叫法都要列出（如"财务总监=CFO=VP Finance"）。
4. **不输出评分权重，不拆复杂硬性/软性结构**。
5. **requirements_text 要短、清楚、方便人类编辑确认**。

### 技术岗补充规则（position_type=技术）
- 按四级递进提取：通用语言/框架 → 工具/平台 → 业务场景 → 引擎/基础设施。
- 关注技术栈生态链：不要只提"Spring Cloud"，要关联容器化、监控、链路追踪等立体化关键词。

### 管理岗补充规则（position_type=管理）
- 必须提取团队规模词（如"10人以上""事业部"）。
- 必须提取业务指标词（如"从0到1""业务线搭建""P&L"）。
- 必须提取管理方法论词（如"OKR""矩阵式管理""变革管理"）。

### 销售/市场岗补充规则（position_type=销售）
- 提取"在哪里卖"的行业词 + "怎么卖"的渠道词 + "卖得怎样"的业绩词。
- 业绩词示例：ARR、NDR、客单价、续约率、渠道拓展、KA管理。

### 职能岗补充规则（position_type=职能）
- 提取专业资质（如 CPA、CFA、法律职业资格）。
- 提取业务支持场景（如"IPO经验""并购""组织发展"）。

【JD】
{jd}

【补充说明】
{notes}
""".format(jd=jd_text or "", notes=user_notes or "")
        data = self._chat_json(
            prompt, self.fallback.build_criteria(jd_text, user_notes)
        )
        keywords = str(data.get("keywords_text") or "").strip()
        if not keywords:
            merged = []
            merged.extend(self._string_list(data.get("direct_keywords")))
            merged.extend(self._string_list(data.get("indirect_keywords")))
            merged.extend(self._string_list(data.get("long_tail_keywords")))
            if not merged:
                merged = self._string_list(data.get("core_terms"))
            keywords = "\\n".join(merged[:15])
        keyword_terms = self._string_list(keywords)[:15]
        return {
            "position_filter": str(data.get("position_filter") or "产品"),
            "position_type": str(data.get("position_type") or ""),
            "core_terms": keyword_terms,
            "direct_keywords": self._string_list(data.get("direct_keywords"))[:12],
            "indirect_keywords": self._string_list(data.get("indirect_keywords"))[:12],
            "long_tail_keywords": self._string_list(data.get("long_tail_keywords"))[:12],
            "negative_terms": self._string_list(data.get("negative_terms"))[:12],
            "hard_requirements": self._string_list(data.get("hard_requirements"))[:12],
            "city_scope": self._string_list(data.get("city_scope"))[:8],
            "keywords_text": "\\n".join(keyword_terms),
            "requirements_text": str(data.get("requirements_text") or "").strip()
            or self.fallback.build_criteria(jd_text, user_notes).get(
                "requirements_text", ""
            ),
        }'''

if old_build in content:
    content = content.replace(old_build, new_build)
    print('build_criteria replaced')
else:
    print('build_criteria NOT found')

# 2. Replace initial_plan
old_initial = '''    def initial_plan(
        self, jd_text: str, user_notes: str, criteria: Dict[str, object]
    ) -> SearchPlan:
        prompt = """请为猎聘找人生成第一轮搜索计划，输出 JSON：
{{
  "query": "搜索栏关键词，2-4个短词，用空格分隔",
  "position_filter": "职位栏收口词",
  "scope": "全部经历/目前职位",
  "match_mode": "all/any",
  "filters": {{"city": [], "active_days": 7, "work_years": "", "education": ""}},
  "intent": "本轮搜索目的",
  "expected_signal": ["期待在候选人卡片中看到的信号"],
  "risk": "本轮可能噪音",
  "search_hypothesis_type": "core_background/target_company/transferable_scene",
  "search_hypothesis_text": "本轮验证的搜索假设"
}}

要求：
1. 第一轮不要用泛词单搜，例如 产品、设计、管理。
2. 搜索栏优先放业务场景词或项目词。
3. 职位栏只放岗位收口词。
4. 只能基于已确认匹配词与岗位要求生成搜索假设，不要发明新的岗位要求。

【JD】
{jd}

【补充说明】
{notes}

【已确认匹配词与岗位要求】
{criteria}
""".format(
            jd=jd_text or "",
            notes=user_notes or "",
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
        )'''

new_initial = '''    def initial_plan(
        self, jd_text: str, user_notes: str, criteria: Dict[str, object]
    ) -> SearchPlan:
        prompt = """请为猎聘找人生成第一轮搜索计划，输出 JSON：
{{
  "query": "搜索栏关键词，2-4个短词，用空格分隔（AND逻辑）。如需同义词扩展，优先将核心词组合成AND短语，避免单泛词",
  "position_filter": "职位栏收口词",
  "scope": "全部经历/目前职位/过往职位",
  "match_mode": "all/any",
  "filters": {{"city": [], "active_days": 7, "work_years": "", "education": ""}},
  "intent": "本轮搜索目的，明确是验证核心背景、目标公司还是可迁移场景",
  "expected_signal": ["期待在候选人卡片中看到的具体信号，至少3条"],
  "risk": "本轮可能噪音及规避方式",
  "search_hypothesis_type": "core_background/target_company/transferable_scene/long_tail",
  "search_hypothesis_text": "本轮验证的搜索假设，用一句话说明你在验证什么"
}}

## 生成要求

### 1. 搜索策略选择（必须明确）
- **core_background**：验证核心业务场景是否存在可用候选人。query 用"核心技能 + 行业/业务词"组合。
- **target_company**：定向挖目标公司/竞品。配合 scope="目前公司" 或 "过往公司"使用。
- **transferable_scene**：跨行业mapping，验证可迁移技能候选人池。query 用"核心技能 + 通用业务词"。
- **long_tail**：用长尾精准词直接狙击专项人才。适合高管或稀缺技术岗。

### 2. 猎聘语法合规
- **query 必须用 AND 组合（空格分隔）**，第一轮不要用泛词单搜，例如"产品""设计""管理"。
- **搜索栏优先放业务场景词或项目词**，而非职位大词。
- **职位栏只放岗位收口词**，不要放长句。
- **如需排除噪音**，可在 query 中体现排除逻辑（如 `产品经理 -助理`）。

### 3. Scope 选择策略
- **全部经历**：适合"做过即可"的硬技能/项目经验（绝大多数首轮搜索）。
- **目前职位**：适合"必须当前在岗"的定向挖猎（如竞品mapping）。
- **过往职位**：适合"曾经在大厂/目标公司干过，现在已晋升/转岗"的场景。

### 4. 先窄后宽原则
- 第一轮优先用 **先窄后宽**：用"核心职能 + 核心技能 + 核心场景"三维 AND 锁定。
- 如果预计结果可能过少，在 risk 中注明，并准备放宽方案。

### 5. 限制
- 只能基于已确认匹配词与岗位要求生成搜索假设，不要发明新的岗位要求。
- expected_signal 必须具体、可观察（如"当前公司属于新能源整车厂""简历中出现BMS或电池管理系统相关项目"）。

【JD】
{jd}

【补充说明】
{notes}

【已确认匹配词与岗位要求】
{criteria}
""".format(
            jd=jd_text or "",
            notes=user_notes or "",
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
        )'''

if old_initial in content:
    content = content.replace(old_initial, new_initial)
    print('initial_plan replaced')
else:
    print('initial_plan NOT found')

# 3. Replace observe_round
old_observe = '''        prompt = """请观察本轮猎聘搜索结果池，输出 JSON：
{{
  "round_quality": "empty/low/uncertain/medium/high",
  "raw_count": "数字",
  "deduped_count": "数字",
  "estimated_relevant_count": "数字",
  "noise_patterns": ["噪音类型"],
  "positive_signals": ["正向信号"],
  "recommended_round_type": "skip_detail/sample_detail/validate_detail/harvest_detail",
  "reason": "判断依据"
}}

【本轮搜索计划】
{plan}

【匹配标准】
{criteria}

【候选人卡片样本】
{cards}
""".format(
            plan=json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
            cards=json.dumps(cards, ensure_ascii=False, indent=2),
        )'''

new_observe = '''        prompt = """请观察本轮猎聘搜索结果池，输出 JSON：
{{
  "round_quality": "empty/low/uncertain/medium/high",
  "raw_count": "数字",
  "deduped_count": "数字",
  "estimated_relevant_count": "数字",
  "noise_patterns": ["噪音类型"],
  "positive_signals": ["正向信号"],
  "recommended_round_type": "skip_detail/sample_detail/validate_detail/harvest_detail",
  "reason": "判断依据，包含质量评估、噪音归因、策略建议"
}}

## 观察框架

### 质量分级标准
- **empty**：搜索结果为空或仅0-2人。→ 关键词过窄或条件冲突。
- **low**：结果数≥8但卡片无有效信号，或 relevant_count=0。→ 建议 skip_detail。
- **uncertain**：有少量潜在信号（2-4个 relevant），但不确定是真实匹配还是标题党。→ 建议 sample_detail（2-4人）。
- **medium**：有多个有效信号（≥5个 relevant），卡片层面出现目标行业/技能/公司。→ 建议 validate_detail（5-10人）。
- **high**：大量强信号（≥10个 strong），目标人才密集。→ 建议 harvest_detail（最多15人）。

### 噪音类型识别（常见）
请从以下维度识别噪音，不要只匹配硬规则：
1. **岗位错配**：销售岗位混入、客服/服务类混入、运营背景偏多。
2. **职级错配**：低年限/实习/助理级简历混入 senior 搜索。
3. **行业错配**：目标行业候选人占比过低，出现大量无关行业。
4. **内容噪音**："内容产品""新媒体"等泛岗位混入技术/管理岗搜索。
5. **技能漂移**：JD 要求"算法"，结果多为"数据分析""BI"等非核心算法。
6. **公司偏差**：目标公司未出现，或出现大量非目标层级公司。
7. **地域偏差**：城市分布与目标严重不符。

### 正向信号识别
- 目标行业/细分领域出现频率。
- 核心技能词在简历摘要中的出现率。
- 目标公司/竞品公司出现数量。
- 职位 title 与岗位收口词的匹配度。
- 项目经验中是否出现目标业务场景词。

【本轮搜索计划】
{plan}

【匹配标准】
{criteria}

【候选人卡片样本】
{cards}
""".format(
            plan=json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
            cards=json.dumps(cards, ensure_ascii=False, indent=2),
        )'''

if old_observe in content:
    content = content.replace(old_observe, new_observe)
    print('observe_round replaced')
else:
    print('observe_round NOT found')

# 4. Replace decide_fetch
old_fetch = '''        prompt = """请决定本轮是否抓取候选人详情，输出 JSON：
{{
  "action": "skip_detail/fetch_details",
  "round_type": "skip_detail/sample_detail/validate_detail/harvest_detail",
  "candidate_ids": ["候选人ID"],
  "fetch_limit": "数字",
  "sampling_strategy": {{"high_confidence": "数字", "diversity": "数字", "uncertain": "数字"}},
  "match_wait_policy": {{"mode": "no_wait/wait_min_results/wait_all", "min_results": "数字", "timeout_seconds": "数字"}},
  "reason": "为什么这么抓"
}}

要求：
1. 明显低质轮可以不抓。
2. 探测轮抓 2-4 个，验证轮抓 5-10 个，收割轮最多 15 个。
3. 不要只抓 Top N，要混入多样性和不确定样本。
4. candidate_ids 必须来自候选人卡片列表。
5. 剩余总预算：{budget}

【观察结论】
{observation}

【候选人卡片】
{cards}
""".format(
            budget=remaining_detail_budget,
            observation=json.dumps(observation.to_dict(), ensure_ascii=False, indent=2),
            cards=json.dumps(cards, ensure_ascii=False, indent=2),
        )'''

new_fetch = '''        prompt = """请决定本轮是否抓取候选人详情，输出 JSON：
{{
  "action": "skip_detail/fetch_details",
  "round_type": "skip_detail/sample_detail/validate_detail/harvest_detail",
  "candidate_ids": ["候选人ID"],
  "fetch_limit": "数字",
  "sampling_strategy": {{"high_confidence": "数字", "diversity": "数字", "uncertain": "数字"}},
  "match_wait_policy": {{"mode": "no_wait/wait_min_results/wait_all", "min_results": "数字", "timeout_seconds": "数字"}},
  "reason": "详细说明：为什么这么抓、抽样逻辑、预期验证什么"
}}

## 抓取策略指南

### 各轮次抓取上限
- **skip_detail**：明显低质轮、空结果轮、或预算耗尽时不抓。
- **sample_detail（探测轮）**：抓 2-4 个。目的是快速验证搜索假设是否成立。必须混合：1个最高置信 + 1个多样性（不同公司/背景） + 1-2个不确定样本。
- **validate_detail（验证轮）**：抓 5-10 个。目的是在 medium 质量池中验证真实匹配度。优先抓卡片信号最明确的，同时保留 1-2 个边缘样本防止漏判。
- **harvest_detail（收割轮）**：抓 8-15 个。目的是在高密度池中批量获取匹配结果。可以放宽抽样范围，优先抓未被抓过的新面孔。

### 抽样原则
1. **不要只抓 Top N 预评分**。预评分基于关键词匹配，可能漏掉"跨行业可迁移"或"简历写法不同但实质匹配"的候选人。
2. **高置信样本**：card_decision="fetch" 且 card_signals≥2 的候选人。
3. **多样性样本**：来自不同公司、不同职级段、不同业务线的候选人，避免同一公司抓多人。
4. **不确定样本**：card_decision="maybe" 但有一项独特亮点（如目标公司背景、罕见项目经验）的候选人。
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
        )'''

if old_fetch in content:
    content = content.replace(old_fetch, new_fetch)
    print('decide_fetch replaced')
else:
    print('decide_fetch NOT found')

# 5. Replace review_round
old_review = '''        prompt = """请复盘本轮猎聘寻访，并决定停止或下一轮搜索，输出 JSON：
{{
  "action": "continue/stop",
  "summary": "复盘结论",
  "next_plan": {{
    "query": "下一轮搜索栏",
    "position_filter": "职位栏",
    "scope": "全部经历/目前职位",
    "match_mode": "all/any",
    "filters": {{}},
    "intent": "下一轮目的",
    "expected_signal": [],
    "risk": "风险",
    "search_hypothesis_type": "core_background/target_company/transferable_scene",
    "search_hypothesis_text": "下一轮验证的搜索假设"
  }},
  "evidence": {{}}
}}

如果 should_stop 为 true，必须 action=stop。
下一轮 query 不要重复 used_queries。
下一轮只能围绕已确认匹配词与岗位要求组合、放宽或收紧，不要发明新的岗位要求。

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
""".format(
            should_stop=should_stop,
            stop_reason=stop_reason,
            target_met=target_met,
            plan=json.dumps(previous_plan.to_dict(), ensure_ascii=False, indent=2),
            used_queries=json.dumps(used_queries or [], ensure_ascii=False),
            matches=json.dumps(match_results or [], ensure_ascii=False, indent=2),
            noise=json.dumps(noise_patterns or [], ensure_ascii=False),
            jd=jd_text or "",
        )'''

new_review = '''        prompt = """请复盘本轮猎聘寻访，并决定停止或下一轮搜索，输出 JSON：
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
""".format(
            should_stop=should_stop,
            stop_reason=stop_reason,
            target_met=target_met,
            plan=json.dumps(previous_plan.to_dict(), ensure_ascii=False, indent=2),
            used_queries=json.dumps(used_queries or [], ensure_ascii=False),
            matches=json.dumps(match_results or [], ensure_ascii=False, indent=2),
            noise=json.dumps(noise_patterns or [], ensure_ascii=False),
            jd=jd_text or "",
        )'''

if old_review in content:
    content = content.replace(old_review, new_review)
    print('review_round replaced')
else:
    print('review_round NOT found')

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')
