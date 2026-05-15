"""Main desktop workbench window."""

from __future__ import annotations

import threading
from collections import deque
from html import escape
from pathlib import Path
import re
from typing import Dict, Optional

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..agent.planner import Planner
from ..agent.runtime import AgentRuntime
from ..agent.brain import LLMAgentBrain
from ..core.config import ConfigManager
from ..services.event_bus import EventBus
from ..storage.sqlite_store import SQLiteStore, from_json
from ..tools.exporter import ExportService
from ..tools.real_liepin import RealLiepinTool
from ..tools.real_matcher import RealMatchService


class PoolNotificationDialog(QDialog):
    """Top-most notification dialog for pool queue."""

    def __init__(self, title: str, session_id: str, parent=None):
        super().__init__(parent)
        self.session_id = session_id
        self.setWindowTitle("项目池提醒")
        self.setWindowFlags(
            Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint
        )
        self.resize(400, 160)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(
            QLabel(
                '<b style="font-size:15px;">项目《{}》</b>'.format(title)
            )
        )
        info = QLabel("寻访基准（匹配词与岗位要求）已生成，请确认后开始搜索。")
        info.setWordWrap(True)
        layout.addWidget(info)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        self.later_btn = QPushButton("稍后")
        self.confirm_btn = QPushButton("去确认")
        self.confirm_btn.setDefault(True)
        self.confirm_btn.setStyleSheet(
            "background: #2563eb; color: white; font-weight: 700; padding: 6px 16px;"
        )
        self.later_btn.clicked.connect(self.reject)
        self.confirm_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(self.later_btn)
        btn_layout.addWidget(self.confirm_btn)
        layout.addLayout(btn_layout)

        if parent:
            QApplication.alert(parent, 0)

    def _on_confirm(self) -> None:
        self.done(100)


class NewSessionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建寻访任务")
        self.resize(720, 560)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("例如：文创产品经理 / 深圳")
        form.addRow("任务名称", self.title_input)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["自动", "单步", "监督"])
        form.addRow("运行模式", self.mode_combo)

        self.max_rounds = QSpinBox()
        self.max_rounds.setRange(1, 12)
        self.max_rounds.setValue(10)
        form.addRow("最大轮次", self.max_rounds)

        self.max_details = QSpinBox()
        self.max_details.setRange(1, 9999)
        self.max_details.setValue(999)
        form.addRow("最大详情抓取", self.max_details)

        self.target_ab = QSpinBox()
        self.target_ab.setRange(1, 9999)
        self.target_ab.setValue(999)
        form.addRow("目标 A/B 数", self.target_ab)

        layout.addLayout(form)

        layout.addWidget(QLabel("JD 文本"))
        self.jd_input = QTextEdit()
        self.jd_input.setPlaceholderText(
            "粘贴岗位描述。Agent 会基于文本生成第一轮搜索假设。"
        )
        layout.addWidget(self.jd_input, 1)

        layout.addWidget(QLabel("补充说明"))
        self.notes_input = QTextEdit()
        self.notes_input.setMaximumHeight(90)
        self.notes_input.setPlaceholderText(
            "客户偏好、排除方向、城市弹性、特殊背景等。"
        )
        layout.addWidget(self.notes_input)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        create_btn = QPushButton("创建")
        create_btn.setDefault(True)
        cancel_btn.clicked.connect(self.reject)
        create_btn.clicked.connect(self._validate_and_accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(create_btn)
        layout.addLayout(buttons)

    def _validate_and_accept(self) -> None:
        if not self.jd_input.toPlainText().strip():
            QMessageBox.warning(self, "提示", "请先输入 JD 文本")
            return
        self.accept()

    def payload(self) -> Dict[str, object]:
        jd_text = self.jd_input.toPlainText().strip()
        title = self.title_input.text().strip() or Planner.infer_title(jd_text)
        return {
            "title": title,
            "jd_text": jd_text,
            "user_notes": self.notes_input.toPlainText().strip(),
            "mode": self.mode_combo.currentText(),
            "max_rounds": self.max_rounds.value(),
            "max_detail_fetches": self.max_details.value(),
            "target_ab_count": self.target_ab.value(),
        }


class SettingsDialog(QDialog):
    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        config = config_manager.config
        self.setWindowTitle("设置")
        self.resize(640, 520)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.api_base_url = QLineEdit(config.api_base_url)
        self.api_base_url.setPlaceholderText("https://api.deepseek.com/v1")
        form.addRow("API Base URL", self.api_base_url)

        self.api_key = QLineEdit(config.api_key)
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText(
            "留空则使用环境变量 {}".format(config.api_key_env or "LIEPIN_AGENT_API_KEY")
        )
        form.addRow("API Key", self.api_key)

        self.model_name = QLineEdit(config.model_name or "deepseek-chat")
        form.addRow("模型名称", self.model_name)

        self.timeout = QSpinBox()
        self.timeout.setRange(10, 600)
        self.timeout.setValue(int(config.timeout or 120))
        form.addRow("API 超时秒数", self.timeout)

        # Backend LLM (Matcher)
        form.addRow(QLabel(""))
        backend_label = QLabel("后端 LLM 配置（候选人匹配专用，留空则共用上方配置）")
        backend_label.setStyleSheet("color: #64748b; font-size: 12px;")
        form.addRow(backend_label)

        self.backend_api_base_url = QLineEdit(config.backend_api_base_url)
        self.backend_api_base_url.setPlaceholderText("https://api.deepseek.com/v1")
        form.addRow("后端 API Base URL", self.backend_api_base_url)

        self.backend_api_key = QLineEdit(config.backend_api_key)
        self.backend_api_key.setEchoMode(QLineEdit.Password)
        self.backend_api_key.setPlaceholderText("留空则使用上方 API Key")
        form.addRow("后端 API Key", self.backend_api_key)

        self.backend_model_name = QLineEdit(config.backend_model_name)
        self.backend_model_name.setPlaceholderText("留空则使用上方模型名称")
        form.addRow("后端模型名称", self.backend_model_name)

        self.browser_channel = QComboBox()
        self.browser_channel.addItems(["msedge", "chrome", "chromium"])
        index = self.browser_channel.findText(config.liepin_browser_channel or "msedge")
        self.browser_channel.setCurrentIndex(max(0, index))
        form.addRow("浏览器通道", self.browser_channel)

        self.profile_dir = QLineEdit(
            config.liepin_browser_profile_dir or "browser_profile/liepin"
        )
        form.addRow("猎聘 Profile", self.profile_dir)

        self.greeting_template = QTextEdit(config.greeting_template or "")
        self.greeting_template.setMaximumHeight(90)
        self.greeting_template.setPlaceholderText(
            "留空则只触发平台默认打招呼；填写后，手动打招呼会发送这段消息。"
        )
        form.addRow("手动打招呼话术", self.greeting_template)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        save_btn = QPushButton("保存")
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self._save)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

    def _save(self) -> None:
        self.config_manager.update(
            api_base_url=self.api_base_url.text().strip(),
            api_key=self.api_key.text().strip(),
            model_name=self.model_name.text().strip() or "deepseek-chat",
            timeout=self.timeout.value(),
            backend_api_base_url=self.backend_api_base_url.text().strip(),
            backend_api_key=self.backend_api_key.text().strip(),
            backend_model_name=self.backend_model_name.text().strip(),
            liepin_browser_channel=self.browser_channel.currentText(),
            liepin_browser_profile_dir=self.profile_dir.text().strip()
            or "browser_profile/liepin",
            greeting_template=self.greeting_template.toPlainText().strip(),
        )
        if not self.config_manager.save_config():
            QMessageBox.warning(self, "保存失败", "配置文件写入失败")
            return
        self.accept()


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
        self.export_btn = QPushButton("导出")
        self.delete_btn = QPushButton("删除")
        for button in [self.continue_btn, self.stop_btn, self.export_btn, self.delete_btn]:
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


