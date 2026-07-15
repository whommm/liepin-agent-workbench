"""Generate reusable greeting text for manual/batch Liepin greetings."""

from __future__ import annotations

import logging
import re
from typing import List

from ..core.config import ConfigManager
from ..prompts.loader import get_prompt_loader
from .llm_client import LLMClient

logger = logging.getLogger(__name__)


class GreetingTextGenerationService:
    DEFAULT_TEMPLATE = (
        "您好，我是猎头顾问，目前有个base{city}的{job_title}机会，"
        "{job_summary}，薪资可谈，方便的话能发一份您的简历看看吗？"
    )

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self._prompt_loader = get_prompt_loader()

    @classmethod
    def from_config(cls, config_manager: ConfigManager | None = None):
        config_manager = config_manager or ConfigManager()
        config = config_manager.config
        return cls(
            LLMClient(
                config.api_base_url,
                config.api_key,
                config.model_name or "deepseek-v4-flash",
                timeout=min(int(config.timeout or 60), 60),
                provider=config.llm_provider or "openai",
                max_retries=config.llm_max_retries,
                max_tokens=config.llm_max_tokens,
                temperature=config.llm_temperature,
                rpm_limit=config.llm_rpm_limit,
                rpm_burst=config.llm_rpm_burst,
                rpm_cooldown_seconds=config.llm_rpm_cooldown_seconds,
            )
        )

    def generate(
        self,
        job_title: str,
        city: str,
        job_description: str,
        salary_range: str = "",
        style: str = "general",
    ) -> str:
        city = self._extract_prefecture_city(city)
        prompt = self._build_prompt(job_title, city, job_description, salary_range, style)
        try:
            raw_text = self.llm_client.chat(
                prompt, system_message=self._prompt_loader.get("greeting_system_prompt")
            )
            return self._post_process(raw_text, job_title, city)
        except Exception as exc:
            logger.warning("greeting text generation fallback: %s", exc)
            return self._fallback_generate(job_title, city, job_description)

    def generate_batch(
        self,
        job_title: str,
        city: str,
        job_description: str,
        salary_range: str = "",
        count: int = 5,
    ) -> List[str]:
        styles = ["general", "highlight", "career", "company", "balance"]
        selected = styles[: max(1, min(count, len(styles)))]
        results: List[str] = []

        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=min(len(selected), 3)) as executor:
            futures = {
                executor.submit(
                    self.generate, job_title, city, job_description, salary_range, style
                ): style
                for style in selected
            }
            for future in as_completed(futures):
                try:
                    text = future.result()
                    if text and text not in results:
                        results.append(text)
                except Exception as exc:
                    logger.warning("generate_batch style=%s failed: %s", futures[future], exc)

        fallback = self._fallback_generate(job_title, city, job_description)
        while len(results) < count:
            results.append(fallback)
        return results[:count]

    def _build_prompt(
        self,
        job_title: str,
        city: str,
        job_description: str,
        salary_range: str,
        style: str,
    ) -> str:
        style_hints = {
            "general": "写一个通用版本，平衡各方面信息",
            "highlight": "重点突出岗位核心亮点和吸引力",
            "career": "强调职业发展空间和成长机会",
            "company": "突出公司优势和团队氛围",
            "balance": "强调工作生活平衡和福利待遇",
        }
        salary_line = ""
        if salary_range:
            salary_line = "薪资范围：{}（注意：输出时请替换为薪资可谈）\n".format(salary_range)
        return self._prompt_loader.get(
            "greeting_user_prompt",
            job_title=job_title or "目标岗位",
            city=city or "该城市",
            job_description=(job_description or "")[:500],
            salary_line=salary_line,
            style_hint=style_hints.get(style, style_hints["general"]),
        )

    def _post_process(self, text: str, job_title: str, city: str) -> str:
        text = (text or "").strip().strip('"\'')
        if not text:
            return self._fallback_generate(job_title, city, "")
        if not text.startswith("您好，我是猎头顾问"):
            text = "您好，我是猎头顾问，目前有个base{}的{}机会，{}".format(
                city or "该城市", job_title or "目标岗位", text
            )
        text = self._mask_salary(text)
        if "简历" not in text[-24:]:
            text = text.rstrip("，。！？") + "，方便的话能发一份您的简历看看吗？"
        return text[:200]

    @staticmethod
    def _mask_salary(text: str) -> str:
        for pattern in (
            r"\d+\s*-\s*\d+\s*[kK万wW]",
            r"\d+\s*[kK万wW]\s*-\s*\d+\s*[kK万wW]",
            r"年薪\s*\d+\s*-\s*\d+\s*万",
            r"月薪\s*\d+\s*-\s*\d+\s*[kK]",
            r"\d+\s*-\s*\d+\s*万/年",
            r"\d+\s*-\s*\d+\s*K/月",
        ):
            text = re.sub(pattern, "薪资可谈", text)
        text = re.sub(r"薪资[：:]\s*[^，。]+", "薪资可谈", text)
        text = re.sub(r"薪水[：:]\s*[^，。]+", "薪资可谈", text)
        return text

    def _fallback_generate(self, job_title: str, city: str, job_description: str) -> str:
        summary = self._extract_brief_summary(job_description)
        if not summary:
            summary = "团队发展快、项目有挑战性"
        return self.DEFAULT_TEMPLATE.format(
            city=city or "该城市",
            job_title=job_title or "目标岗位",
            job_summary=summary,
        )

    @staticmethod
    def _extract_brief_summary(job_description: str) -> str:
        for sentence in re.split(r"[。；\n]", job_description or ""):
            sentence = sentence.strip()
            if 10 < len(sentence) < 80:
                return re.sub(r"负责|工作|职责|要求|任职", "", sentence).strip("，。 ")
        return ""

    @staticmethod
    def _extract_prefecture_city(city_text: str) -> str:
        if not city_text:
            return "该城市"
        for city in (
            "北京", "上海", "天津", "重庆", "深圳", "广州", "杭州", "南京", "苏州",
            "成都", "武汉", "西安", "长沙", "郑州", "青岛", "大连", "厦门", "宁波",
            "无锡", "佛山", "东莞", "福州", "济南", "合肥", "昆明",
        ):
            if city in city_text:
                return city
        match = re.search(r"(.*?)(?:市|区|县|镇|街道)", city_text)
        if match and len(match.group(1)) >= 2:
            return match.group(1)
        return re.sub(r"[市区县镇街道省]", "", city_text)[:4] or city_text[:4]
