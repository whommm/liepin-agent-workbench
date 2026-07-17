from liepin_agent.core.liepin_search_service import LiepinSearchService


def test_clean_candidate_lines_keeps_company_title_and_city_aligned():
    service = LiepinSearchService.__new__(LiepinSearchService)

    cleaned, name, age, title, company, city, work_years, education, gender = (
        service._clean_candidate_lines(
            [
                "徐**",
                "39岁",
                "工作16年",
                "本科",
                "西安-莲湖区",
                "求职期望：",
                "大连销售总监",
                "陕西澜山能源有限责任公司 · 天然气销售",
            ]
        )
    )

    assert cleaned
    assert name == "徐**"
    assert age == "39岁"
    assert work_years == "16年"
    assert education == "本科"
    assert city == "西安-莲湖区"
    assert company == "陕西澜山能源有限责任公司"
    assert title == "天然气销售"


def test_clean_candidate_lines_does_not_treat_work_as_city():
    service = LiepinSearchService.__new__(LiepinSearchService)

    _, _, _, title, company, city, work_years, _, _ = service._clean_candidate_lines(
        [
            "王**",
            "39岁",
            "工作16年",
            "本科",
            "大连",
            "Prysmian · 销售经理",
        ]
    )

    assert work_years == "16年"
    assert city == "大连"
    assert company == "Prysmian"
    assert title == "销售经理"
