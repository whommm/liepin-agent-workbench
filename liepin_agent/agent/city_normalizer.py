"""城市名归一化：把县级市/区映射到猎聘支持的地级市/直辖市。

猎聘"期望城市/目前城市"筛选只接受直辖市和地级市级别，无法选中
县级市（如浙江金华的义乌、苏州的昆山）。当 JD 或 LLM 把这类县级市
直接写进 ``filters.city`` 时，猎聘城市弹窗找不到对应选项会抛出
``未找到城市选项`` 错误并整轮中断。

这里把常见的县级市、地名变体统一下沉到所属地级市，作为提示词之外的
兜底护栏：即便 LLM 漏掉归一化规则，运行期也不会把无效城市下发到猎聘。
"""

from __future__ import annotations

import re
from typing import Iterable, List

# 县级市 / 常见区县名 → 所属地级市/直辖市。
_COUNTY_TO_PREFECTURE = {
    # 浙江
    "义乌": "金华", "东阳": "金华", "永康": "金华", "兰溪": "金华",
    "诸暨": "绍兴", "嵊州": "绍兴", "新昌": "绍兴",
    "慈溪": "宁波", "余姚": "宁波", "宁海": "宁波", "象山": "宁波",
    "瑞安": "温州", "乐清": "温州", "永嘉": "温州", "平阳": "温州",
    "桐乡": "嘉兴", "海宁": "嘉兴", "平湖": "嘉兴", "海盐": "嘉兴",
    "江阴": "无锡", "宜兴": "无锡",
    # 江苏
    "昆山": "苏州", "张家港": "苏州", "常熟": "苏州", "太仓": "苏州",
    "邳州": "徐州", "新沂": "徐州",
    "溧阳": "常州", "金坛": "常州",
    "仪征": "扬州", "高邮": "扬州",
    "丹阳": "镇江", "扬中": "镇江", "句容": "镇江",
    "泰兴": "泰州", "靖江": "泰州", "兴化": "泰州",
    "启东": "南通", "海门": "南通", "如皋": "南通",
    "东台": "盐城",
    # 广东
    "顺德": "佛山", "南海": "佛山", "三水": "佛山", "高明": "佛山",
    "增城": "广州", "从化": "广州",
    "龙岗": "深圳", "宝安": "深圳", "南山": "深圳", "福田": "深圳",
    "罗湖": "深圳", "盐田": "深圳", "龙华": "深圳", "光明": "深圳",
    "坪山": "深圳",
    "潮安": "潮州", "潮阳": "汕头", "澄海": "汕头",
    "普宁": "揭阳",
    "高州": "茂名", "化州": "茂名", "信宜": "茂名",
    "四会": "肇庆",
    "鹤山": "江门", "开平": "江门", "台山": "江门", "恩平": "江门",
    # 福建
    "晋江": "泉州", "石狮": "泉州", "南安": "泉州", "惠安": "泉州",
    "福清": "福州", "长乐": "福州",
    "龙海": "漳州",
    "邵武": "南平", "武夷山": "南平", "建瓯": "南平",
    # 山东
    "章丘": "济南",
    "即墨": "青岛", "胶州": "青岛", "平度": "青岛", "莱西": "青岛",
    "龙口": "烟台", "莱阳": "烟台", "莱州": "烟台", "招远": "烟台",
    "蓬莱": "烟台",
    "青州": "濰坊", "诸城": "濰坊", "寿光": "濰坊", "高密": "濰坊",
    "乳山": "威海", "文登": "威海", "荣成": "威海",
    "邹平": "滨州",
    "肥城": "泰安",
    "莱芜": "济南",
    # 四川/重庆
    "都江堰": "成都", "彭州": "成都", "邛崃": "成都", "崇州": "成都",
    "江津": "重庆", "合川": "重庆", "永川": "重庆", "南川": "重庆",
    "綦江": "重庆",
    # 河北
    "辛集": "石家庄", "晋州": "石家庄",
    "三河": "廊坊", "霸州": "廊坊",
    "任丘": "沧州", "黄骅": "沧州", "河间": "沧州",
    "遵化": "唐山", "迁安": "唐山",
    # 河南
    "巩义": "郑州", "新郑": "郑州", "新密": "郑州", "登封": "郑州",
    "偃师": "洛阳",
    "林州": "安阳",
    "禹州": "许昌", "长葛": "许昌",
    "永城": "商丘",
    "邓州": "南阳",
    # 湖南/湖北
    "浏阳": "长沙", "宁乡": "长沙",
    "仙桃": "武汉", "天门": "武汉", "潜江": "武汉",
    "大冶": "黄石",
    "钟祥": "荆门",
    "麻城": "黄冈",
    # 安徽/江西
    "巢湖": "合肥", "庐江": "合肥",
    "瑞昌": "九江",
    "贵溪": "鹰潭",
    "乐平": "景德镇",
    # 辽宁
    "瓦房店": "大连", "普兰店": "大连", "庄河": "大连",
    "海城": "鞍山",
    "凌海": "锦州",
}

# 带地理描述的"省-地级市-县级市"或"地级市-县级市"前缀，截取到地级市。
_PROVINCE_PREFIX_RE = re.compile(
    r"([\u4e00-\u9fa5]+?)[省特别自治区][\s·、,，-]+([\u4e00-\u9fa5]{2,4})"
)


def normalize_city(value: object) -> str:
    """Normalize a single city string to a Liepin-supported prefecture-level name.

    * None/空 → "" (留空，不发到猎聘)
    * "浙江金华义乌" / "义乌" 等命中县级市 → 下沉到所属地级市（金华）
    * "省 · 市형" 带分隔符的，取地级市那一段
    * 其他无法识别 → 原样返回，交给上层护栏处理（很可能再被
      _guard_search_filters 丢弃）
    """
    if value is None:
        return ""
    text = str(value).strip().strip("·、,").strip()
    if not text:
        return ""

    # 1) 完全相等 → 直接命中
    if text in _COUNTY_TO_PREFECTURE:
        return _COUNTY_TO_PREFECTURE[text]

    # 2) 子串命中：覆盖 "浙江金华义乌"、"base 义乌优先" 等连写场景。
    #    items 来自 LLM 单字段或 planner 切片后的城市名，长度受控，
    #    子串误判风险低；遇县级市都应当沉降到地级市。
    for county, prefecture in _COUNTY_TO_PREFECTURE.items():
        if county in text:
            return prefecture

    # 3) "浙江 · 金华" 之类带分隔符的写法：取最后一段
    cleaned = re.sub(r"中国|省|特别行政区|自治区|市", "", text)
    cleaned = [p for p in re.split(r"[\s·、,，\-/]+", cleaned) if p]
    if len(cleaned) >= 2:
        tail = cleaned[-1]
        if tail in _COUNTY_TO_PREFECTURE:
            return _COUNTY_TO_PREFECTURE[tail]
        candidate = cleaned[-2]
        if candidate in _COUNTY_TO_PREFECTURE.values():
            return candidate

    return text


def normalize_city_list(value: object) -> List[str]:
    """Normalize a list/comma-separated string of cities, drop empties and dupes."""
    if isinstance(value, (list, tuple, set)):
        items: Iterable[str] = (str(item) for item in value)
    else:
        raw = str(value or "").strip()
        if not raw:
            return []
        items = re.split(r"[、,，;\n/]+", raw)
    normalized: List[str] = []
    for item in items:
        city = normalize_city(item)
        if city and city not in normalized:
            normalized.append(city)
    return normalized