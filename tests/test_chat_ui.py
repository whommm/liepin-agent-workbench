"""主窗口聊天栏：发消息打断、对话态按钮、继续执行、标题栏中文状态。"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from liepin_agent.storage.sqlite_store import SQLiteStore
from liepin_agent.ui.main_window import MainWindow


class FakeChatService:
    def __init__(self, reply="收到，我会按这个方向调整。"):
        self.reply_text = reply
        self.replies = []

    def load_history(self, session_id, limit=20):
        return [{"role": "user", "content": "占位"}]

    def reply(self, session_id, history):
        self.replies.append((session_id, history))
        return self.reply_text


def _make_window(tmp_path):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    store = SQLiteStore(str(tmp_path / "ui.db"))
    window = MainWindow(store, tmp_path)
    window.chat_service = FakeChatService()
    return app, store, window


def _drain_chat(window, timeout=5.0):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while window._chat_busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert not window._chat_busy, "chat reply was not delivered in time"


def test_send_message_interrupts_running_session(tmp_path):
    app, store, window = _make_window(tmp_path)
    try:
        session_id = store.create_session(title="压缩机销售", jd_text="JD")
        store.update_session_status(session_id, "running")
        window.selected_session_id = session_id

        calls = []
        window.runtime.is_active = lambda sid: True
        window.runtime.interrupt_for_dialog = lambda sid: calls.append(sid) or True

        window.chat_input.setPlainText("多找有外企背景的人")
        window._send_chat_message()
        _drain_chat(window)

        assert calls == [session_id]
        events = store.list_events(session_id)
        types = [e["event_type"] for e in events]
        assert "user_message" in types
        assert "dialog_interrupted" in types
        assert "agent_reply" in types
        # 时间线里渲染成了聊天气泡
        window.refresh_all()
        html = window.timeline.toHtml()
        assert "寻访 Agent" in html
        assert "f6e9da" in html  # 用户气泡底色
    finally:
        window.close()
        app.processEvents()


def test_send_message_does_not_interrupt_paused_session(tmp_path):
    app, store, window = _make_window(tmp_path)
    try:
        session_id = store.create_session(title="t", jd_text="JD")
        store.update_session_status(session_id, "paused")
        window.selected_session_id = session_id

        window.runtime.is_active = lambda sid: False
        window.runtime.interrupt_for_dialog = lambda sid: (_ for _ in ()).throw(
            AssertionError("should not interrupt")
        )

        window.chat_input.setPlainText("先记着这个想法")
        window._send_chat_message()
        _drain_chat(window)

        types = [e["event_type"] for e in store.list_events(session_id)]
        assert "user_message" in types
        assert "dialog_interrupted" not in types
        assert "agent_reply" in types
        assert store.get_session(session_id)["status"] == "paused"
    finally:
        window.close()
        app.processEvents()


def test_end_dialog_sets_pending_command_and_resumes(tmp_path):
    app, store, window = _make_window(tmp_path)
    try:
        session_id = store.create_session(title="t", jd_text="JD")
        store.update_session_status(session_id, "user_dialog")
        window.selected_session_id = session_id
        store.add_event(session_id, None, "user_message", "我", "多找外企背景", {})
        store.add_event(session_id, None, "agent_reply", "寻访 Agent", "好的", {})

        window._end_dialog_and_resume()

        session = store.get_session(session_id)
        assert session["status"] == "running"
        assert "多找外企背景" in (session.get("pending_user_command") or "")
        types = [e["event_type"] for e in store.list_events(session_id)]
        assert "dialog_resumed" in types
    finally:
        window.close()
        app.processEvents()


def test_chat_bar_state_by_status(tmp_path):
    app, store, window = _make_window(tmp_path)
    try:
        session_id = store.create_session(title="t", jd_text="JD")
        window.selected_session_id = session_id

        store.update_session_status(session_id, "user_dialog")
        window.refresh_all()
        assert not window.end_dialog_btn.isHidden()
        assert "对话中" in window.chat_hint_label.text()

        store.update_session_status(session_id, "running")
        window.refresh_all()
        assert window.end_dialog_btn.isHidden()
        assert "打断" in window.chat_hint_label.text()

        store.update_session_status(session_id, "completed")
        window.refresh_all()
        assert not window.chat_input.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_stage_label_uses_chinese_status(tmp_path):
    app, store, window = _make_window(tmp_path)
    try:
        session_id = store.create_session(title="t", jd_text="JD")
        store.update_session_status(session_id, "cancelled")
        window.selected_session_id = session_id
        window.refresh_all()

        text = window.stage_label.text()
        assert "已取消" in text
        assert "cancelled" not in text

        store.update_session_status(session_id, "user_dialog")
        window.refresh_all()
        assert "对话中" in window.stage_label.text()
    finally:
        window.close()
        app.processEvents()
