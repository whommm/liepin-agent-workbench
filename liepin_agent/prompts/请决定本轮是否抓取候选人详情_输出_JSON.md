请决定本轮是否抓取候选人详情，输出 JSON：
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
