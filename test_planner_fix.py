import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from liepin_agent.agent.planner import Planner

planner = Planner()

test_cases = [
    ("小家电无刷电机研发经理", "岗位需求就是必须有无刷电机经验，年龄最大42岁，公司是做小家电的，这个岗位就是做无刷电机研发，要男的，要本科\n研发经理/副经理\n岗位职责：\n1、建立产品设计规范，负责所有开发项目的技术指导\n2、熟悉无刷电机驱动优先；"),
    ("小强电机工程师", "负责无刷电机结构、性能设计开发，独立完成3D建模与2D图纸输出；\n主导PFMEA和DFMEA编写，识别并解决潜在失效风险;\n能够根据产品设计仿真合理的电机磁链，设计合理的电机铁芯、绕组等;"),
    ("轨道交通销售经理", "产品是高铁和铁路用的机器人和公务小车\n竞对名单：主导、盛锴、运达、诺丽、唐源、鼎汉\n负责重庆、上海市场的最好\n要原来就在这个行业的，认识轨道交通领导的人才有用"),
    ("文创潮玩产品经理", "负责文创产品设计，潮玩IP衍生品开发，3D打印原型，供应链量产管理"),
]

with open('test_planner_result.txt', 'w', encoding='utf-8') as f:
    for title, jd in test_cases:
        f.write(f"\n{'='*60}\n")
        f.write(f"岗位: {title}\n")
        f.write(f"{'='*60}\n")
        terms = planner.extract_domain_terms(jd)
        f.write(f"提取关键词: {terms}\n")
        plan = planner.initial_plan(jd)
        f.write(f"搜索query: {plan.query}\n")
        f.write(f"职位filter: {plan.position_filter}\n")
        criteria = planner.build_criteria(jd)
        f.write(f"keywords_text:\n{criteria['keywords_text']}\n")

print("Done, see test_planner_result.txt")
