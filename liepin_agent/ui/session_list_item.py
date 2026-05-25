"""Session list item widget for the liepin workbench sidebar."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from .main_window import MainWindow


# 状态 → 色带/圆点颜色
STATUS_COLORS: Dict[str, str] = {
    "running": "#5a9a5a",
    "waiting_approval": "#c4956a",
    "paused": "#c4956a",
    "criteria_draft": "#6a8aaa",
    "criteria_confirmed": "#7cb87c",
    "completed": "#8a8070",
    "failed": "#c56a6a",
    "cancelled": "#a09585",
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
    "padding: 2px 8px; font-size: 11px; color: white; "
    "border: none; border-radius: 4px; min-height: 20px; max-height: 20px;"
)


def _mini_btn_style(bg: str, hover: str, pressed: str = "") -> str:
    s = "QPushButton {" + _MINI_BTN + " background: " + bg + ";}"
    s += "QPushButton:hover {" + _MINI_BTN + " background: " + hover + ";}"
    if pressed:
        s += "QPushButton:pressed {" + _MINI_BTN + " background: " + pressed + ";}"
    return s


class SessionListItemWidget(QFrame):
    def __init__(self, session: Dict[str, object], parent_window: "MainWindow"):
        super().__init__()
        self.session = session
        self.parent_window = parent_window
        self.session_id = str(session["id"])
        self.setObjectName("SessionItem")

        status = str(session.get("status") or "")
        status_color = STATUS_COLORS.get(status, "#b8a890")

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        stripe = QFrame()
        stripe.setObjectName("StatusStripe")
        stripe.setStyleSheet("background: {};".format(status_color))
        stripe.setFixedWidth(3)
        stripe.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        root_layout.addWidget(stripe)

        content = QVBoxLayout()
        content.setContentsMargins(10, 8, 10, 8)
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
            "background: {}; border-radius: 4px; min-width: 8px; max-width: 8px; "
            "min-height: 8px; max-height: 8px;".format(status_color)
        )
        status_row.addWidget(dot)

        status_text = STATUS_LABELS.get(status, status)
        info = QLabel(
            "{}  |  候选 {}  |  A/B {}".format(
                status_text,
                session.get("candidate_count") or 0,
                session.get("ab_count") or 0,
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

        self.continue_btn.setStyleSheet(_mini_btn_style("#d4a574", "#e0b584", "#b8865a"))
        self.stop_btn.setStyleSheet(_mini_btn_style("#c56a6a", "#d97b7b", "#a85050"))
        self.export_btn.setStyleSheet(_mini_btn_style("rgba(160,150,130,0.65)", "rgba(175,165,145,0.80)"))
        self.delete_btn.setStyleSheet(_mini_btn_style("#c56a6a", "#d97b7b", "#a85050"))

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
        self.send_cmd_btn.setStyleSheet(_mini_btn_style("#7cb87c", "#8cc88c", "#6aa86a"))
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
        status_color = STATUS_COLORS.get(status, "#b8a890")

        # 更新色带
        stripe = self.findChild(QFrame, "StatusStripe")
        if stripe:
            stripe.setStyleSheet("background: {};".format(status_color))

        # 更新圆点
        dot = self.findChild(QLabel, "StatusDot")
        if dot:
            dot.setStyleSheet(
                "background: {}; border-radius: 4px; min-width: 8px; max-width: 8px; "
                "min-height: 8px; max-height: 8px;".format(status_color)
            )

        # 更新状态文本
        info = self.findChild(QLabel, "SessionInfo")
        if info:
            status_text = STATUS_LABELS.get(status, status)
            info.setText(
                "{}  |  候选 {}  |  A/B {}".format(
                    status_text,
                    session.get("candidate_count") or 0,
                    session.get("ab_count") or 0,
                )
            )

        # 更新按钮
        is_running = status == "running"
        self.continue_btn.setText("暂停" if is_running else "继续")

        finished = status in {"completed", "failed", "cancelled"}
        self.stop_btn.setEnabled(not finished)
        self.send_cmd_btn.setEnabled(not finished)
