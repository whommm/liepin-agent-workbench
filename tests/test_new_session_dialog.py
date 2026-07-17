import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liepin_agent.core.config import ConfigManager
from liepin_agent.ui.dialogs import NewSessionDialog


class FakeConsultant:
    def __init__(self):
        self.reply_calls = 0

    def reply(self, history):
        self.reply_calls += 1
        return "顾问分析 {}".format(self.reply_calls)

    def finalize_plan(self, history):
        return "一、岗位概述\n测试方案"


def _make_dialog(tmp_path):
    app = QApplication.instance() or QApplication([])
    manager = ConfigManager(str(tmp_path / "config.json"))
    dialog = NewSessionDialog(manager, consultant=FakeConsultant())
    return app, dialog


def _wait_idle(app, dialog, timeout=5.0):
    deadline = time.time() + timeout
    while dialog._busy and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert not dialog._busy, "consultant call did not finish in time"


def _send(app, dialog, text):
    dialog.chat_input.setPlainText(text)
    dialog._send_message()
    _wait_idle(app, dialog)


def test_finalize_locked_until_one_discussion_round(tmp_path):
    app, dialog = _make_dialog(tmp_path)
    assert dialog.finalize_btn.isEnabled() is False

    _send(app, dialog, "这是JD：跨境电商运营经理")
    assert dialog._user_message_count() == 1
    assert dialog.finalize_btn.isEnabled() is False  # 只发了 JD 还不算讨论

    _send(app, dialog, "要求5年经验，base深圳")
    assert dialog._user_message_count() == 2
    assert dialog.finalize_btn.isEnabled() is True


def test_finalize_switches_to_plan_state_and_payload(tmp_path):
    app, dialog = _make_dialog(tmp_path)
    _send(app, dialog, "这是JD：跨境电商运营经理")
    _send(app, dialog, "要求5年经验，base深圳")

    dialog._finalize()
    _wait_idle(app, dialog)

    assert dialog.plan_edit.isHidden() is False
    assert dialog.chat_view.isHidden() is True
    assert dialog.confirm_btn.isHidden() is False
    assert "一、岗位概述" in dialog.plan_edit.toPlainText()

    payload = dialog.payload()
    assert payload["jd_text"] == dialog.plan_edit.toPlainText()
    assert payload["user_notes"] == ""
    assert payload["title"]  # infer_title 兜底

    dialog._back_to_discussion()
    assert dialog.chat_view.isHidden() is False
    assert dialog.plan_edit.isHidden() is True
    assert dialog.finalize_btn.isEnabled() is True


def test_empty_message_is_ignored(tmp_path):
    app, dialog = _make_dialog(tmp_path)
    dialog.chat_input.setPlainText("   ")
    dialog._send_message()
    assert dialog._history == []
    assert dialog.consultant.reply_calls == 0
