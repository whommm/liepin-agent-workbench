"""Session list item widget for the liepin workbench sidebar."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from .main_window import MainWindow


# Status colors are limited to states that need quick visual distinction.
STATUS_COLORS: Dict[str, str] = {
    "running": "#2f6b4f",
    "waiting_approval": "#a96632",
    "paused": "#667085",
    "criteria_draft": "#a96632",
    "criteria_confirmed": "#2f6b4f",
    "completed": "#667085",
    "failed": "#a33f3f",
    "cancelled": "#98a2b3",
}

STATUS_LABELS: Dict[str, str] = {
    "criteria_draft": "待确认基准",
    "criteria_confirmed": "已确认",
    "running": "运行中",
    "waiting_approval": "等待确认",
    "paused": "已暂停",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}

_MINI_BTN = (
    "padding: 2px 8px; font-size: 11px; font-weight: 600; "
    "border-radius: 4px; min-height: 20px; max-height: 20px;"
)


def _mini_btn_style(role: str = "neutral") -> str:
    colors = {
        "neutral": ("#ffffff", "#344054", "#d0d5dd", "#f2f4f7"),
        "primary": ("#a96632", "#ffffff", "#a96632", "#8f5429"),
        "danger": ("#ffffff", "#a33f3f", "#d7a3a3", "#fff4f4"),
    }
    bg, fg, border, hover = colors.get(role, colors["neutral"])
    base = _MINI_BTN + " background: {}; color: {}; border: 1px solid {};".format(
        bg, fg, border
    )
    return (
        "QPushButton {" + base + "}"
        "QPushButton:hover {" + base + " background: " + hover + ";}"
    )


class SessionListItemWidget(QFrame):
    def __init__(self, session: Dict[str, object], parent_window: "MainWindow"):
        super().__init__()
        self.session = session
        self.parent_window = parent_window
        self.session_id = str(session["id"])
        self.setObjectName("SessionItem")

        status = str(session.get("status") or "")
        status_color = STATUS_COLORS.get(status, "#98a2b3")

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        content = QVBoxLayout()
        content.setContentsMargins(8, 8, 8, 8)
        content.setSpacing(6)

        # 标题行
        title = QLabel(str(session.get("title") or "未命名任务"))
        title.setObjectName("SessionTitle")
        title.setWordWrap(True)
        content.addWidget(title)

        # 状态行：彩色圆点 + 状态 + 统计
        status_row = QHBoxLayout()
        status_row.setSpacing(5)
        status_row.setContentsMargins(0, 0, 0, 0)

        dot = QLabel()
        dot.setObjectName("StatusDot")
        dot.setStyleSheet(
            "background: {}; border-radius: 3px; min-width: 7px; max-width: 7px; "
            "min-height: 7px; max-height: 7px;".format(status_color)
        )
        status_row.addWidget(dot)

        status_text = STATUS_LABELS.get(status, status)
        info = QLabel(
            "{}  |  候选 {}".format(
                status_text,
                session.get("candidate_count") or 0,
            )
        )
        info.setObjectName("SessionInfo")
        status_row.addWidget(info, 1)
        content.addLayout(status_row)

        # 按钮行：所有按钮紧凑排列
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_row.setContentsMargins(0, 0, 0, 0)

        is_running = status == "running"
        self.continue_btn = QPushButton("暂停" if is_running else "继续")
        self.stop_btn = QPushButton("终止")
        self.export_btn = QPushButton("导出")
        self.delete_btn = QPushButton("删除")

        self.continue_btn.setStyleSheet(_mini_btn_style())
        self.stop_btn.setStyleSheet(_mini_btn_style("danger"))
        self.export_btn.setStyleSheet(_mini_btn_style())
        self.delete_btn.setStyleSheet(_mini_btn_style("danger"))

        btn_row.addWidget(self.continue_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.export_btn)
        btn_row.addWidget(self.delete_btn)
        btn_row.addStretch(1)
        content.addLayout(btn_row)

        # 指令输入
        cmd_layout = QHBoxLayout()
        cmd_layout.setSpacing(4)
        cmd_layout.setContentsMargins(0, 0, 0, 0)
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("输入指令…")
        self.command_input.setFixedHeight(22)
        self.command_input.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.send_cmd_btn = QPushButton("发送")
        self.send_cmd_btn.setStyleSheet(_mini_btn_style("primary"))
        self.send_cmd_btn.setFixedHeight(22)
        cmd_layout.addWidget(self.command_input, 1)
        cmd_layout.addWidget(self.send_cmd_btn)
        content.addLayout(cmd_layout)

        root_layout.addLayout(content, 1)

        # 事件绑定
        self.send_cmd_btn.clicked.connect(self._on_send_command)
        self.command_input.returnPressed.connect(self._on_send_command)
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

    def _on_send_command(self) -> None:
        text = self.command_input.text().strip()
        if text:
            self.parent_window.send_user_command(self.session_id, text)
            self.command_input.clear()

    def update_from_session(self, session: Dict[str, object]) -> None:
        """增量更新状态，避免重建 widget 导致焦点丢失。"""
        self.session = session
        status = str(session.get("status") or "")
        status_color = STATUS_COLORS.get(status, "#98a2b3")

        # 更新圆点
        dot = self.findChild(QLabel, "StatusDot")
        if dot:
            dot.setStyleSheet(
                "background: {}; border-radius: 3px; min-width: 7px; max-width: 7px; "
                "min-height: 7px; max-height: 7px;".format(status_color)
            )

        # 更新状态文本
        info = self.findChild(QLabel, "SessionInfo")
        if info:
            status_text = STATUS_LABELS.get(status, status)
            info.setText(
                "{}  |  候选 {}".format(
                    status_text,
                    session.get("candidate_count") or 0,
                )
            )

        # 更新按钮
        is_running = status == "running"
        self.continue_btn.setText("暂停" if is_running else "继续")

        finished = status in {"completed", "failed", "cancelled"}
        self.stop_btn.setEnabled(not finished)
        self.send_cmd_btn.setEnabled(not finished)
