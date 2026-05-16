"""Session list item widget for the liepin workbench sidebar."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from .main_window import MainWindow


class SessionListItemWidget(QFrame):
    def __init__(self, session: Dict[str, object], parent_window: "MainWindow"):
        super().__init__()
        self.session = session
        self.parent_window = parent_window
        self.session_id = str(session["id"])
        self.setObjectName("SessionItem")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(6)

        title = QLabel(str(session.get("title") or "未命名任务"))
        title.setObjectName("SessionTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        status = str(session.get("status") or "")
        status_text = {
            "criteria_draft": "待确认基准",
            "criteria_confirmed": "已确认，待开始",
            "running": "运行中",
            "waiting_approval": "等待人工确认",
            "paused": "已暂停",
            "completed": "已完成",
            "failed": "失败",
            "cancelled": "已取消",
        }.get(status, status)
        info = QLabel(
            "{} | 候选人 {} | 详情 {} | A/B {}".format(
                status_text,
                session.get("candidate_count") or 0,
                session.get("detail_count") or 0,
                session.get("ab_count") or 0,
            )
        )
        info.setObjectName("SessionInfo")
        layout.addWidget(info)

        buttons = QGridLayout()
        buttons.setSpacing(5)
        self.continue_btn = QPushButton("暂停" if status == "running" else "继续")
        self.stop_btn = QPushButton("终止")
        self.stop_btn.setObjectName("DangerBtn")
        self.export_btn = QPushButton("导出")
        self.export_btn.setObjectName("SecondaryBtn")
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("DangerBtn")
        for button in [
            self.continue_btn,
            self.stop_btn,
            self.export_btn,
            self.delete_btn,
        ]:
            button.setFixedHeight(26)
        buttons.addWidget(self.continue_btn, 0, 0)
        buttons.addWidget(self.stop_btn, 0, 1)
        buttons.addWidget(self.export_btn, 1, 0)
        buttons.addWidget(self.delete_btn, 1, 1)
        buttons.setColumnStretch(0, 1)
        buttons.setColumnStretch(1, 1)
        layout.addLayout(buttons)

        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("输入指令，例如：去掉BLDC，只保留无刷电机")
        self.command_input.setFixedHeight(26)
        self.send_cmd_btn = QPushButton("发送指令")
        self.send_cmd_btn.setObjectName("SuccessBtn")
        self.send_cmd_btn.setFixedHeight(26)
        cmd_layout = QHBoxLayout()
        cmd_layout.setSpacing(5)
        cmd_layout.addWidget(self.command_input, 1)
        cmd_layout.addWidget(self.send_cmd_btn)
        layout.addLayout(cmd_layout)

        self.send_cmd_btn.clicked.connect(
            lambda: parent_window.send_user_command(
                self.session_id, self.command_input.text()
            )
        )
        self.continue_btn.clicked.connect(
            lambda: parent_window.toggle_session_run(self.session_id)
        )
        self.stop_btn.clicked.connect(
            lambda: parent_window.stop_session(self.session_id)
        )
        self.export_btn.clicked.connect(
            lambda: parent_window.export_session(self.session_id)
        )
        self.delete_btn.clicked.connect(
            lambda: parent_window.delete_session(self.session_id)
        )

        if status in {"completed", "failed", "cancelled"}:
            self.stop_btn.setEnabled(False)
            self.send_cmd_btn.setEnabled(False)
