请决定本轮是否抓取候选人详情，输出 JSON：
{{
  "action": "skip_detail/fetch_details",
  "round_type": "skip_detail/sample_detail/validate_detail/harvest_detail",
  "candidate_ids": ["候选人ID"],
  "fetch_limit": 数字,
  "sampling_strategy": {{"high_confidence": 数字, "diversity": 数字, "uncertain": 数字}},
  "match_wait_policy": {{"mode": "no_wait/wait_min_results/wait_all", "min_results": 数字, "timeout_seconds": 数字}},
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