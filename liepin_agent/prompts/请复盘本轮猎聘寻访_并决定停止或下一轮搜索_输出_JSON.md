请复盘本轮猎聘寻访，并决定停止或下一轮搜索，输出 JSON：
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