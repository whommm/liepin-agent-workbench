from pathlib import Path

from liepin_agent.prompts.loader import PromptLoader, _BUILT_IN_PROMPTS


PROMPTS_DIR = Path(__file__).parents[1] / "liepin_agent" / "prompts" / "txt"


def test_all_txt_prompts_have_identical_built_in_fallbacks(tmp_path, monkeypatch):
    """A package without txt data must use the same prompt contracts as dev."""

    monkeypatch.setattr(PromptLoader, "_instance", None)
    fallback_loader = PromptLoader(tmp_path)

    prompt_files = sorted(PROMPTS_DIR.glob("*.txt"))
    assert prompt_files
    for prompt_file in prompt_files:
        expected = prompt_file.read_text(encoding="utf-8")
        assert prompt_file.stem in _BUILT_IN_PROMPTS
        assert fallback_loader.raw(prompt_file.stem) == expected


def test_missing_txt_fallback_still_formats_prompt_variables(tmp_path, monkeypatch):
    monkeypatch.setattr(PromptLoader, "_instance", None)
    fallback_loader = PromptLoader(tmp_path)

    review = fallback_loader.get(
        "review_round",
        should_stop=False,
        stop_reason="",
        target_met=False,
        plan="{}",
        used_queries="{}",
        matches="{}",
        noise="[]",
        jd="JD摘要",
        criteria='{"hard_requirements":["必须有LNG经验"]}',
    )
    greeting = fallback_loader.get(
        "greeting_user_prompt",
        job_title="销售总监",
        city="上海",
        job_description="负责能源行业客户开发",
        salary_line="薪资待遇优厚",
        style_hint="专业简洁",
    )

    assert "必须有LNG经验" in review
    assert "有界策略历史" in review
    assert "销售总监" in greeting
    assert "上海" in greeting
