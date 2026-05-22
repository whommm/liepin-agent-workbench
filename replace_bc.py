with open(r'D:\liepin-agent-workbench\liepin_agent\agent\brain.py', 'r', encoding='utf-8') as f:
    content = f.read()

with open(r'D:\liepin-agent-workbench\actual_bc.txt', 'r', encoding='utf-8') as f:
    old_prompt = f.read()

new_prompt = '''        prompt = """请从 JD 中提取"匹配词与岗位要求"草案，输出 JSON：
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
""".format(jd=jd_text or "", notes=user_notes or "")'''

if old_prompt in content:
    content = content.replace(old_prompt, new_prompt)
    print('build_criteria prompt replaced successfully')
else:
    print('old_prompt NOT found in content')

with open(r'D:\liepin-agent-workbench\liepin_agent\agent\brain.py', 'w', encoding='utf-8') as f:
    f.write(content)
