请为猎聘找人生成第一轮搜索计划，输出 JSON：
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