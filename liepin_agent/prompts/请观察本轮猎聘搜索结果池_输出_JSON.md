请观察本轮猎聘搜索结果池，输出 JSON：
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