class MainWindow(QMainWindow):
    def __init__(self, store: SQLiteStore, workspace_root: Path):
        super().__init__()
        self.store = store
        self.store.recover_interrupted_sessions()
        self.workspace_root = Path(workspace_root)
        self.event_bus = EventBus()
        self.config_manager = ConfigManager()
        self.runtime = self._build_runtime()
        self.selected_session_id: Optional[str] = None
        self.selected_candidate_id: Optional[str] = None
        self._dirty = True
        self._criteria_dirty = False
        self._pending_status_text = ""
        self._pending_browser_error = ""
        self._runtime_events = deque()
        self._runtime_events_lock = threading.Lock()
        self._queue_running = False
        self._active_pool_session_id: Optional[str] = None

        self.setWindowTitle("猎聘寻访 Agent 工作台")
        self._build_ui()
        self._connect_events()
        self._apply_style()

        # Runtime events may come from worker threads; poll a tiny queue on the
        # UI thread so Qt widgets are only touched from the main thread.
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500)
        self.refresh_timer.timeout.connect(self._refresh_if_dirty)
        self.refresh_timer.start()
        self.refresh_all()

    def _build_runtime(self) -> AgentRuntime:
        return AgentRuntime(
            store=self.store,
            event_bus=self.event_bus,
            liepin_tool=RealLiepinTool(self.config_manager),
            matcher=RealMatchService.from_config(self.config_manager),
            agent_brain=LLMAgentBrain.from_config(self.config_manager),
        )

    def _rebuild_runtime_tools(self) -> None:
        try:
            self.runtime.liepin_tool.close()
        except Exception:
            pass
        self.runtime.browser_queue.shutdown()
        self.runtime.match_queue.shutdown()
        self.runtime = self._build_runtime()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)
        root_layout.addWidget(self._build_top_bar())

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(self._build_left_panel())
        main_splitter.addWidget(self._build_center_panel())
        main_splitter.addWidget(self._build_right_panel())
        main_splitter.setSizes([260, 780, 360])
        root_layout.addWidget(main_splitter, 1)
        self.setCentralWidget(root)

    def _build_top_bar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("TopBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 8, 14, 8)

        self.title_label = QLabel("未选择任务")
        self.title_label.setObjectName("TitleLabel")
        self.stage_label = QLabel("就绪")
        self.browser_label = QLabel("浏览器：真实猎聘")
        self.stats_label = QLabel("轮次 0 | 候选人 0 | 详情 0 | A/B 0")

        layout.addWidget(self.title_label, 2)
        layout.addWidget(self.stage_label)
        layout.addWidget(self.browser_label)
        layout.addWidget(self.stats_label)
        layout.addStretch(1)

        self.new_btn = QPushButton("新建")
        self.add_to_pool_btn = QPushButton("添加到池")
        self.open_liepin_btn = QPushButton("打开猎聘")
        self.close_liepin_btn = QPushButton("关闭浏览器")
        self.settings_btn = QPushButton("设置")
        for button in [
            self.new_btn,
            self.add_to_pool_btn,
            self.open_liepin_btn,
            self.close_liepin_btn,
            self.settings_btn,
        ]:
            layout.addWidget(button)
        return frame

    def _build_left_panel(self) -> QWidget:
        container = QWidget()
        container.setMinimumWidth(280)
        outer_layout = QVBoxLayout(container)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(6)

        splitter = QSplitter(Qt.Vertical)

        # --- Project Pool ---
        pool_frame = QFrame()
        pool_frame.setObjectName("Panel")
        pool_layout = QVBoxLayout(pool_frame)
        pool_layout.setContentsMargins(6, 6, 6, 6)
        pool_header = QHBoxLayout()
        pool_header.addWidget(QLabel("项目池"))
        pool_header.addStretch(1)
        self.start_queue_btn = QPushButton("开始队列")
        self.stop_queue_btn = QPushButton("停止队列")
        self.stop_queue_btn.setVisible(False)
        self.clear_completed_btn = QPushButton("清理")
        pool_header.addWidget(self.start_queue_btn)
        pool_header.addWidget(self.stop_queue_btn)
        pool_header.addWidget(self.clear_completed_btn)
        pool_layout.addLayout(pool_header)

        self.pool_list = QListWidget()
        self.pool_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.pool_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.pool_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pool_list.model().rowsMoved.connect(self._on_pool_reordered)
        pool_layout.addWidget(self.pool_list, 1)
        splitter.addWidget(pool_frame)

        # --- Session List ---
        sessions_frame = QFrame()
        sessions_frame.setObjectName("Panel")
        sessions_layout = QVBoxLayout(sessions_frame)
        sessions_layout.setContentsMargins(6, 6, 6, 6)
        sessions_layout.addWidget(QLabel("寻访任务"))
        self.session_list = QListWidget()
        self.session_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sessions_layout.addWidget(self.session_list, 1)
        splitter.addWidget(sessions_frame)

        splitter.setSizes([220, 380])
        outer_layout.addWidget(splitter)
        return container

    def _build_center_panel(self) -> QWidget:
        splitter = QSplitter(Qt.Vertical)
        timeline_frame = QFrame()
        timeline_frame.setObjectName("Panel")
        timeline_layout = QVBoxLayout(timeline_frame)
        timeline_layout.addWidget(QLabel("Agent 决策时间线"))
        self.timeline = QTextBrowser()
        self.timeline.setOpenExternalLinks(False)
        timeline_layout.addWidget(self.timeline, 1)

        table_frame = QFrame()
        table_frame.setObjectName("Panel")
        table_layout = QVBoxLayout(table_frame)
        table_layout.addWidget(QLabel("候选人池"))
        self.candidate_table = QTableWidget(0, 12)
        self.candidate_table.setHorizontalHeaderLabels(
            [
                "姓名",
                "公司",
                "职位",
                "城市",
                "年限",
                "学历",
                "金领",
                "卡片判断",
                "匹配",
                "打招呼",
                "状态",
                "摘要",
            ]
        )
        self.candidate_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.candidate_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.candidate_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.candidate_table.verticalHeader().setVisible(False)
        table_layout.addWidget(self.candidate_table, 1)

        splitter.addWidget(timeline_frame)
        splitter.addWidget(table_frame)
        splitter.setSizes([420, 300])
        return splitter

    def _build_right_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.addWidget(QLabel("匹配词与岗位要求"))
        self.criteria_keywords_input = QTextEdit()
        self.criteria_keywords_input.setPlaceholderText("每行一个关键词，例如：LNG、BOG、螺杆压缩机")
        self.criteria_keywords_input.setMaximumHeight(90)
        layout.addWidget(self.criteria_keywords_input)
        self.criteria_requirements_input = QTextEdit()
        self.criteria_requirements_input.setPlaceholderText("用一段话描述本岗位最关键的匹配要求")
        self.criteria_requirements_input.setMaximumHeight(120)
        layout.addWidget(self.criteria_requirements_input)
        criteria_buttons = QHBoxLayout()
        self.regenerate_criteria_btn = QPushButton("重新生成草案")
        self.confirm_criteria_btn = QPushButton("确认寻访基准")
        self.confirm_and_start_btn = QPushButton("确认并开始")
        criteria_buttons.addWidget(self.regenerate_criteria_btn)
        criteria_buttons.addWidget(self.confirm_criteria_btn)
        criteria_buttons.addWidget(self.confirm_and_start_btn)
        layout.addLayout(criteria_buttons)
        layout.addWidget(QLabel("当前策略"))
        self.strategy_view = QTextBrowser()
        self.strategy_view.setMinimumHeight(220)
        layout.addWidget(self.strategy_view)
        detail_header = QHBoxLayout()
        detail_header.addWidget(QLabel("候选人详情"))
        detail_header.addStretch(1)
        self.manual_greeting_btn = QPushButton("手动打招呼")
        self.manual_greeting_btn.setEnabled(False)
        detail_header.addWidget(self.manual_greeting_btn)
        layout.addLayout(detail_header)
        self.detail_view = QTextBrowser()
        layout.addWidget(self.detail_view, 2)
        layout.addWidget(QLabel("详细日志"))
        self.log_view = QTextBrowser()
        layout.addWidget(self.log_view, 2)
        return frame

    def _connect_events(self) -> None:
        self.new_btn.clicked.connect(self.create_session)
        self.add_to_pool_btn.clicked.connect(self.create_session_and_add_to_pool)
        self.open_liepin_btn.clicked.connect(self.open_liepin_browser)
        self.close_liepin_btn.clicked.connect(self.close_liepin_browser)
        self.settings_btn.clicked.connect(self.open_settings)
        self.start_queue_btn.clicked.connect(self._start_queue)
        self.stop_queue_btn.clicked.connect(self._stop_queue)
        self.clear_completed_btn.clicked.connect(self._clear_completed_pool)
        self.pool_list.currentItemChanged.connect(self._on_pool_item_selected)
        self.manual_greeting_btn.clicked.connect(self.greet_selected_candidate)
        self.regenerate_criteria_btn.clicked.connect(self.regenerate_criteria_draft)
        self.confirm_criteria_btn.clicked.connect(self.confirm_current_criteria)
        self.confirm_and_start_btn.clicked.connect(self.confirm_criteria_and_start)
        self.criteria_keywords_input.textChanged.connect(self._mark_criteria_dirty)
        self.criteria_requirements_input.textChanged.connect(self._mark_criteria_dirty)
        self.session_list.currentItemChanged.connect(self._on_session_changed)
        self.candidate_table.itemSelectionChanged.connect(self._on_candidate_selected)
        self.event_bus.subscribe(self._queue_runtime_event)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #eef4fb;
                color: #172033;
                font-family: "Microsoft YaHei", "Segoe UI";
                font-size: 13px;
            }
            QFrame#TopBar, QFrame#Panel {
                background: #ffffff;
                border: 1px solid #d7e2f2;
                border-radius: 8px;
            }
            QLabel#TitleLabel {
                font-weight: 700;
                font-size: 16px;
            }
            QFrame#SessionItem {
                background: #f8fbff;
                border: 1px solid #d7e2f2;
                border-radius: 6px;
                margin: 3px;
            }
            QLabel#SessionTitle {
                font-weight: 700;
                color: #172033;
            }
            QLabel#SessionInfo {
                color: #64748b;
                font-size: 12px;
            }
            QPushButton {
                background: #2563eb;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: #1d4ed8;
            }
            QListWidget, QTextBrowser, QTextEdit, QTableWidget, QLineEdit, QComboBox, QSpinBox {
                background: #f8fbff;
                border: 1px solid #d7e2f2;
                border-radius: 5px;
                padding: 4px;
            }
            QHeaderView::section {
                background: #e8f0ff;
                border: none;
                padding: 6px;
                font-weight: 700;
            }
            """
        )

    def create_session(self) -> None:
        dialog = NewSessionDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        payload = dialog.payload()
        session_id = self.store.create_session(
            title=str(payload["title"]),
            jd_text=str(payload["jd_text"]),
            user_notes=str(payload["user_notes"]),
            mode=str(payload["mode"]),
            max_rounds=int(payload["max_rounds"]),
            max_detail_fetches=int(payload["max_detail_fetches"]),
            target_ab_count=int(payload["target_ab_count"]),
        )
        self.selected_session_id = session_id
        self.refresh_all()
        self._select_session_in_list(session_id)
        self._queue_criteria_draft(session_id)

    def create_session_and_add_to_pool(self) -> None:
        dialog = NewSessionDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        payload = dialog.payload()
        session_id = self.store.create_session(
            title=str(payload["title"]),
            jd_text=str(payload["jd_text"]),
            user_notes=str(payload["user_notes"]),
            mode=str(payload["mode"]),
            max_rounds=int(payload["max_rounds"]),
            max_detail_fetches=int(payload["max_detail_fetches"]),
            target_ab_count=int(payload["target_ab_count"]),
        )
        self.store.add_session_to_pool(session_id)
        self.selected_session_id = session_id
        self.refresh_all()
        self._select_session_in_list(session_id)
        self._queue_criteria_draft(session_id)

    def start_selected_session(self) -> None:
        if not self.selected_session_id:
            QMessageBox.information(self, "提示", "请先选择或新建任务")
            return
        self.continue_session(self.selected_session_id)

    def toggle_session_run(self, session_id: str) -> None:
        session = self.store.get_session(session_id) or {}
        if (
            str(session.get("status") or "") == "running"
            and self.runtime.is_active(session_id)
        ):
            self.runtime.pause_session(session_id)
            self._mark_dirty()
            return
        self.continue_session(session_id)

    def continue_session(self, session_id: str) -> None:
        self.selected_session_id = session_id
        self._select_session_in_list(session_id)
        session = self.store.get_session(session_id) or {}
        criteria = self.store.get_latest_criteria_version(session_id, "confirmed")
        if not criteria:
            QMessageBox.information(
                self,
                "需要确认寻访基准",
                "请先确认右侧的“匹配词与岗位要求”，确认后 Agent 才会开始搜索。",
            )
            self._mark_dirty()
            return
        if self._criteria_dirty:
            QMessageBox.information(
                self,
                "基准有未确认修改",
                "你修改了“匹配词与岗位要求”，请先点击“确认寻访基准”。",
            )
            return
        if self.runtime.is_active(session_id):
            self.runtime.resume_session(session_id)
            self._mark_dirty()
            return
        config = self.config_manager.config
        if not (config.api_base_url and config.api_key and config.model_name):
            QMessageBox.warning(
                self,
                "缺少配置",
                "请先在“设置”里配置 API Base URL、API Key 和模型名称。",
            )
            return
        try:
            logged_in = self.runtime.browser_queue.run(
                self.runtime.liepin_tool.check_login
            )
        except Exception as exc:
            self.open_liepin_browser()
            QMessageBox.warning(
                self,
                "猎聘未就绪",
                "检查猎聘登录失败，已尝试自动打开猎聘页面。\n{}\n\n请在打开的浏览器中完成登录后，再点击开始。".format(
                    exc
                ),
            )
            return
        if not logged_in:
            self.open_liepin_browser()
            QMessageBox.warning(
                self,
                "需要登录",
                "已自动打开猎聘页面。请在浏览器中完成登录后，再点击开始。",
            )
            return
        if str(session.get("status") or "") in {"completed", "cancelled"}:
            reply = QMessageBox.question(
                self,
                "继续任务",
                "该任务已经结束。继续会基于已有记录追加后续轮次，确定继续吗？",
            )
            if reply != QMessageBox.Yes:
                return
        self.runtime.start_session(session_id)
        self._mark_dirty()

    def pause_selected_session(self) -> None:
        if self.selected_session_id:
            self.runtime.pause_session(self.selected_session_id)

    def resume_selected_session(self) -> None:
        if self.selected_session_id:
            self.continue_session(self.selected_session_id)

    def cancel_selected_session(self) -> None:
        if self.selected_session_id:
            self.stop_session(self.selected_session_id)

    def export_selected_session(self) -> None:
        if not self.selected_session_id:
            return
        self.export_session(self.selected_session_id)

    def stop_session(self, session_id: str) -> None:
        self.runtime.cancel_session(session_id)
        self.store.add_event(
            session_id,
            None,
            "manual_stop",
            "用户终止任务",
            "用户点击终止，已请求停止当前任务。",
            {},
        )
        self._criteria_dirty = False
        self._mark_dirty()

    def send_user_command(self, session_id: str, command: str) -> None:
        command = command.strip()
        if not command:
            return
        self.store.set_pending_user_command(session_id, command)
        self.store.add_event(
            session_id,
            None,
            "user_command",
            "用户发送指令",
            command,
            {},
        )
        self._mark_dirty()

    def export_session(self, session_id: str) -> None:
        exporter = ExportService(self.store, self.workspace_root / "exports")
        path = exporter.export_session(session_id)
        message = "Excel 总览：\n{}".format(path)
        if exporter.last_candidate_reports_dir:
            message += "\n\n候选人 Word 档案：\n{}".format(
                exporter.last_candidate_reports_dir
            )
        QMessageBox.information(self, "导出完成", message)

    def greet_selected_candidate(self) -> None:
        session_id = self.selected_session_id
        candidate_ids = self._selected_candidate_ids()
        if not candidate_ids or not session_id:
            QMessageBox.information(
                self, "提示", "请先在候选人池中选择一位或多位候选人。"
            )
            return
        if self.runtime.is_active(session_id):
            QMessageBox.information(
                self,
                "任务运行中",
                "请先暂停或等待任务结束后，再手动打招呼，避免浏览器页面被并发操作。",
            )
            return
        candidates = self.store.get_candidates_by_ids(candidate_ids)
        if not candidates:
            QMessageBox.warning(self, "提示", "未找到选中的候选人记录。")
            return
        candidates_by_id = {str(item.get("id") or ""): dict(item) for item in candidates}
        targets = []
        skipped = []
        already_greeted = []
        contact_present = []
        for candidate_id in candidate_ids:
            candidate = candidates_by_id.get(candidate_id)
            if not candidate:
                skipped.append("候选人记录不存在")
                continue
            name = str(candidate.get("name") or "候选人")
            detail = self.store.get_candidate_detail(candidate_id) or {}
            if not detail:
                skipped.append("{}：尚未抓取详情".format(name))
                continue
            current_status = str(detail.get("greeting_status") or "")
            if current_status == "pending":
                skipped.append("{}：手动打招呼正在执行".format(name))
                continue
            profile_url = self._candidate_profile_url(candidate, detail)
            if not profile_url:
                skipped.append("{}：缺少抓取到的候选人链接".format(name))
                continue
            if current_status in {"success", "already_greeted"}:
                already_greeted.append(
                    "{}（{}）".format(name, self._greeting_status_label(current_status))
                )
            if self._resume_has_contact_info(str(detail.get("resume_text") or "")):
                contact_present.append(name)
            candidate["profile_url"] = profile_url
            targets.append(
                {
                    "candidate_id": candidate_id,
                    "candidate": candidate,
                    "profile_url": profile_url,
                }
            )
        if not targets:
            message = "选中的候选人暂不能打招呼。"
            if skipped:
                message += "\n{}".format("\n".join(skipped[:6]))
            QMessageBox.information(self, "提示", message)
            return
        confirm_lines = []
        if len(targets) > 1:
            confirm_lines.append(
                "将按队列依次为 {} 位候选人打开抓取到的详情链接并打招呼。".format(
                    len(targets)
                )
            )
        if skipped:
            confirm_lines.append(
                "已跳过 {} 位：{}".format(len(skipped), "；".join(skipped[:4]))
            )
        if already_greeted:
            confirm_lines.append(
                "{} 位已标记打过招呼：{}".format(
                    len(already_greeted), "；".join(already_greeted[:4])
                )
            )
        if contact_present:
            confirm_lines.append(
                "{} 位简历详情中已出现联系方式：{}".format(
                    len(contact_present), "；".join(contact_present[:4])
                )
            )
        if confirm_lines:
            confirm_lines.append("仍要继续吗？")
            reply = QMessageBox.question(
                self,
                "确认打招呼",
                "\n".join(confirm_lines),
            )
            if reply != QMessageBox.Yes:
                return
        template = str(self.config_manager.config.greeting_template or "")
        for target in targets:
            candidate_id = str(target["candidate_id"])
            candidate = dict(target["candidate"])
            profile_url = str(target["profile_url"])
            self.store.update_candidate_greeting_status(
                candidate_id, "pending", message="手动打招呼进行中。"
            )
            self.store.add_event(
                session_id,
                None,
                "manual_greeting",
                "手动打招呼已启动",
                "{} / {}".format(
                    candidate.get("name") or "候选人",
                    candidate.get("current_title") or "",
                ),
                {"candidate_id": candidate_id, "profile_url": profile_url},
            )
            future = self.runtime.browser_queue.submit(
                self.runtime.liepin_tool.greet_candidate,
                candidate,
                message_template=template,
            )
            future.add_done_callback(
                self._manual_greeting_done_callback(
                    session_id, candidate_id, profile_url
                )
            )
        self.manual_greeting_btn.setEnabled(False)
        self.manual_greeting_btn.setToolTip("手动打招呼正在执行。")
        self._mark_dirty()

    def _manual_greeting_done_callback(
        self, session_id: str, candidate_id: str, profile_url: str
    ):
        def _done(done_future):
            try:
                result = done_future.result()
                status = str(result.get("status") or "failed")
                message = str(result.get("message") or "")
                error = str(result.get("error") or "")
            except Exception as exc:
                status = "failed"
                message = ""
                error = str(exc)
            self.store.update_candidate_greeting_status(
                candidate_id, status, message=message, error=error
            )
            title = {
                "success": "手动打招呼成功",
                "already_greeted": "候选人已打过招呼",
                "skipped": "手动打招呼已跳过",
            }.get(status, "手动打招呼失败")
            self.store.add_event(
                session_id,
                None,
                "manual_greeting",
                title,
                message or error or status,
                {
                    "candidate_id": candidate_id,
                    "status": status,
                    "profile_url": profile_url,
                },
            )
            self.event_bus.publish(
                "manual_greeting_done",
                {
                    "session_id": session_id,
                    "candidate_id": candidate_id,
                    "status": status,
                    "error": error,
                },
            )

        return _done

    def delete_session(self, session_id: str) -> None:
        session = self.store.get_session(session_id) or {}
        title = session.get("title") or "该任务"
        if self.runtime.is_active(session_id):
            self.runtime.cancel_session(session_id)
            QMessageBox.information(
                self,
                "任务仍在停止中",
                "已请求终止“{}”。请等待任务停止后再删除，避免后台任务继续写入。".format(
                    title
                ),
            )
            self._mark_dirty()
            return
        reply = QMessageBox.question(
            self,
            "删除任务",
            "确定删除“{}”及其所有候选人、日志和匹配结果吗？".format(title),
        )
        if reply != QMessageBox.Yes:
            return
        self.runtime.cancel_session(session_id)
        self.store.delete_session(session_id)
        if self.selected_session_id == session_id:
            self.selected_session_id = None
        self.refresh_all()

    def open_liepin_browser(self) -> None:
        self.stage_label.setText("正在打开猎聘浏览器...")
        future = self.runtime.browser_queue.submit(
            self.runtime.liepin_tool.open_for_login_or_search
        )

        def _done(done_future):
            try:
                done_future.result()
                self.event_bus.publish("browser_ready", {})
            except Exception as exc:
                self.event_bus.publish("browser_error", {"error": str(exc)})

        future.add_done_callback(_done)

    def close_liepin_browser(self) -> None:
        active_session_ids = [
            session_id
            for session_id in list(self.runtime._threads.keys())
            if self.runtime.is_active(session_id)
        ]
        if active_session_ids:
            reply = QMessageBox.question(
                self,
                "关闭浏览器",
                "当前仍有任务线程可能在使用浏览器。是否先请求停止任务并关闭浏览器？",
            )
            if reply != QMessageBox.Yes:
                return
            for session_id in active_session_ids:
                self.runtime.cancel_session(session_id)
        self.stage_label.setText("正在关闭猎聘浏览器...")
        future = self.runtime.browser_queue.submit(self.runtime.liepin_tool.close_browser)

        def _done(done_future):
            try:
                done_future.result()
                self.event_bus.publish("browser_closed", {})
            except Exception as exc:
                self.event_bus.publish("browser_error", {"error": str(exc)})

        future.add_done_callback(_done)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config_manager, self)
        if dialog.exec() != QDialog.Accepted:
            return
        active_session_ids = [
            session_id
            for session_id in list(self.runtime._threads.keys())
            if self.runtime.is_active(session_id)
        ]
        if active_session_ids:
            QMessageBox.information(
                self,
                "设置已保存",
                "当前仍有任务运行，暂不重载浏览器和匹配服务；停止任务后重新打开设置即可让新配置生效。",
            )
            self._mark_dirty()
            return
        self._rebuild_runtime_tools()
        QMessageBox.information(
            self, "设置已保存", "真实猎聘工具和匹配服务已重新加载。"
        )

    def regenerate_criteria_draft(self) -> None:
        if not self.selected_session_id:
            return
        self._queue_criteria_draft(self.selected_session_id)
        self._mark_dirty()

    def confirm_current_criteria(self) -> None:
        if not self.selected_session_id:
            return
        criteria = self.store.get_latest_criteria_version(self.selected_session_id)
        keywords = self.criteria_keywords_input.toPlainText().strip()
        requirements = self.criteria_requirements_input.toPlainText().strip()
        if not keywords or not requirements:
            QMessageBox.warning(self, "提示", "请先填写关键词和岗位要求描述。")
            return
        if criteria:
            if str(criteria.get("status") or "") == "confirmed":
                session = self.store.get_session(self.selected_session_id) or {}
                criteria_id = self.store.create_criteria_version(
                    self.selected_session_id,
                    keywords,
                    requirements,
                    source_jd_text=str(session.get("jd_text") or ""),
                    source_user_notes=str(session.get("user_notes") or ""),
                    created_by="human",
                )
                self.store.confirm_criteria_version(criteria_id)
            else:
                self.store.update_criteria_version(
                    str(criteria["id"]), keywords, requirements, status="draft"
                )
                self.store.confirm_criteria_version(str(criteria["id"]))
        else:
            session = self.store.get_session(self.selected_session_id) or {}
            criteria_id = self.store.create_criteria_version(
                self.selected_session_id,
                keywords,
                requirements,
                source_jd_text=str(session.get("jd_text") or ""),
                source_user_notes=str(session.get("user_notes") or ""),
                created_by="human",
            )
            self.store.confirm_criteria_version(criteria_id)
        self.store.add_event(
            self.selected_session_id,
            None,
            "criteria_confirmed",
            "寻访基准已确认",
            "后续搜索、抓详情和匹配将基于当前匹配词与岗位要求执行。",
            {"keywords_text": keywords, "requirements_text": requirements},
        )
        self._mark_dirty()

    def confirm_criteria_and_start(self) -> None:
        self.confirm_current_criteria()
        if self.selected_session_id:
            self.continue_session(self.selected_session_id)

    def _queue_criteria_draft(self, session_id: str) -> None:
        self.store.add_event(
            session_id,
            None,
            "criteria_draft",
            "AI 正在生成寻访基准草案",
            "系统正在后台生成匹配词与岗位要求，界面可继续操作。",
            {},
        )
        self._mark_dirty()
        future = self.runtime.match_queue.submit(self._generate_criteria_draft, session_id)

        def _done(done_future):
            try:
                done_future.result()
                self.event_bus.publish("criteria_ready", {"session_id": session_id})
            except Exception as exc:
                self.store.add_event(
                    session_id,
                    None,
                    "criteria_draft",
                    "寻访基准草案生成失败",
                    "可在右侧手动填写匹配词与岗位要求。错误：{}".format(exc),
                    {},
                )
                self.event_bus.publish("criteria_error", {"session_id": session_id})

        future.add_done_callback(_done)

    def _generate_criteria_draft(self, session_id: str) -> None:
        session = self.store.get_session(session_id) or {}
        try:
            criteria = self.runtime.brain.build_criteria(
                str(session.get("jd_text") or ""),
                str(session.get("user_notes") or ""),
            )
        except Exception:
            criteria = {}
        keywords = str(criteria.get("keywords_text") or "").strip()
        requirements = str(criteria.get("requirements_text") or "").strip()
        if not keywords:
            keywords = "\n".join(str(item) for item in criteria.get("core_terms", []) if item)
        if not requirements:
            requirements = "请人工填写本岗位最关键的匹配要求。"
        self.store.create_criteria_version(
            session_id,
            keywords,
            requirements,
            source_jd_text=str(session.get("jd_text") or ""),
            source_user_notes=str(session.get("user_notes") or ""),
            ai_raw_response=criteria,
            created_by="ai",
            status="draft",
        )
        self.store.add_event(
            session_id,
            None,
            "criteria_draft",
            "AI 已生成寻访基准草案",
            "请人工确认或修改匹配词与岗位要求。",
            {"keywords_text": keywords, "requirements_text": requirements},
        )

    def _on_session_changed(
        self, current: QListWidgetItem, _previous: QListWidgetItem
    ) -> None:
        if current is None:
            return
        self.selected_session_id = current.data(Qt.UserRole)
        self.selected_candidate_id = None
        self.detail_view.clear()
        self._update_manual_greeting_button_state()
        self._mark_dirty()

    def _on_candidate_selected(self) -> None:
        candidate_ids = self._selected_candidate_ids()
        if not candidate_ids:
            self.selected_candidate_id = None
            self.detail_view.clear()
            self._update_manual_greeting_button_state()
            return
        self.selected_candidate_id = candidate_ids[0]
        self._render_candidate_detail(candidate_ids[0])
        self._update_manual_greeting_button_state()

    def _selected_candidate_ids(self) -> list[str]:
        if not hasattr(self, "candidate_table"):
            return []
        selection_model = self.candidate_table.selectionModel()
        rows = []
        if selection_model is not None:
            rows = sorted({index.row() for index in selection_model.selectedRows()})
        if not rows:
            rows = sorted({item.row() for item in self.candidate_table.selectedItems()})
        candidate_ids = []
        for row in rows:
            item = self.candidate_table.item(row, 0)
            if not item:
                continue
            candidate_id = str(item.data(Qt.UserRole) or "")
            if candidate_id and candidate_id not in candidate_ids:
                candidate_ids.append(candidate_id)
        return candidate_ids

    def _update_manual_greeting_button_state(self) -> None:
        if not hasattr(self, "manual_greeting_btn"):
            return
        enabled = False
        candidate_ids = self._selected_candidate_ids()
        count = len(candidate_ids)
        self.manual_greeting_btn.setText(
            "批量打招呼({})".format(count) if count > 1 else "手动打招呼"
        )
        tooltip = "请先选择一位或多位已抓取详情的候选人。"
        if self.selected_session_id and candidate_ids:
            if self.runtime.is_active(self.selected_session_id):
                tooltip = "任务运行中，暂停或结束后再手动打招呼。"
            else:
                candidates = self.store.get_candidates_by_ids(candidate_ids)
                candidates_by_id = {
                    str(candidate.get("id") or ""): candidate for candidate in candidates
                }
                eligible = 0
                pending = 0
                missing = 0
                for candidate_id in candidate_ids:
                    detail = self.store.get_candidate_detail(candidate_id) or {}
                    candidate = candidates_by_id.get(candidate_id) or {}
                    if not detail:
                        missing += 1
                        continue
                    if str(detail.get("greeting_status") or "") == "pending":
                        pending += 1
                        continue
                    if not self._candidate_profile_url(candidate, detail):
                        missing += 1
                        continue
                    eligible += 1
                if eligible:
                    enabled = True
                    tooltip = "已选 {} 位，其中 {} 位可用抓取到的详情链接依次打招呼。".format(
                        count, eligible
                    )
                    if pending or missing:
                        tooltip += " 其余 {} 位会被跳过。".format(pending + missing)
                elif pending:
                    tooltip = "选中的候选人正在执行手动打招呼。"
                else:
                    tooltip = "选中的候选人缺少详情或抓取到的候选人链接。"
        self.manual_greeting_btn.setEnabled(enabled)
        self.manual_greeting_btn.setToolTip(tooltip)

    def _mark_dirty(self) -> None:
        self._dirty = True
        # Trigger refresh on next event loop iteration instead of waiting for timer
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._refresh_if_dirty)

    def _queue_runtime_event(
        self, event_type: str, payload: Dict[str, object]
    ) -> None:
        with self._runtime_events_lock:
            self._runtime_events.append((event_type, payload or {}))
        self._dirty = True

    def _drain_runtime_events(self) -> None:
        events = []
        with self._runtime_events_lock:
            while self._runtime_events:
                events.append(self._runtime_events.popleft())
        for event_type, payload in events:
            self._handle_runtime_event(event_type, payload)

    def _handle_runtime_event(
        self, event_type: str, payload: Dict[str, object]
    ) -> None:
        if event_type == "browser_ready":
            self._pending_status_text = "猎聘浏览器已打开"
        elif event_type == "browser_closed":
            self._pending_status_text = "猎聘浏览器已关闭"
        elif event_type == "browser_error":
            self._pending_status_text = "猎聘浏览器打开失败"
            self._pending_browser_error = str(payload.get("error") or "未知错误")
        elif event_type == "manual_greeting_done":
            if str(payload.get("status") or "") in {"success", "already_greeted"}:
                self._pending_status_text = "手动打招呼完成"
            else:
                self._pending_status_text = "手动打招呼未完成"
        elif event_type == "criteria_ready":
            session_id = str(payload.get("session_id") or "")
            if self._queue_running and session_id:
                entry = self.store.get_pool_entry(session_id)
                if entry and entry.get("status") == "active":
                    self._show_pool_notification(
                        session_id, entry.get("title") or "未命名"
                    )
            self._mark_dirty()
        else:
            self._mark_dirty()

    def _refresh_if_dirty(self) -> None:
        self._drain_runtime_events()
        if self._pending_status_text:
            if "打开失败" in self._pending_status_text:
                self.browser_label.setText("浏览器：打开失败")
            elif "已关闭" in self._pending_status_text:
                self.browser_label.setText("浏览器：已关闭")
            elif "已打开" in self._pending_status_text:
                self.browser_label.setText("浏览器：已打开，请确认已登录")
            self.stage_label.setText(self._pending_status_text)
            self._pending_status_text = ""
        if self._pending_browser_error:
            error = self._pending_browser_error
            self._pending_browser_error = ""
            QMessageBox.warning(self, "猎聘浏览器打开失败", error)
        if self._dirty:
            self.refresh_all()
        self._check_queue_advance()

    def refresh_all(self) -> None:
        self._dirty = False
        self._refresh_pool()
        self._refresh_sessions()
        self._refresh_selected_session()

    def _refresh_sessions(self) -> None:
        sessions = self.store.list_sessions()
        current_id = self.selected_session_id
        self.session_list.blockSignals(True)
        self.session_list.clear()
        for session in sessions:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, session["id"])
            widget = SessionListItemWidget(session, self)
            item.setSizeHint(widget.sizeHint())
            self.session_list.addItem(item)
            self.session_list.setItemWidget(item, widget)
            if session["id"] == current_id:
                self.session_list.setCurrentItem(item)
        self.session_list.blockSignals(False)
        if not current_id and sessions:
            self.selected_session_id = sessions[0]["id"]
            self.session_list.setCurrentRow(0)

    def _select_session_in_list(self, session_id: str) -> None:
        for index in range(self.session_list.count()):
            item = self.session_list.item(index)
            if item.data(Qt.UserRole) == session_id:
                self.session_list.setCurrentItem(item)
                break

    def _refresh_selected_session(self) -> None:
        if not self.selected_session_id:
            self.selected_candidate_id = None
            self.title_label.setText("未选择任务")
            self.stage_label.setText("就绪")
            self.timeline.clear()
            self.candidate_table.setRowCount(0)
            self.strategy_view.clear()
            self.detail_view.clear()
            self.log_view.clear()
            self._update_manual_greeting_button_state()
            return
        session = self.store.get_session(self.selected_session_id) or {}
        sessions = {item["id"]: item for item in self.store.list_sessions()}
        aggregate = sessions.get(self.selected_session_id, {})
        metrics = self.store.session_efficiency_metrics(self.selected_session_id)
        self.title_label.setText(str(session.get("title") or "未命名任务"))
        latest_event = self._latest_event(self.selected_session_id)
        stage_text = latest_event.get("title") if latest_event else ""
        if session.get("error_message"):
            stage_text = "{} | {}".format(
                stage_text or "注意", session.get("error_message")
            )
        self.stage_label.setText(
            "状态：{}{}".format(
                session.get("status") or "",
                " | {}".format(stage_text) if stage_text else "",
            )
        )
        self.stats_label.setText(
            "轮次 {} | 读卡 {} | 候选人 {} | 详情 {} | A/B {} | A/B/详情 {}".format(
                len(self.store.list_rounds(self.selected_session_id)),
                metrics.get("raw_candidate_count") or 0,
                aggregate.get("candidate_count") or 0,
                aggregate.get("detail_count") or 0,
                aggregate.get("ab_count") or 0,
                metrics.get("ab_per_detail_fetch") or 0,
            )
        )
        self._render_timeline()
        self._render_criteria_editor()
        self._render_candidates()
        self._render_strategy()
        self._render_logs()
        self._update_manual_greeting_button_state()

    def _render_timeline(self) -> None:
        events = self.store.list_events(self.selected_session_id)
        lines = []
        for event in events[-80:]:
            lines.append(
                "<p><b>{}</b> <span style='color:#64748b'>{}</span><br>{}</p>".format(
                    self._html(event.get("title")),
                    self._html(event.get("created_at")),
                    self._html(event.get("message")).replace("\n", "<br>"),
                )
            )
        self.timeline.setHtml("".join(lines))
        self.timeline.verticalScrollBar().setValue(
            self.timeline.verticalScrollBar().maximum()
        )

    def _render_criteria_editor(self) -> None:
        criteria = (
            self.store.get_latest_criteria_version(self.selected_session_id, "draft")
            or self.store.get_latest_criteria_version(
                self.selected_session_id, "confirmed"
            )
            or {}
        )
        if self.criteria_keywords_input.hasFocus() or self.criteria_requirements_input.hasFocus():
            return
        self.criteria_keywords_input.blockSignals(True)
        self.criteria_requirements_input.blockSignals(True)
        self.criteria_keywords_input.setPlainText(str(criteria.get("keywords_text") or ""))
        self.criteria_requirements_input.setPlainText(
            str(criteria.get("requirements_text") or "")
        )
        self.criteria_keywords_input.blockSignals(False)
        self.criteria_requirements_input.blockSignals(False)
        self._criteria_dirty = False
        is_confirmed = str(criteria.get("status") or "") == "confirmed"
        self.confirm_criteria_btn.setText(
            "已确认" if is_confirmed else "确认寻访基准"
        )

    def _render_logs(self) -> None:
        events = self.store.list_events(self.selected_session_id)
        lines = []
        for event in events[-120:]:
            payload = event.get("payload") or {}
            payload_text = ""
            if payload:
                import json

                payload_text = (
                    "<pre style='white-space: pre-wrap; color:#475569'>{}</pre>".format(
                        self._html(json.dumps(payload, ensure_ascii=False, indent=2))
                    )
                )
            lines.append(
                "<div style='margin-bottom:10px'>"
                "<b>{}</b> <span style='color:#64748b'>{} / {}</span><br>"
                "{}{}"
                "</div>".format(
                    self._html(event.get("title")),
                    self._html(event.get("created_at")),
                    self._html(event.get("event_type")),
                    self._html(event.get("message")).replace("\n", "<br>"),
                    payload_text,
                )
            )
        self.log_view.setHtml("".join(lines))
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def _latest_event(self, session_id: str) -> Dict[str, object]:
        events = self.store.list_events(session_id)
        return events[-1] if events else {}

    def _render_candidates(self) -> None:
        selected_ids = set(self._selected_candidate_ids())
        if self.selected_candidate_id:
            selected_ids.add(self.selected_candidate_id)
        candidates = self.store.list_candidates(self.selected_session_id)
        available_ids = {str(candidate.get("id") or "") for candidate in candidates}
        selected_ids = {
            candidate_id for candidate_id in selected_ids if candidate_id in available_ids
        }
        self.candidate_table.blockSignals(True)
        self.candidate_table.setRowCount(len(candidates))
        for row, candidate in enumerate(candidates):
            candidate_id = str(candidate.get("id") or "")
            values = [
                candidate.get("name") or "",
                candidate.get("current_company") or "",
                candidate.get("current_title") or "",
                candidate.get("city") or "",
                candidate.get("work_years") or "",
                candidate.get("education") or "",
                "是" if int(candidate.get("is_gold_collar") or 0) == 1 else "否",
                self._card_decision_label(candidate.get("card_decision") or ""),
                candidate.get("match_tier") or "",
                self._greeting_status_label(candidate.get("greeting_status") or ""),
                candidate.get("status") or "",
                candidate.get("summary_text") or "",
            ]
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                if column == 0:
                    table_item.setData(Qt.UserRole, candidate_id)
                if column in {6, 7, 9}:
                    table_item.setTextAlignment(Qt.AlignCenter)
                self.candidate_table.setItem(row, column, table_item)
        selection_model = self.candidate_table.selectionModel()
        if selection_model is not None:
            selection_model.clearSelection()
            for row, candidate in enumerate(candidates):
                candidate_id = str(candidate.get("id") or "")
                if candidate_id in selected_ids:
                    selection_model.select(
                        self.candidate_table.model().index(row, 0),
                        QItemSelectionModel.Select | QItemSelectionModel.Rows,
                    )
        self.candidate_table.blockSignals(False)
        selected_after_refresh = self._selected_candidate_ids()
        self.selected_candidate_id = (
            selected_after_refresh[0] if selected_after_refresh else None
        )
        self.candidate_table.resizeColumnsToContents()
        self.candidate_table.setColumnWidth(11, 260)

    def _render_strategy(self) -> None:
        rounds = self.store.list_rounds(self.selected_session_id)
        criteria = self.store.get_latest_criteria(self.selected_session_id)
        if not rounds:
            self.strategy_view.setHtml("<p>等待 Agent 生成搜索计划。</p>")
            return
        last = rounds[-1]
        filters = from_json(last.get("filters_json"), {})
        html = """
        <p><b>第 {round_index} 轮</b> | {status}</p>
        <p><b>打招呼：</b>{auto_greeting}</p>
        <p><b>寻访基准：</b>v{criteria_version}</p>
        <p><b>关键词：</b>{keywords}</p>
        <p><b>岗位要求：</b>{requirements}</p>
        <p><b>搜索栏：</b>{query}</p>
        <p><b>职位栏：</b>{position}</p>
        <p><b>搜索假设：</b>{hypothesis_type} / {hypothesis_text}</p>
        <p><b>范围：</b>{scope} / {match_mode}</p>
        <p><b>城市：</b>{city}</p>
        <p><b>意图：</b>{intent}</p>
        <p><b>统计：</b>结果 {raw_count}，建议抓详情 {prequalified_count}，抓详情 {detail_fetch_count}，A/B {ab_count}</p>
        """.format(
            round_index=self._html(last.get("round_index")),
            status=self._html(last.get("status")),
            auto_greeting=self._html(self._greeting_policy_text()),
            criteria_version=self._html(criteria.get("version") or ""),
            keywords=self._html((criteria.get("keywords_text") or "").replace("\n", "、")),
            requirements=self._html(criteria.get("requirements_text") or ""),
            query=self._html(last.get("query")),
            position=self._html(last.get("position_filter") or "不限"),
            hypothesis_type=self._html(last.get("search_hypothesis_type") or ""),
            hypothesis_text=self._html(last.get("search_hypothesis_text") or ""),
            scope=self._html(last.get("scope")),
            match_mode=self._html(last.get("match_mode")),
            city=self._html(
                "、".join(filters.get("city") if isinstance(filters.get("city"), list) else [])
                or str(filters.get("city") or "")
                or "不限"
            ),
            intent=self._html(last.get("intent")),
            raw_count=self._html(last.get("raw_count") or 0),
            prequalified_count=self._html(last.get("prequalified_count") or 0),
            detail_fetch_count=self._html(last.get("detail_fetch_count") or 0),
            ab_count=self._html(last.get("ab_count") or 0),
        )
        self.strategy_view.setHtml(html)

    def _greeting_policy_text(self) -> str:
        config = self.config_manager.config
        if config.greeting_template:
            return "仅人工选择候选人后手动触发；使用自定义话术"
        return "仅人工选择候选人后手动触发；使用平台默认打招呼"

    def _render_candidate_detail(self, candidate_id: str) -> None:
        candidates = self.store.get_candidates_by_ids([candidate_id])
        if not candidates:
            return
        candidate = candidates[0]
        detail = self.store.get_candidate_detail(candidate_id) or {}
        matches = [
            item
            for item in self.store.list_match_results(self.selected_session_id)
            if item.get("candidate_id") == candidate_id
        ]
        match = matches[0] if matches else {}
        sources = self.store.list_candidate_sources(candidate_id)
        evidence = match.get("matched_evidence") or []
        evidence_html = "".join(
            "<li><b>{}</b>：{} <span style='color:#64748b'>{}</span></li>".format(
                self._html(item.get("criterion") or ""),
                self._html(item.get("evidence") or ""),
                self._html(item.get("strength") or ""),
            )
            for item in evidence
            if isinstance(item, dict)
        )
        unknowns_html = "".join(
            "<li>{}</li>".format(self._html(item))
            for item in (match.get("missing_or_unclear") or [])
        )
        questions_html = "".join(
            "<li>{}</li>".format(self._html(item))
            for item in (match.get("questions_to_verify") or [])
        )
        source_html = "".join(
            "<li>第{}轮：{} / {} / 排名 {}</li>".format(
                self._html(source.get("round_id") or ""),
                self._html(source.get("query") or ""),
                self._html(source.get("search_hypothesis_type") or ""),
                self._html(source.get("result_index") or 0),
            )
            for source in sources[-8:]
        )
        is_gold = int(detail.get("is_gold_collar") or 0) == 1
        html = """
        <p><b>{name}</b> / {title}</p>
        <p>{company} | {city} | {work_years} | {education}</p>
        <p><b>金领：</b>{gold} | <b>打招呼：</b>{greeting_status}</p>
        <p><b>卡片判断：</b>{card_decision}</p>
        <p><b>匹配：</b>{tier} {recommendation}</p>
        <p><b>摘要：</b>{summary}</p>
        <p><b>风险：</b>{risks}</p>
        <p><b>命中证据：</b></p><ul>{evidence}</ul>
        <p><b>缺口/未知：</b></p><ul>{unknowns}</ul>
        <p><b>待确认问题：</b></p><ul>{questions}</ul>
        <p><b>来源历史：</b></p><ul>{sources}</ul>
        <hr>
        <pre style="white-space: pre-wrap;">{resume}</pre>
        """.format(
            name=self._html(candidate.get("name")),
            title=self._html(candidate.get("current_title")),
            company=self._html(candidate.get("current_company")),
            city=self._html(candidate.get("city")),
            work_years=self._html(candidate.get("work_years")),
            education=self._html(candidate.get("education")),
            gold="是" if is_gold else "否",
            greeting_status=self._html(
                self._greeting_status_label(detail.get("greeting_status") or "")
                or detail.get("greeting_error")
                or "未触发"
            ),
            card_decision=self._html(
                self._card_decision_label(candidate.get("card_decision") or "")
            ),
            tier=self._html(match.get("tier") or "待匹配"),
            recommendation=self._html(match.get("recommendation")),
            summary=self._html(
                match.get("summary") or candidate.get("summary_text") or ""
            ),
            risks=self._html(match.get("risks") or "暂无"),
            evidence=evidence_html or "<li>暂无</li>",
            unknowns=unknowns_html or "<li>暂无</li>",
            questions=questions_html or "<li>暂无</li>",
            sources=source_html or "<li>暂无</li>",
            resume=self._html(detail.get("resume_text") or "尚未抓取详情。"),
        )
        self.detail_view.setHtml(html)

    @staticmethod
    def _html(value: object) -> str:
        return escape(str(value or ""), quote=True)

    @classmethod
    def _candidate_profile_url(
        cls, candidate: Dict[str, object], detail: Dict[str, object]
    ) -> str:
        raw_payload = from_json(detail.get("raw_payload_json"), {}) or {}
        profile_url = cls._find_url(raw_payload)
        if profile_url:
            return profile_url
        return str(candidate.get("profile_url") or "")

    @classmethod
    def _find_url(cls, value: object) -> str:
        if isinstance(value, str):
            if value.startswith(("http://", "https://")):
                return value
            nested = from_json(value, None)
            return cls._find_url(nested) if nested is not None else ""
        if isinstance(value, dict):
            for key in ("profile_url", "resume_url", "detail_url", "url", "href"):
                url = value.get(key)
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    return url
            for child in value.values():
                url = cls._find_url(child)
                if url:
                    return url
        if isinstance(value, list):
            for child in value:
                url = cls._find_url(child)
                if url:
                    return url
        return ""

    @staticmethod
    def _resume_has_contact_info(resume_text: str) -> bool:
        text = resume_text or ""
        return bool(
            re.search(r"1[3-9]\d{9}", text)
            or re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
            or re.search(
                r"(?:微信|VX|WX|WeChat|电话|手机|QQ)[:：\s]*[A-Za-z0-9_\-]{5,}",
                text,
                re.I,
            )
        )

    def _mark_criteria_dirty(self) -> None:
        self._criteria_dirty = True

    @staticmethod
    def _card_decision_label(value: object) -> str:
        return {
            "fetch": "值得抓详情",
            "maybe": "信息不足",
            "noise": "明显噪音",
        }.get(str(value or ""), "信息不足")

    @staticmethod
    def _greeting_status_label(value: object) -> str:
        return {
            "success": "已发送",
            "already_greeted": "已打过",
            "skipped": "已跳过",
            "failed": "失败",
            "pending": "待处理",
        }.get(str(value or ""), "")

    # ------------------------------------------------------------------
    # Project Pool
    # ------------------------------------------------------------------

    def _start_queue(self) -> None:
        entries = self.store.list_pool_entries()
        queued = [e for e in entries if e.get("status") == "queued"]
        if not queued:
            QMessageBox.information(self, "项目池", "当前没有排队中的项目。")
            return
        self._queue_running = True
        self.start_queue_btn.setVisible(False)
        self.stop_queue_btn.setVisible(True)
        self._advance_queue()

    def _stop_queue(self) -> None:
        self._queue_running = False
        self.start_queue_btn.setVisible(True)
        self.stop_queue_btn.setVisible(False)
        active = self.store.get_active_pool_session()
        if active:
            self.store.update_pool_status(active["session_id"], "queued")
            self._active_pool_session_id = None
        self._refresh_pool()

    def _advance_queue(self) -> None:
        if not self._queue_running:
            return
        # Mark any finished active session as completed
        active = self.store.get_active_pool_session()
        if active:
            session = self.store.get_session(active["session_id"]) or {}
            status = str(session.get("status") or "")
            if status in {"completed", "failed", "cancelled"}:
                self.store.update_pool_status(active["session_id"], "completed")
                active = None
            elif status in {"running", "waiting_approval", "criteria_draft", "criteria_confirmed", "ready", "paused"}:
                # Still in progress; do nothing
                self._active_pool_session_id = active["session_id"]
                self._refresh_pool()
                return
            else:
                # Unknown or terminal state
                self.store.update_pool_status(active["session_id"], "completed")
                active = None

        self._active_pool_session_id = None
        next_entry = self.store.get_next_queued_session()
        if not next_entry:
            self._queue_running = False
            self.start_queue_btn.setVisible(True)
            self.stop_queue_btn.setVisible(False)
            QMessageBox.information(self, "项目池", "所有项目已处理完毕。")
            self._refresh_pool()
            return

        session_id = next_entry["session_id"]
        self.store.update_pool_status(session_id, "active")
        self._active_pool_session_id = session_id
        self._refresh_pool()

        session = self.store.get_session(session_id) or {}
        status = str(session.get("status") or "")

        if status in {"completed", "failed", "cancelled"}:
            self.store.update_pool_status(session_id, "completed")
            QTimer.singleShot(500, self._advance_queue)
            return

        if status == "criteria_draft":
            draft = self.store.get_latest_criteria_version(session_id, "draft")
            confirmed = self.store.get_latest_criteria_version(session_id, "confirmed")
            if confirmed:
                self.continue_session(session_id)
            elif draft:
                self._show_pool_notification(session_id, session.get("title") or "未命名")
            else:
                # No criteria yet; trigger generation and wait for event
                self.selected_session_id = session_id
                self._queue_criteria_draft(session_id)
        elif status in {"criteria_confirmed", "ready", "paused"}:
            self.continue_session(session_id)
        elif status == "running":
            # Already running; just let it proceed
            pass
        else:
            # Fallback
            self.continue_session(session_id)

    def _check_queue_advance(self) -> None:
        if not self._queue_running:
            return
        active_entry = self.store.get_active_pool_session()
        if not active_entry:
            QTimer.singleShot(500, self._advance_queue)
            return
        session_id = active_entry["session_id"]
        session = self.store.get_session(session_id) or {}
        status = str(session.get("status") or "")
        if status in {"completed", "failed", "cancelled"}:
            self.store.update_pool_status(session_id, "completed")
            QTimer.singleShot(800, self._advance_queue)

    def _show_pool_notification(self, session_id: str, title: str) -> None:
        if not self._queue_running:
            return
        dialog = PoolNotificationDialog(title, session_id, self)
        result = dialog.exec()
        if result == 100:
            self.selected_session_id = session_id
            self._select_session_in_list(session_id)
            self.refresh_all()

    def _on_pool_reordered(self, *args) -> None:
        ordered_ids = []
        for i in range(self.pool_list.count()):
            item = self.pool_list.item(i)
            session_id = str(item.data(Qt.UserRole) or "")
            if session_id:
                ordered_ids.append(session_id)
        if ordered_ids:
            self.store.reorder_pool(ordered_ids)
        self._refresh_pool()

    def _refresh_pool(self) -> None:
        entries = self.store.list_pool_entries()
        current_id = self._active_pool_session_id
        self.pool_list.blockSignals(True)
        self.pool_list.clear()
        for entry in entries:
            session_id = str(entry.get("session_id") or "")
            title = str(entry.get("title") or "未命名")
            status = str(entry.get("status") or "")
            session_status = str(entry.get("session_status") or "")
            label = "{} | {}".format(title, self._pool_status_label(status, session_status))
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, session_id)
            if status == "active":
                item.setBackground(Qt.GlobalColor.yellow)
            self.pool_list.addItem(item)
            if session_id == current_id:
                self.pool_list.setCurrentItem(item)
        self.pool_list.blockSignals(False)

    def _on_pool_item_selected(self, current: QListWidgetItem, _previous: QListWidgetItem) -> None:
        if current is None:
            return
        session_id = str(current.data(Qt.UserRole) or "")
        if session_id:
            self.selected_session_id = session_id
            self._select_session_in_list(session_id)
            self._mark_dirty()

    def _clear_completed_pool(self) -> None:
        count = self.store.clear_pool_by_status(["completed", "failed"])
        self._refresh_pool()
        if count:
            QMessageBox.information(self, "项目池", "已清理 {} 个已完成/失败的项目。".format(count))

    @staticmethod
    def _pool_status_label(pool_status: str, session_status: str) -> str:
        mapping = {
            "queued": "排队中",
            "active": "处理中",
            "completed": "已完成",
            "failed": "失败",
        }
        base = mapping.get(pool_status, pool_status)
        if pool_status == "active" and session_status:
            session_map = {
                "criteria_draft": "待确认基准",
                "criteria_confirmed": "已确认",
                "running": "运行中",
                "waiting_approval": "等待确认",
                "paused": "已暂停",
            }
            extra = session_map.get(session_status, session_status)
            return "{}（{}）".format(base, extra)
        return base

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        active_session_ids = [
            session_id
            for session_id in list(self.runtime._threads.keys())
            if self.runtime.is_active(session_id)
        ]
        if active_session_ids:
            reply = QMessageBox.question(
                self,
                "退出工作台",
                "当前仍有任务在运行。是否请求停止任务并退出？",
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            for session_id in active_session_ids:
                self.runtime.cancel_session(session_id)
        self.event_bus.unsubscribe(self._queue_runtime_event)
        try:
            self.runtime.liepin_tool.close()
        except Exception:
            pass
        self.runtime.browser_queue.shutdown()
        self.runtime.match_queue.shutdown()
        super().closeEvent(event)
