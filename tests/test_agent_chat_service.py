"""AgentChatService：双向对话上下文组装、历史读取与降级。"""

from liepin_agent.services.agent_chat import (
    FALLBACK_REPLY,
    AgentChatService,
)
from liepin_agent.storage.sqlite_store import SQLiteStore


class FakeLLMClient:
    def __init__(self, reply="好的，我来调整方向。", exc=None):
        self.reply = reply
        self.exc = exc
        self.calls = []

    def chat(self, prompt, system_message=""):
        self.calls.append((prompt, system_message))
        if self.exc is not None:
            raise self.exc
        return self.reply


def _make_service(tmp_path, llm=None):
    store = SQLiteStore(str(tmp_path / "chat.db"))
    service = AgentChatService(llm or FakeLLMClient(), store)
    return store, service


def test_reply_includes_criteria_and_user_message_in_prompt(tmp_path):
    store, service = _make_service(tmp_path)
    session_id = store.create_session(title="压缩机销售", jd_text="天然气压缩机销售岗")
    criteria_id = store.create_criteria_version(
        session_id,
        "LNG\nBOG\n螺杆压缩机",
        "需要有天然气或压缩机设备销售经验。",
        created_by="human",
    )
    store.confirm_criteria_version(criteria_id)
    history = [{"role": "user", "content": "多找找有外企背景的人"}]

    reply = service.reply(session_id, history)

    assert reply == "好的，我来调整方向。"
    prompt, system_message = service.llm_client.calls[0]
    assert "LNG" in prompt
    assert "压缩机设备销售经验" in prompt
    assert "多找找有外企背景的人" in prompt
    assert system_message  # agent_chat_system 兜底/文件提示词非空


def test_reply_falls_back_when_llm_fails(tmp_path):
    store, service = _make_service(
        tmp_path, llm=FakeLLMClient(exc=RuntimeError("boom"))
    )
    session_id = store.create_session(title="t", jd_text="jd")

    reply = service.reply(session_id, [{"role": "user", "content": "在吗"}])

    assert reply == FALLBACK_REPLY


def test_load_history_reads_only_chat_events_in_order(tmp_path):
    store, service = _make_service(tmp_path)
    session_id = store.create_session(title="t", jd_text="jd")
    store.add_event(session_id, None, "user_message", "我", "第一条", {})
    store.add_event(session_id, None, "round_review", "复盘", "无关事件", {})
    store.add_event(session_id, None, "agent_reply", "寻访 Agent", "收到", {})
    store.add_event(session_id, None, "user_message", "我", "第二条", {})

    history = service.load_history(session_id)

    assert history == [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "收到"},
        {"role": "user", "content": "第二条"},
    ]
