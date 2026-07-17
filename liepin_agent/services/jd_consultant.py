"""JD 讨论顾问：新建任务前与用户多轮讨论，收敛并定稿《寻访方案》。"""

from __future__ import annotations

import logging
from typing import Dict, List

from ..core.config import ConfigManager
from ..prompts.loader import get_prompt_loader
from ..tools.llm_client import LLMClient

logger = logging.getLogger(__name__)

History = List[Dict[str, str]]

_ROLE_LABELS = {"user": "用户", "assistant": "顾问"}


class JDConsultant:
    """多轮 JD 讨论 + 定稿《寻访方案》。

    使用独立的 chat 模型配置（留空时逐字段 fallback 到默认配置）。
    """

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self._prompt_loader = get_prompt_loader()

    @classmethod
    def from_config(cls, config_manager: ConfigManager | None = None):
        config_manager = config_manager or ConfigManager()
        config = config_manager.config
        spec = config_manager.llm_connection_specs()["chat"]
        return cls(
            LLMClient(
                str(spec.get("api_base_url") or ""),
                str(spec.get("api_key") or ""),
                str(spec.get("model_name") or "deepseek-v4-flash"),
                timeout=int(spec.get("timeout") or 300),
                provider=str(spec.get("provider") or "openai"),
                max_retries=config.llm_max_retries,
                max_tokens=config.llm_max_tokens,
                temperature=config.chat_llm_temperature,
                rpm_limit=config.llm_rpm_limit,
                rpm_burst=config.llm_rpm_burst,
                rpm_cooldown_seconds=config.llm_rpm_cooldown_seconds,
            )
        )

    def reply(self, history: History) -> str:
        """Return the consultant's next reply given the discussion so far."""
        transcript = self._format_transcript(history)
        logger.info(
            "JDConsultant.reply: turns=%s transcript_len=%s",
            len(history),
            len(transcript),
        )
        return self.llm_client.chat(
            transcript,
            system_message=self._prompt_loader.get("jd_consultant_system"),
        )

    def finalize_plan(self, history: History) -> str:
        """Produce the final 《寻访方案》 document from the whole discussion."""
        transcript = self._format_transcript(history)
        prompt = self._prompt_loader.get(
            "jd_consultant_finalize", transcript=transcript
        )
        logger.info(
            "JDConsultant.finalize_plan: turns=%s prompt_len=%s",
            len(history),
            len(prompt),
        )
        return self.llm_client.chat(
            prompt,
            system_message=self._prompt_loader.get("jd_consultant_system"),
        )

    @staticmethod
    def _format_transcript(history: History) -> str:
        lines = []
        for item in history:
            role = str(item.get("role") or "user")
            label = _ROLE_LABELS.get(role, "用户")
            content = str(item.get("content") or "").strip()
            if content:
                lines.append("{}：\n{}".format(label, content))
        return "\n\n".join(lines)
