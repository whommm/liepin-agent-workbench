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

# Let's try reading the exact bytes for build_criteria part
idx1 = content.find('    def build_criteria')
idx2 = content.find('    def initial_plan')
actual_build = content[idx1:idx2]
print('=== ACTUAL BUILD START ===')
print(repr(actual_build[:300]))
print('=== ACTUAL BUILD END ===')

# Check if old_build matches
if old_build in content:
    print('old_build found by string match')
else:
    print('old_build NOT found by string match')
    # Try finding the prompt part only
    prompt_start = content.find('请从 JD 中提取')
    print('prompt_start index:', prompt_start)
    if prompt_start != -1:
        print(repr(content[prompt_start-30:prompt_start+50]))
