"""猎聘城市筛选只支持直辖市 + 地级市；县级市必须归一化，否则城市弹窗
选不到会让整轮搜索直接报错中断。这里覆盖归一化器与 Brain 护栏的行为。
"""

from liepin_agent.agent.brain import LLMAgentBrain
from liepin_agent.agent.city_normalizer import normalize_city, normalize_city_list
from liepin_agent.agent.planner import Planner


def test_normalize_city_maps_county_to_prefecture():
    assert normalize_city("义乌") == "金华"
    assert normalize_city("昆山") == "苏州"
    assert normalize_city("晋江") == "泉州"
    assert normalize_city("顺德") == "佛山"
    assert normalize_city("慈溪") == "宁波"


def test_normalize_city_handles_prefixed_locations():
    # "浙江 金华 义乌" 这类带省市前缀，应下沉到地级市金华
    assert normalize_city("浙江金华义乌") == "金华"
    # 已经是地级市原样保留
    assert normalize_city("金华") == "金华"
    assert normalize_city("深圳") == "深圳"
    assert normalize_city("") == ""
    assert normalize_city(None) == ""


def test_normalize_city_drops_unrecognized_names_kept_for_fallback():
    # 不在映射表里的下辖区/县原样返回，由上层护栏处理（_guard 中的
    # confirmed 集合校验会把它过滤掉或被 LLM 护栏留下）
    assert normalize_city("青龙满族自治县") == "青龙满族自治县"


def test_normalize_city_list_dedupes_after_mapping():
    # 义乌和慈溪 都映射到不同的地级市；重复映射到同一地级市要去重
    assert normalize_city_list(["义乌", "东阳"]) == ["金华"]
    assert normalize_city_list(["深圳", "南山"]) == ["深圳"]
    # 逗号/分隔符的字符串也支持
    assert normalize_city_list("义乌、昆山、晋江") == ["金华", "苏州", "泉州"]


def test_planner_extract_city_scope_recognizes_county_level_in_jd():
    """JD 写"浙江金华义乌"时，确定性 Planner 也应归一化到金华，而不是漏过。"""
    assert Planner.extract_city_scope("base 浙江金华义乌") == ["金华"]
    assert Planner.extract_city_scope("昆山优先") == ["苏州"]


def test_brain_guard_search_filters_normalizes_county_city():
    """LLM 在 plan 里把县级市直接写进 filters.city 时，护栏也要先归一再做
    confirmed 集合校验，否则会让无效城市下发到猎聘。
    """
    criteria = {
        "city_scope": ["义乌"],
        "city_requirement": "义乌",
        "requirements_text": "",
        "keywords_text": "",
    }
    guarded = LLMAgentBrain._guard_search_filters(
        {"city": ["义乌"], "active_days": 30},
        criteria,
        hypothesis_type="core_background",
    )
    # 归一化后 city 应为 ["金华"]，而不是把 "义乌" 原样留下
    assert guarded.get("city") == ["金华"]


def test_brain_guard_search_filters_drops_city_when_not_confirmed():
    # JD 没有任何城市要求时，LLM 自行填的县级市也要被丢弃，避免误下沉再触发
    criteria = {
        "city_scope": [],
        "city_requirement": "无明确要求",
        "requirements_text": "",
        "keywords_text": "",
    }
    guarded = LLMAgentBrain._guard_search_filters(
        {"city": ["义乌"], "active_days": 30},
        criteria,
        hypothesis_type="core_background",
    )
    assert "city" not in guarded


def test_brain_guard_search_filters_drops_invalid_when_only_county_in_request():
    """LLM 写了县级市、但 criteria.city_scope 也是县级市时，两端都归一化后
    仍要保持匹配，落到所属地级市而不是直接丢掉用户的真实城市要求。
    """
    criteria = {
        "city_scope": ["义乌", "顺德"],
        "city_requirement": "义乌、顺德均可",
        "requirements_text": "",
        "keywords_text": "",
    }
    guarded = LLMAgentBrain._guard_search_filters(
        {"city": ["义乌"], "active_days": 30},
        criteria,
        hypothesis_type="core_background",
    )
    # 归一化后 requested=["金华"]，confirmed={"金华","佛山"}，命中 → 保留
    assert guarded.get("city") == ["金华"]