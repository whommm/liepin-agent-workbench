path = r'D:\liepin-agent-workbench\liepin_agent\agent\brain.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 0-indexed line numbers
def line_index(n): return n - 1

# Replace build_criteria (lines 160-199, 0-indexed 159-199)
# Keep line 160 (def header), replace body until line before initial_plan
build_start = line_index(160)
build_end = line_index(201)  # exclusive, initial_plan starts at 201

new_build_body = '''        prompt = """请从 JD 中提取"匹配词与岗位要求"草案，输出 JSON：
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
        }

'''

lines[build_start+1:build_end] = [new_build_body]

# Replace observe_round (lines 263-317, 0-indexed 262-317)
observe_start = line_index(263)
observe_end = line_index(318)  # exclusive, decide_fetch starts at 318

new_observe_body = '''        cards = [
            self._candidate_card(item)
            for item in sorted(candidates, key=lambda c: -c.pre_score)[:40]
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
        )
        fallback_observation = self.fallback.observe_round(
            plan, candidates, criteria
        ).to_dict()
        data = self._chat_json(prompt, fallback_observation)
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

'''

lines[observe_start+1:observe_end] = [new_observe_body]

# Replace decide_fetch (lines 318-400, 0-indexed 317-400)
fetch_start = line_index(318)
fetch_end = line_index(401)  # exclusive, review_round starts at 401

new_fetch_body = '''        cards = [
            self._candidate_card(item)
            for item in sorted(candidates, key=lambda c: -c.pre_score)[:50]
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
        )
        fallback_decision = self.fallback.decide_fetch(
            observation, candidates, remaining_detail_budget
        ).to_dict()
        data = self._chat_json(prompt, fallback_decision)
        action = str(data.get("action") or "skip_detail")
        candidate_ids = [
            item
            for item in self._string_list(data.get("candidate_ids"))
            if item in valid_ids
        ]
        fetch_limit = min(
            int(data.get("fetch_limit") or len(candidate_ids)),
            remaining_detail_budget,
            15,
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
                "timeout_seconds": 180,
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

'''

lines[fetch_start+1:fetch_end] = [new_fetch_body]

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('All replacements done')
