"""寻访 Agent 对话服务：用户打断后与 Agent 双向沟通。

与 JDConsultant 同构：独立 chat 模型配置（留空时逐字段 fallback 到默认配置）。
对话历史直接读写 agent_events（user_message / agent_reply 事件），不新建表。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..core.config import ConfigManager
from ..prompts.loader import get_prompt_loader
from ..tools.llm_client import LLMClient

logger = logging.getLogger(__name__)

History = List[Dict[str, str]]

_EVENT_TYPE_TO_ROLE = {"user_message": "user", "agent_reply": "assistant"}
_ROLE_LABELS = {"user": "用户", "assistant": "寻访 Agent"}
_CHAT_EVENT_TYPES = tuple(_EVENT_TYPE_TO_ROLE)

MAX_HISTORY_TURNS = 20
MAX_CONTEXT_EVENTS = 15

FALLBACK_REPLY = (
    "我暂时没能生成回复（模型调用失败）。你的消息已记录，"
    "点击「继续执行」后我会在下一轮搜索计划中采纳。"
)


class AgentChatService:
    """用户与运行中的寻访 Agent 之间的双向对话。"""

    def __init__(self, llm_client: LLMClient, store):
        self.llm_client = llm_client
        self.store = store
        self._prompt_loader = get_prompt_loader()

    @classmethod
    def from_config(cls, config_manager: ConfigManager | None, store):
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
            ),
            store,
        )

    def load_history(self, session_id: str, limit: int = MAX_HISTORY_TURNS) -> History:
        """Read the chat transcript for a session from agent_events."""
        events = self.store.list_events(session_id)
        history: History = []
        for event in events:
            role = _EVENT_TYPE_TO_ROLE.get(str(event.get("event_type") or ""))
            content = str(event.get("message") or "").strip()
            if role and content:
                history.append({"role": role, "content": content})
        return history[-max(1, int(limit)):]

    def reply(self, session_id: str, history: History) -> str:
        """Generate the agent's conversational reply with full work context."""
        context = self._build_context(session_id)
        transcript = self._format_transcript(history)
        prompt = self._prompt_loader.get(
            "agent_chat_reply", context=context, transcript=transcript
        )
        logger.info(
            "AgentChatService.reply: session=%s turns=%s prompt_len=%s",
            session_id[:8],
            len(history),
            len(prompt),
        )
        try:
            return self.llm_client.chat(
                prompt,
                system_message=self._prompt_loader.get("agent_chat_system"),
            )
        except Exception as exc:
            logger.warning("AgentChatService.reply failed: %s", exc)
            return FALLBACK_REPLY

    def _build_context(self, session_id: str) -> str:
        session = self.store.get_session(session_id) or {}
        criteria = (
            self.store.get_latest_criteria_version(session_id, "confirmed")
            or self.store.get_latest_criteria_version(session_id, "draft")
            or {}
        )
        try:
            metrics = self.store.session_efficiency_metrics(session_id)
        except Exception:
            metrics = {}

        lines = [
            "任务标题：{}".format(session.get("title") or "未命名"),
            "任务状态：{}".format(session.get("status") or ""),
            "岗位 JD：{}".format(_truncate(str(session.get("jd_text") or ""), 600)),
            "已确认匹配词：{}".format(criteria.get("keywords_text") or "（无）"),
            "已确认岗位要求：{}".format(
                _truncate(str(criteria.get("requirements_text") or "（无）"), 400)
            ),
            "进度：第 {} 轮 | 读卡 {} | 抓详情 {}".format(
                metrics.get("search_round_count") or 0,
                metrics.get("raw_candidate_count") or 0,
                metrics.get("detail_fetch_count") or 0,
            ),
        ]

        events = [
            event
            for event in self.store.list_events(session_id)
            if str(event.get("event_type") or "") not in _CHAT_EVENT_TYPES
        ]
        if events:
            lines.append("最近工作记录：")
            for event in events[-MAX_CONTEXT_EVENTS:]:
                lines.append(
                    "- {}：{}".format(
                        event.get("title") or "",
                        _truncate(str(event.get("message") or ""), 120),
                    )
                )
        return "\n".join(lines)

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


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"
