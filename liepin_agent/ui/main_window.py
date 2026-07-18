"""Main desktop workbench window."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from html import escape
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..agent.runtime import AgentRuntime
from ..agent.brain import LLMAgentBrain
from ..core.config import ConfigManager
from ..domain.job_profile import normalize_job_profile
from ..domain.greeting_policy import parse_recommendation_state
from ..domain.recommendation import RECOMMENDATION_LABELS, recommendation_label
from ..services.agent_chat import AgentChatService
from ..services.event_bus import EventBus
from ..storage.sqlite_store import SQLiteStore, from_json
from ..tools.exporter import ExportService
from ..tools.excel_greeting import ExcelGreetingService, GreetingQuotaTracker
from ..tools.real_liepin import RealLiepinTool
from ..tools.real_matcher import RealMatchService
from .dialogs import (
    BatchGreetingDialog,
    GreetingScopeDialog,
    NewSessionDialog,
    PoolNotificationDialog,
    SettingsDialog,
)
from .chat_bubbles import bubble_html
from .session_list_item import STATUS_LABELS, SessionListItemWidget
from .styles import MAIN_STYLESHEET


logger = logging.getLogger(__name__)


class _AgentChatSignals(QObject):
    replied = Signal(str, str)
    failed = Signal(str, str)


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
        self._pending_feedback_label = ""
        self._runtime_dirty = False
        self._force_refresh = False
        self._last_refresh_monotonic = 0.0
        self._last_heavy_refresh_monotonic = 0.0
        self._runtime_refresh_interval = 1.0
        self._heavy_refresh_interval = 5.0
        self._feedback_summary_snapshot: Dict[str, object] = {}
        self.chat_service = AgentChatService.from_config(
            self.config_manager, self.store
        )
        self._chat_signals = _AgentChatSignals(self)
        self._chat_signals.replied.connect(self._on_chat_reply)
        self._chat_signals.failed.connect(self._on_chat_failed)
        self._chat_busy = False

        # Cache for incremental session-list updates
        self._session_list_ids: List[str] = []
        self._session_rows_by_id: Dict[str, Dict[str, object]] = {}

        self.setWindowTitle("猎聘寻访 Agent 工作台")
        self._build_ui()
        self._connect_events()
        self.setStyleSheet(MAIN_STYLESHEET)

        # Runtime events may come from worker threads; poll a tiny queue on the
        # UI thread so Qt widgets are only touched from the main thread.
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500)
        self.refresh_timer.timeout.connect(self._refresh_if_dirty)
        self.refresh_timer.start()
        initial_refresh_started = time.monotonic()
        logger.info("Starting initial UI refresh")
        self.refresh_all()
        logger.info(
            "Initial UI refresh completed in %.3fs",
            time.monotonic() - initial_refresh_started,
        )

    def _build_runtime(self) -> AgentRuntime:
        return AgentRuntime(
            store=self.store,
            event_bus=self.event_bus,
            liepin_tool=RealLiepinTool(self.config_manager),
            matcher=RealMatchService.from_config(self.config_manager),
            agent_brain=LLMAgentBrain.from_config(self.config_manager),
            config=self.config_manager.config,
        )

    def _rebuild_runtime_tools(self) -> None:
        try:
            self.runtime.liepin_tool.close()
        except Exception:
            pass
        self.runtime.browser_queue.shutdown()
        self.runtime.match_queue.shutdown()
        self.runtime = self._build_runtime()
        self.chat_service = AgentChatService.from_config(
            self.config_manager, self.store
        )

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)
        root_layout.addWidget(self._build_top_bar())

        main_splitter = QSplitter(Qt.Horizontal)
        left_panel = self._build_left_panel()
        center_panel = self._build_center_panel()
        right_panel = self._build_right_panel()
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(center_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setStretchFactor(2, 0)
        main_splitter.setSizes([260, 930, 370])
        root_layout.addWidget(main_splitter, 1)
        self.setCentralWidget(root)

    def _build_top_bar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("TopBar")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(6)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self.title_label = QLabel("未选择任务")
        self.title_label.setObjectName("TitleLabel")

        self.stage_label = QLabel("就绪")
        self.stage_label.setObjectName("SessionInfo")

        self.greeting_progress = QProgressBar()
        self.greeting_progress.setVisible(False)
        self.greeting_progress.setMaximumWidth(180)
        self.greeting_progress.setFixedHeight(14)
        self.greeting_progress.setTextVisible(True)
        self.greeting_progress.setFormat("%v/%m")

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("SessionInfo")

        self.browser_label = QLabel("")
        self.browser_label.setObjectName("SessionInfo")

        status_row.addWidget(self.title_label)
        status_row.addWidget(self.stage_label)
        status_row.addWidget(self.greeting_progress)
        status_row.addStretch(1)
        status_row.addWidget(self.stats_label)
        status_row.addWidget(self.browser_label)
        layout.addLayout(status_row)

        command_row = QHBoxLayout()
        command_row.setSpacing(6)

        sep1 = QFrame()
        sep1.setObjectName("ToolbarSeparator")

        # 任务操作组
        self.new_btn = QPushButton("新建")
        self.add_to_pool_btn = QPushButton("加入池")
        self.add_to_pool_btn.setObjectName("SecondaryBtn")
        command_row.addWidget(self.new_btn)
        command_row.addWidget(self.add_to_pool_btn)
        command_row.addWidget(sep1)

        sep2 = QFrame()
        sep2.setObjectName("ToolbarSeparator")

        # 浏览器组
        self.open_liepin_btn = QPushButton("打开猎聘")
        self.open_liepin_btn.setObjectName("SuccessBtn")
        self.close_liepin_btn = QPushButton("关闭浏览器")
        self.close_liepin_btn.setObjectName("DangerBtn")
        command_row.addWidget(self.open_liepin_btn)
        command_row.addWidget(self.close_liepin_btn)
        command_row.addWidget(sep2)

        sep3 = QFrame()
        sep3.setObjectName("ToolbarSeparator")

        # 批量与设置组
        self.batch_greeting_btn = QPushButton("Excel 批量打招呼")
        self.batch_greeting_btn.setObjectName("SuccessBtn")
        self.cancel_greeting_btn = QPushButton("取消打招呼")
        self.cancel_greeting_btn.setObjectName("DangerBtn")
        self.cancel_greeting_btn.setVisible(False)
        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("SecondaryBtn")
        command_row.addWidget(self.batch_greeting_btn)
        command_row.addWidget(self.cancel_greeting_btn)
        command_row.addStretch(1)
        command_row.addWidget(sep3)
        command_row.addWidget(self.settings_btn)
        layout.addLayout(command_row)

        return frame

    def _build_left_panel(self) -> QWidget:
        container = QWidget()
        container.setMinimumWidth(250)
        container.setMaximumWidth(320)
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
        pool_title = QLabel("项目池")
        pool_title.setObjectName("SectionTitle")
        pool_header.addWidget(pool_title)
        pool_header.addStretch(1)
        self.start_queue_btn = QPushButton("开始队列")
        self.start_queue_btn.setObjectName("SuccessBtn")
        self.stop_queue_btn = QPushButton("停止队列")
        self.stop_queue_btn.setObjectName("DangerBtn")
        self.stop_queue_btn.setVisible(False)
        self.clear_completed_btn = QPushButton("清理")
        self.clear_completed_btn.setObjectName("SecondaryBtn")
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
        session_title = QLabel("寻访任务")
        session_title.setObjectName("SectionTitle")
        sessions_layout.addWidget(session_title)
        self.session_list = QListWidget()
        self.session_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sessions_layout.addWidget(self.session_list, 1)
        splitter.addWidget(sessions_frame)

        splitter.setSizes([180, 420])
        outer_layout.addWidget(splitter)
        return container

    def _build_center_panel(self) -> QWidget:
        splitter = QSplitter(Qt.Vertical)
        timeline_frame = QFrame()
        timeline_frame.setObjectName("Panel")
        timeline_layout = QVBoxLayout(timeline_frame)
        timeline_layout.setContentsMargins(8, 8, 8, 8)
        timeline_title = QLabel("运行记录")
        timeline_title.setObjectName("SectionTitle")
        timeline_layout.addWidget(timeline_title)
        self.timeline = QTextBrowser()
        self.timeline.setOpenExternalLinks(False)
        timeline_layout.addWidget(self.timeline, 1)

        # 对话栏：随时打断 Agent 并与其沟通，消息与回复渲染进运行记录
        self.chat_hint_label = QLabel("选择任务后，可随时给 Agent 发消息。")
        self.chat_hint_label.setObjectName("HintLabel")
        timeline_layout.addWidget(self.chat_hint_label)
        chat_row = QHBoxLayout()
        chat_row.setSpacing(6)
        self.chat_input = QTextEdit()
        self.chat_input.setPlaceholderText(
            "向 Agent 说明你的想法（Ctrl+Enter 发送）；运行中的任务会被打断进入对话"
        )
        self.chat_input.setFixedHeight(52)
        self.chat_input.installEventFilter(self)
        chat_row.addWidget(self.chat_input, 1)
        self.chat_send_btn = QPushButton("发送")
        self.chat_send_btn.setObjectName("AccentBtn")
        self.chat_send_btn.setFixedWidth(72)
        self.end_dialog_btn = QPushButton("继续执行")
        self.end_dialog_btn.setObjectName("SuccessBtn")
        self.end_dialog_btn.setToolTip("结束对话，Agent 继续工作并采纳沟通结论")
        self.end_dialog_btn.setVisible(False)
        chat_row.addWidget(self.chat_send_btn, 0, Qt.AlignBottom)
        chat_row.addWidget(self.end_dialog_btn, 0, Qt.AlignBottom)
        timeline_layout.addLayout(chat_row)

        table_frame = QFrame()
        table_frame.setObjectName("Panel")
        table_layout = QVBoxLayout(table_frame)
        table_layout.setContentsMargins(8, 8, 8, 8)
        self.candidate_table_title = QLabel("候选人详情")
        self.candidate_table_title.setObjectName("SectionTitle")
        candidate_table_header = QHBoxLayout()
        candidate_table_header.addWidget(self.candidate_table_title)
        candidate_table_header.addStretch(1)
        self.select_greeting_scope_btn = QPushButton("按资格选择")
        self.select_greeting_scope_btn.setObjectName("SecondaryBtn")
        self.select_greeting_scope_btn.setToolTip("按建议状态批量选中候选人，之后仍可逐人取消。")
        candidate_table_header.addWidget(self.select_greeting_scope_btn)
        table_layout.addLayout(candidate_table_header)
        self.candidate_table = QTableWidget(0, 17)
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
                "建议状态",
                "已知适配",
                "潜在上界",
                "证据覆盖",
                "综合排序",
                "人工判断",
                "打招呼",
                "状态",
                "摘要",
            ]
        )
        self.candidate_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.candidate_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.candidate_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.candidate_table.verticalHeader().setVisible(False)
        self.candidate_table.setAlternatingRowColors(True)
        candidate_header = self.candidate_table.horizontalHeader()
        candidate_header.setSectionResizeMode(QHeaderView.Interactive)
        candidate_header.setStretchLastSection(False)
        candidate_header.setMinimumSectionSize(42)
        self.candidate_table.verticalHeader().setDefaultSectionSize(28)
        self.candidate_table.setColumnWidth(0, 90)
        self.candidate_table.setColumnWidth(3, 58)
        self.candidate_table.setColumnWidth(4, 52)
        self.candidate_table.setColumnWidth(7, 80)
        self.candidate_table.setColumnWidth(8, 96)
        self.candidate_table.setColumnWidth(9, 68)
        self.candidate_table.setColumnWidth(10, 68)
        self.candidate_table.setColumnWidth(11, 68)
        self.candidate_table.setColumnWidth(12, 68)
        self.candidate_table.setColumnWidth(13, 76)
        self.candidate_table.setColumnWidth(14, 72)
        candidate_header.setSectionResizeMode(1, QHeaderView.Stretch)
        candidate_header.setSectionResizeMode(2, QHeaderView.Stretch)
        for hidden_column in (4, 5, 6, 7, 15, 16):
            self.candidate_table.setColumnHidden(hidden_column, True)
        self._candidate_table_initialized = False
        table_layout.addWidget(self.candidate_table, 1)

        splitter.addWidget(table_frame)
        splitter.addWidget(timeline_frame)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([620, 180])
        return splitter

    def _build_feedback_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        title = QLabel("人工判断")
        title.setObjectName("FormSectionLabel")
        layout.addWidget(title)

        decision_row = QHBoxLayout()
        decision_row.setSpacing(6)
        self.feedback_button_group = QButtonGroup(self)
        self.feedback_button_group.setExclusive(True)
        self.feedback_buttons: Dict[str, QPushButton] = {}
        for value, text, object_name in (
            ("recommended", "推荐", "SuccessBtn"),
            ("uncertain", "待确认", "SecondaryBtn"),
            ("not_suitable", "不合适", "DangerBtn"),
        ):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setObjectName(object_name)
            button.clicked.connect(
                lambda _checked=False, selected=value: self._select_feedback_label(selected)
            )
            self.feedback_button_group.addButton(button)
            self.feedback_buttons[value] = button
            decision_row.addWidget(button)
        layout.addLayout(decision_row)

        self.feedback_reason_combo = QComboBox()
        self.feedback_reason_combo.addItem("选择原因", "")
        for reason in (
            "核心经验不足",
            "行业不匹配",
            "职位层级不匹配",
            "地点不匹配",
            "薪资不匹配",
            "信息不足",
            "其他",
        ):
            self.feedback_reason_combo.addItem(reason, reason)
        layout.addWidget(self.feedback_reason_combo)

        self.feedback_note_input = QLineEdit()
        self.feedback_note_input.setPlaceholderText("补充判断依据")
        layout.addWidget(self.feedback_note_input)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.save_feedback_btn = QPushButton("保存判断")
        self.pairwise_feedback_btn = QPushButton("首行优先")
        self.pairwise_feedback_btn.setToolTip("选择两位候选人后，记录表格中靠前者更优")
        self.pairwise_feedback_btn.setEnabled(False)
        action_row.addWidget(self.save_feedback_btn)
        action_row.addWidget(self.pairwise_feedback_btn)
        layout.addLayout(action_row)
        return panel

    def _build_right_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("Panel")
        frame.setMinimumWidth(330)
        frame.setMaximumWidth(430)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)

        inspector_title = QLabel("工作检查器")
        inspector_title.setObjectName("SectionTitle")
        layout.addWidget(inspector_title)

        self.right_tabs = QTabWidget()
        self.right_tabs.setDocumentMode(True)

        profile_widget = QWidget()
        profile_layout = QVBoxLayout(profile_widget)
        profile_layout.setContentsMargins(8, 8, 8, 8)
        profile_layout.setSpacing(8)

        criteria_title = QLabel("岗位匹配要求")
        criteria_title.setObjectName("FormSectionLabel")
        profile_layout.addWidget(criteria_title)
        self.criteria_requirements_input = QTextEdit()
        self.criteria_requirements_input.setPlaceholderText(
            "用一段话描述本岗位最关键的匹配要求，例如：\n"
            "需要5年以上无刷电机设计经验，熟悉FOC控制算法，有小家电或新能源汽车行业背景优先。"
        )
        self.criteria_requirements_input.setMaximumHeight(140)
        profile_layout.addWidget(self.criteria_requirements_input)

        direction_title = QLabel("寻访方向（AI 对岗位的理解，可直接编辑修正）")
        direction_title.setObjectName("HintLabel")
        profile_layout.addWidget(direction_title)
        self.search_direction_input = QLineEdit()
        self.search_direction_input.setPlaceholderText("AI 生成草案后显示对岗位的理解方向")
        self.search_direction_input.setEnabled(False)
        profile_layout.addWidget(self.search_direction_input)

        self.profile_tabs = QTabWidget()
        self.profile_tabs.setDocumentMode(True)

        criteria_widget = QWidget()
        criteria_layout = QVBoxLayout(criteria_widget)
        criteria_layout.setContentsMargins(2, 2, 2, 2)
        criteria_actions = QHBoxLayout()
        criteria_actions.addStretch(1)
        self.add_criterion_btn = QPushButton("+")
        self.add_criterion_btn.setToolTip("新增岗位条件")
        self.remove_criterion_btn = QPushButton("-")
        self.remove_criterion_btn.setToolTip("删除选中的岗位条件")
        criteria_actions.addWidget(self.add_criterion_btn)
        criteria_actions.addWidget(self.remove_criterion_btn)
        criteria_layout.addLayout(criteria_actions)
        self.criteria_items_table = QTableWidget(0, 8)
        self.criteria_items_table.setHorizontalHeaderLabels(
            ["类型", "条件", "权重", "替代条件", "搜索词", "年限窗", "证据渠道", "证据要求"]
        )
        self.criteria_items_table.verticalHeader().setVisible(False)
        self.criteria_items_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.criteria_items_table.horizontalHeader().setStretchLastSection(True)
        self.criteria_items_table.setColumnWidth(0, 72)
        self.criteria_items_table.setColumnWidth(1, 180)
        self.criteria_items_table.setColumnWidth(2, 50)
        criteria_layout.addWidget(self.criteria_items_table)
        self.profile_tabs.addTab(criteria_widget, "结构化条件")

        personas_widget = QWidget()
        personas_layout = QVBoxLayout(personas_widget)
        personas_layout.setContentsMargins(2, 2, 2, 2)
        persona_actions = QHBoxLayout()
        persona_actions.addStretch(1)
        self.add_persona_btn = QPushButton("+")
        self.add_persona_btn.setToolTip("新增人才原型")
        self.remove_persona_btn = QPushButton("-")
        self.remove_persona_btn.setToolTip("删除选中的人才原型")
        persona_actions.addWidget(self.add_persona_btn)
        persona_actions.addWidget(self.remove_persona_btn)
        personas_layout.addLayout(persona_actions)
        self.personas_table = QTableWidget(0, 5)
        self.personas_table.setHorizontalHeaderLabels(
            ["名称", "描述", "职位/技能", "公司类型", "优先级"]
        )
        self.personas_table.verticalHeader().setVisible(False)
        self.personas_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.personas_table.horizontalHeader().setStretchLastSection(True)
        self.personas_table.setColumnWidth(0, 90)
        self.personas_table.setColumnWidth(1, 180)
        personas_layout.addWidget(self.personas_table)
        self.profile_tabs.addTab(personas_widget, "人才原型")
        profile_layout.addWidget(self.profile_tabs, 1)

        criteria_buttons = QHBoxLayout()
        self.regenerate_criteria_btn = QPushButton("重新生成草案")
        self.regenerate_criteria_btn.setObjectName("SecondaryBtn")
        self.confirm_criteria_btn = QPushButton("确认寻访基准")
        self.confirm_and_start_btn = QPushButton("确认并开始")
        self.confirm_and_start_btn.setObjectName("SuccessBtn")
        criteria_buttons.addWidget(self.regenerate_criteria_btn)
        criteria_buttons.addWidget(self.confirm_criteria_btn)
        criteria_buttons.addWidget(self.confirm_and_start_btn)
        profile_layout.addLayout(criteria_buttons)

        self.profile_tab_index = self.right_tabs.addTab(profile_widget, "画像")

        strategy_widget = QWidget()
        strategy_layout = QVBoxLayout(strategy_widget)
        strategy_layout.setContentsMargins(8, 8, 8, 8)
        self.strategy_view = QTextBrowser()
        self.strategy_view.setMinimumHeight(120)
        strategy_layout.addWidget(self.strategy_view)
        self.right_tabs.addTab(strategy_widget, "策略")

        coverage_widget = QWidget()
        coverage_layout = QVBoxLayout(coverage_widget)
        coverage_layout.setContentsMargins(8, 8, 8, 8)
        coverage_primary_actions = QHBoxLayout()
        self.pause_hypothesis_btn = QPushButton("暂停方向")
        self.resume_hypothesis_btn = QPushButton("恢复方向")
        self.raise_hypothesis_btn = QPushButton("提高优先级")
        self.lower_hypothesis_btn = QPushButton("降低优先级")
        self.pause_hypothesis_btn.setObjectName("SecondaryBtn")
        coverage_primary_actions.addWidget(self.pause_hypothesis_btn)
        coverage_primary_actions.addWidget(self.resume_hypothesis_btn)
        coverage_layout.addLayout(coverage_primary_actions)

        coverage_priority_actions = QHBoxLayout()
        coverage_priority_actions.addWidget(self.raise_hypothesis_btn)
        coverage_priority_actions.addWidget(self.lower_hypothesis_btn)
        coverage_layout.addLayout(coverage_priority_actions)

        self.coverage_summary_label = QLabel("")
        self.coverage_summary_label.setObjectName("HintLabel")
        coverage_layout.addWidget(self.coverage_summary_label)
        self.coverage_table = QTableWidget(0, 8)
        self.coverage_table.setHorizontalHeaderLabels(
            ["方向", "搜索词", "状态", "次数", "新增", "有效", "重复率", "优先级"]
        )
        self.coverage_table.verticalHeader().setVisible(False)
        self.coverage_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.coverage_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.coverage_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.coverage_table.horizontalHeader().setStretchLastSection(True)
        coverage_layout.addWidget(self.coverage_table)
        self.right_tabs.addTab(coverage_widget, "覆盖")

        quality_widget = QWidget()
        quality_layout = QVBoxLayout(quality_widget)
        quality_layout.setContentsMargins(8, 8, 8, 8)
        quality_header = QHBoxLayout()
        self.refresh_ranking_btn = QPushButton("刷新排序")
        self.refresh_ranking_btn.setObjectName("SecondaryBtn")
        quality_header.addWidget(self.refresh_ranking_btn)
        quality_header.addStretch(1)
        quality_layout.addLayout(quality_header)
        self.quality_view = QTextBrowser()
        quality_layout.addWidget(self.quality_view)
        self.right_tabs.addTab(quality_widget, "质量")

        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(8, 8, 8, 8)
        outcome_row = QHBoxLayout()
        self.outcome_combo = QComboBox()
        self.outcome_combo.addItem("业务结果", "")
        for text, value in (
            ("已打招呼", "greeted"),
            ("已回复", "replied"),
            ("已约面", "interview"),
            ("未通过", "rejected"),
            ("已录用", "hired"),
            ("未回复", "no_response"),
        ):
            self.outcome_combo.addItem(text, value)
        self.save_outcome_btn = QPushButton("记录结果")
        self.outcome_combo.setEnabled(False)
        self.save_outcome_btn.setEnabled(False)
        outcome_row.addWidget(self.outcome_combo, 1)
        outcome_row.addWidget(self.save_outcome_btn)
        detail_layout.addLayout(outcome_row)

        candidate_action_row = QHBoxLayout()
        self.reevaluate_btn = QPushButton("重新评估")
        self.reevaluate_btn.setObjectName("SecondaryBtn")
        self.reevaluate_btn.setEnabled(False)
        candidate_action_row.addWidget(self.reevaluate_btn)
        self.manual_greeting_btn = QPushButton("手动打招呼")
        self.manual_greeting_btn.setEnabled(False)
        candidate_action_row.addWidget(self.manual_greeting_btn)
        detail_layout.addLayout(candidate_action_row)
        self.detail_view = QTextBrowser()
        detail_layout.addWidget(self.detail_view, 1)
        detail_layout.addWidget(self._build_feedback_panel())
        self.detail_tab_index = self.right_tabs.addTab(detail_widget, "候选人")

        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(8, 8, 8, 8)
        self.log_view = QTextBrowser()
        log_layout.addWidget(self.log_view, 1)
        self.right_tabs.addTab(log_widget, "日志")

        layout.addWidget(self.right_tabs, 1)
        return frame

    def _connect_events(self) -> None:
        self.new_btn.clicked.connect(self.create_session)
        self.add_to_pool_btn.clicked.connect(self.create_session_and_add_to_pool)
        self.open_liepin_btn.clicked.connect(self.open_liepin_browser)
        self.close_liepin_btn.clicked.connect(self.close_liepin_browser)
        self.batch_greeting_btn.clicked.connect(self.open_batch_greeting_dialog)
        self.cancel_greeting_btn.clicked.connect(self._cancel_batch_greeting)
        self.settings_btn.clicked.connect(self.open_settings)
        self.start_queue_btn.clicked.connect(self._start_queue)
        self.stop_queue_btn.clicked.connect(self._stop_queue)
        self.clear_completed_btn.clicked.connect(self._clear_completed_pool)
        self.pool_list.currentItemChanged.connect(self._on_pool_item_selected)
        self.manual_greeting_btn.clicked.connect(self.greet_selected_candidate)
        self.select_greeting_scope_btn.clicked.connect(self._select_candidates_by_greeting_scope)
        self.reevaluate_btn.clicked.connect(self._reevaluate_selected_candidates)
        self.regenerate_criteria_btn.clicked.connect(self.regenerate_criteria_draft)
        self.confirm_criteria_btn.clicked.connect(self.confirm_current_criteria)
        self.confirm_and_start_btn.clicked.connect(self.confirm_criteria_and_start)
        self.criteria_requirements_input.textChanged.connect(self._mark_criteria_dirty)
        self.criteria_items_table.itemChanged.connect(self._mark_criteria_dirty)
        self.personas_table.itemChanged.connect(self._mark_criteria_dirty)
        self.add_criterion_btn.clicked.connect(self._add_criterion_row)
        self.remove_criterion_btn.clicked.connect(self._remove_criterion_row)
        self.add_persona_btn.clicked.connect(self._add_persona_row)
        self.remove_persona_btn.clicked.connect(self._remove_persona_row)
        self.pause_hypothesis_btn.clicked.connect(
            lambda: self._set_selected_hypothesis_status("paused")
        )
        self.resume_hypothesis_btn.clicked.connect(
            lambda: self._set_selected_hypothesis_status("pending")
        )
        self.raise_hypothesis_btn.clicked.connect(
            lambda: self._adjust_selected_hypothesis_priority(0.1)
        )
        self.lower_hypothesis_btn.clicked.connect(
            lambda: self._adjust_selected_hypothesis_priority(-0.1)
        )
        self.refresh_ranking_btn.clicked.connect(self._refresh_candidate_ranking)
        self.session_list.currentItemChanged.connect(self._on_session_changed)
        self.candidate_table.itemSelectionChanged.connect(self._on_candidate_selected)
        self.chat_send_btn.clicked.connect(self._send_chat_message)
        self.end_dialog_btn.clicked.connect(self._end_dialog_and_resume)
        self.save_feedback_btn.clicked.connect(self._save_candidate_feedback)
        self.pairwise_feedback_btn.clicked.connect(self._save_pairwise_feedback)
        self.save_outcome_btn.clicked.connect(self._save_candidate_outcome)
        self.event_bus.subscribe(self._queue_runtime_event)

    def _apply_style(self) -> None:
        self.setStyleSheet(MAIN_STYLESHEET)

    def _create_session_from_dialog(self, add_to_pool: bool = False) -> Optional[str]:
        dialog = NewSessionDialog(self.config_manager, self)
        if dialog.exec() != QDialog.Accepted:
            return None
        payload = dialog.payload()
        session_id = self.store.create_session(
            title=str(payload["title"]),
            jd_text=str(payload["jd_text"]),
            user_notes=str(payload["user_notes"]),
            mode=str(payload["mode"]),
            max_rounds=int(payload["max_rounds"]),
            max_detail_fetches=int(payload["max_detail_fetches"]),
            # SQLite retains the historical column name for migration safety.
            target_ab_count=int(payload["target_effective_count"]),
        )
        if add_to_pool:
            self.store.add_session_to_pool(session_id)
        self.selected_session_id = session_id
        self.refresh_all()
        self._select_session_in_list(session_id)
        self._queue_criteria_draft(session_id)
        return session_id

    def create_session(self) -> None:
        self._create_session_from_dialog(add_to_pool=False)

    def create_session_and_add_to_pool(self) -> None:
        self._create_session_from_dialog(add_to_pool=True)

    def start_selected_session(self) -> None:
        if not self.selected_session_id:
            QMessageBox.information(self, "提示", "请先选择或新建任务")
            return
        self.continue_session(self.selected_session_id)

    def toggle_session_run(self, session_id: str) -> None:
        session = self.store.get_session(session_id) or {}
        if str(session.get("status") or "") == "user_dialog":
            # 对话状态下"继续"= 结束对话并恢复执行
            self.selected_session_id = session_id
            self._end_dialog_and_resume()
            return
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
                '请先确认右侧的"岗位匹配要求"，确认后 Agent 才会开始搜索。',
            )
            self._mark_dirty()
            return
        if self._criteria_dirty:
            QMessageBox.information(
                self,
                "基准有未确认修改",
                '你修改了"岗位匹配要求"，请先点击"确认寻访基准"。',
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

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        if obj is self.chat_input and event.type() == QEvent.Type.KeyPress:
            if (
                event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() & Qt.ControlModifier
            ):
                self._send_chat_message()
                return True
        return super().eventFilter(obj, event)

    def _send_chat_message(self) -> None:
        text = self.chat_input.toPlainText().strip()
        session_id = self.selected_session_id
        if not text or not session_id or self._chat_busy:
            return
        session = self.store.get_session(session_id) or {}
        status = str(session.get("status") or "")
        if status in {"completed", "cancelled", "failed"}:
            return
        self.store.add_event(session_id, None, "user_message", "我", text, {})
        self.chat_input.clear()
        interrupted = False
        if status == "running" and self.runtime.is_active(session_id):
            interrupted = self.runtime.interrupt_for_dialog(session_id)
        if interrupted:
            self.store.add_event(
                session_id,
                None,
                "dialog_interrupted",
                "已进入对话",
                "收到你的消息，Agent 会在当前操作完成后暂停，沟通好后点击「继续执行」。",
                {},
            )
        self._mark_dirty()

        self._chat_busy = True
        self.chat_send_btn.setEnabled(False)
        self.chat_send_btn.setText("思考中...")
        history = self.chat_service.load_history(session_id)

        def _run() -> None:
            try:
                reply = self.chat_service.reply(session_id, history)
                self._chat_signals.replied.emit(session_id, reply)
            except Exception as exc:  # reply() 内部已有兜底，这里是双保险
                self._chat_signals.failed.emit(session_id, str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _on_chat_reply(self, session_id: str, reply: str) -> None:
        self.store.add_event(session_id, None, "agent_reply", "寻访 Agent", reply, {})
        self._chat_busy = False
        self.chat_send_btn.setText("发送")
        self._mark_dirty()

    def _on_chat_failed(self, session_id: str, error: str) -> None:
        self.store.add_event(
            session_id,
            None,
            "agent_reply",
            "寻访 Agent",
            "（回复失败：{}；你的消息已记录，继续执行后会生效）".format(error or "未知错误"),
            {},
        )
        self._chat_busy = False
        self.chat_send_btn.setText("发送")
        self._mark_dirty()

    def _end_dialog_and_resume(self) -> None:
        session_id = self.selected_session_id
        if not session_id:
            return
        session = self.store.get_session(session_id) or {}
        status = str(session.get("status") or "")

        # 收集本轮对话中用户的消息作为结论：自上一个 dialog_resumed 以来的 user_message
        events = self.store.list_events(session_id)
        boundary = 0
        for index, event in enumerate(events):
            if str(event.get("event_type") or "") == "dialog_resumed":
                boundary = index + 1
        user_turns = [
            str(event.get("message") or "").strip()
            for event in events[boundary:]
            if str(event.get("event_type") or "") == "user_message"
            and str(event.get("message") or "").strip()
        ]
        if user_turns:
            self.store.set_pending_user_command(session_id, "\n".join(user_turns))

        if status == "user_dialog":
            self.runtime.end_dialog(session_id)
        self.store.add_event(
            session_id,
            None,
            "dialog_resumed",
            "继续执行",
            "对话结束，沟通结论将在本轮结束后纳入下一轮搜索计划。"
            if user_turns
            else "对话结束，继续执行当前任务。",
            {},
        )
        self._mark_dirty()

    def _update_chat_bar_state(self) -> None:
        session_id = self.selected_session_id
        session: Dict[str, object] = {}
        if session_id:
            session = (
                self._session_rows_by_id.get(session_id)
                or self.store.get_session(session_id)
                or {}
            )
        status = str(session.get("status") or "")
        terminal = not session_id or status in {"completed", "cancelled", "failed"}
        in_dialog = status == "user_dialog"
        self.chat_input.setEnabled(not terminal)
        self.chat_send_btn.setEnabled(not terminal and not self._chat_busy)
        self.end_dialog_btn.setVisible(in_dialog)
        if not session_id:
            hint = "选择任务后，可随时给 Agent 发消息。"
        elif terminal:
            hint = "任务已结束，无法继续对话。"
        elif in_dialog:
            hint = "对话中：Agent 已暂停。沟通好后点击「继续执行」，结论将用于调整下一轮搜索。"
        elif status == "running":
            hint = "发送消息会打断 Agent 进入对话（Ctrl+Enter 发送）。"
        else:
            hint = "可随时给 Agent 留言（Ctrl+Enter 发送）。"
        self.chat_hint_label.setText(hint)

    def export_session(self, session_id: str) -> None:
        exporter = ExportService(self.store, self.workspace_root / "exports")
        path = exporter.export_session(session_id)
        message = "Excel 总览：\n{}".format(path)
        if exporter.last_candidate_reports_dir:
            message += "\n\n候选人 Word 档案：\n{}".format(
                exporter.last_candidate_reports_dir
            )
        QMessageBox.information(self, "导出完成", message)

    def open_batch_greeting_dialog(self) -> None:
        if self.selected_session_id and self.runtime.is_active(self.selected_session_id):
            QMessageBox.information(
                self,
                "任务运行中",
                "请先暂停或等待当前任务结束后，再执行 Excel 批量打招呼。",
            )
            return
        dialog = BatchGreetingDialog(self.config_manager, self.workspace_root, self)
        if self.selected_session_id:
            session = self.store.get_session(self.selected_session_id) or {}
            dialog.set_job_defaults(
                str(session.get("title") or ""),
                str(session.get("jd_text") or ""),
            )
        if dialog.exec() != QDialog.Accepted:
            return
        payload = dialog.payload()
        count = int(payload.get("candidate_count") or 0)
        names = "、".join((payload.get("candidate_names") or [])[:8])
        if count > 8:
            names += " 等"
        dry_run = bool(payload.get("dry_run"))
        verify_gold_on_page = bool(payload.get("verify_gold_on_page"))
        request_resume = bool(payload.get("request_resume"))
        gold_only = bool(payload.get("gold_only"))
        delay_min = float(payload.get("delay_min") or 2.0)
        delay_max = float(payload.get("delay_max") or 5.0)
        max_retries = int(payload.get("max_retries") or 1)
        max_candidates = int(payload.get("max_candidates") or 0)
        recommendation_states = list(payload.get("recommendation_states") or [])
        excel_path = str(payload.get("excel_path") or "")
        if excel_path:
            self.config_manager.update(last_greeting_excel_path=excel_path)
            self.config_manager.save_config()
        if not dry_run:
            quota_tracker = GreetingQuotaTracker(self.workspace_root)
            today_count = quota_tracker.today_count()
            warn_limit = self.config_manager.config.greet_daily_quota_warn
            if warn_limit > 0 and today_count + count > warn_limit:
                reply = QMessageBox.warning(
                    self,
                    "额度提醒",
                    "今日已打招呼 {} 人，本次将再处理 {} 人，合计将超过 {} 人的预警阈值。\n\n是否继续？".format(
                        today_count, count, warn_limit,
                    ),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
        action_label = "预览 dry-run（不会实际发送）" if dry_run else "实际发送打招呼"
        gold_label = "发送前会重新打开页面复核金领状态" if verify_gold_on_page else "将信任 Excel 金领字段，不做页面复核"
        resume_label = "同时索要简历" if request_resume else "不索要简历"
        scope_label = "、".join(
            RECOMMENDATION_LABELS.get(state, state) for state in recommendation_states
        )
        if gold_only:
            scope_label += " + 金领"
        limit_label = "全部（{} 人）".format(count) if max_candidates == 0 else "前 {} 人（按建议状态优先）".format(count)
        reply = QMessageBox.question(
            self,
            "确认批量打招呼",
            "即将处理 Excel 中 {} 候选人。\n\n模式：{}\n安全复核：{}\n索要简历：{}\n处理人数：{}\n文件：{}\n候选人：{}\n\n是否继续？".format(
                scope_label,
                action_label,
                gold_label,
                resume_label,
                limit_label,
                payload.get("excel_path") or "",
                names or "-",
            ),
        )
        if reply != QMessageBox.Yes:
            return
        self._start_excel_batch_greeting(
            str(payload.get("excel_path") or ""),
            str(payload.get("message") or ""),
            dry_run=dry_run,
            verify_gold_on_page=verify_gold_on_page,
            request_resume=request_resume,
            gold_only=gold_only,
            delay_min=delay_min,
            delay_max=delay_max,
            max_retries=max_retries,
            max_candidates=max_candidates,
            recommendation_states=recommendation_states,
        )

    def _start_excel_batch_greeting(
        self,
        excel_path: str,
        message: str,
        dry_run: bool = False,
        verify_gold_on_page: bool = True,
        request_resume: bool = False,
        gold_only: bool = True,
        delay_min: float = 2.0,
        delay_max: float = 5.0,
        max_retries: int = 1,
        max_candidates: int = 0,
        recommendation_states: Optional[List[str]] = None,
    ) -> None:
        self.batch_greeting_btn.setEnabled(False)
        self.batch_greeting_btn.setText("预览中..." if dry_run else "打招呼中...")
        self.cancel_greeting_btn.setVisible(True)
        self.greeting_progress.setVisible(True)
        self.greeting_progress.setValue(0)
        self.stage_label.setText("Excel 批量打招呼预览中" if dry_run else "Excel 批量打招呼进行中")

        service = ExcelGreetingService(self.runtime.liepin_tool)
        self._active_greeting_service = service

        def _run():
            try:
                results = service.greet_from_excel(
                    excel_path,
                    message_template=message,
                    delay_min=delay_min,
                    delay_max=delay_max,
                    dry_run=dry_run,
                    verify_gold_on_page=verify_gold_on_page,
                    request_resume=request_resume,
                    gold_only=gold_only,
                    max_retries=max_retries,
                    max_candidates=max_candidates,
                    recommendation_states=recommendation_states,
                    progress_callback=lambda current, total, name: self.event_bus.publish(
                        "excel_greeting_progress",
                        {"current": current, "total": total, "name": name},
                    ),
                )
                if not dry_run:
                    success_count = sum(1 for r in results if r.get("status") == "success")
                    if success_count > 0:
                        GreetingQuotaTracker(self.workspace_root).increment(success_count)
                summary = ExcelGreetingService.generate_summary(results, cancelled=service.is_stopped)
                self.event_bus.publish(
                    "excel_greeting_done",
                    {"summary": summary, "excel_path": excel_path},
                )
            except Exception as exc:
                self.event_bus.publish("excel_greeting_error", {"error": str(exc)})

        threading.Thread(target=_run, daemon=True).start()

    def _cancel_batch_greeting(self) -> None:
        service = getattr(self, "_active_greeting_service", None)
        if service:
            service.request_stop()
        self.cancel_greeting_btn.setEnabled(False)
        self.cancel_greeting_btn.setText("正在取消...")
        self.stage_label.setText("正在取消批量打招呼...")

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
        session = self.store.get_session(session_id) or {}
        list_rows = {str(item.get("id") or ""): item for item in self.store.list_candidates(session_id)}
        candidates_by_id = {str(item.get("id") or ""): dict(item) for item in candidates}
        # Batch-fetch all details to avoid N+1 queries
        all_details = {
            cid: (self.store.get_candidate_detail(cid) or {})
            for cid in candidate_ids
        }
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
            detail = all_details.get(candidate_id) or {}
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
                skipped.append("{}：已打过招呼".format(name))
                continue
            if self._resume_has_contact_info(str(detail.get("resume_text") or "")):
                contact_present.append(name)
                skipped.append("{}：简历详情中已出现联系方式".format(name))
                continue
            if self.config_manager.config.greet_gold_only:
                is_gold = int(detail.get("is_gold_collar") or 0) == 1
                if not is_gold:
                    skipped.append("{}：非金领候选人".format(name))
                    continue
            candidate["profile_url"] = profile_url
            candidate["session_title"] = str(session.get("title") or "")
            candidate.update(self._greeting_context_from_row(list_rows.get(candidate_id) or {}))
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
        template = str(self.config_manager.config.greeting_template or "")
        preview_message = ""
        if template and targets:
            preview_message = self.runtime.liepin_tool._render_greeting_template(
                template, dict(targets[0]["candidate"])
            )
        final_confirm_lines = [
            "将按队列依次为 {} 位候选人打开详情页并手动打招呼。".format(len(targets))
        ]
        if template:
            final_confirm_lines.append("话术预览：{}".format(preview_message or template))
        else:
            final_confirm_lines.append("话术：使用平台默认打招呼。")
        final_confirm_lines.append(
            "候选人：{}".format(
                "；".join(
                    str(item["candidate"].get("name") or "候选人") for item in targets[:6]
                )
            )
        )
        if confirm_lines:
            final_confirm_lines.append("\n".join(confirm_lines))
        reply = QMessageBox.question(
            self,
            "确认打招呼",
            "\n".join(final_confirm_lines),
        )
        if reply != QMessageBox.Yes:
            return
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

    @staticmethod
    def _greeting_context_from_row(row: Dict[str, object]) -> Dict[str, object]:
        return {
            "matched_evidence": row.get("matched_evidence") or [],
            "questions_to_verify": row.get("questions_to_verify") or [],
            "match_risks": row.get("match_risks") or "",
        }

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
        active_session_ids = self.runtime.active_session_ids()
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
        active_session_ids = self.runtime.active_session_ids()
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
        self.regenerate_criteria_btn.setEnabled(False)
        self.regenerate_criteria_btn.setText("生成中...")
        self._queue_criteria_draft(self.selected_session_id)
        self._mark_dirty()

    def confirm_current_criteria(self) -> None:
        if not self.selected_session_id:
            return
        criteria = self.store.get_latest_criteria_version(self.selected_session_id)
        requirements = self.criteria_requirements_input.toPlainText().strip()
        if not requirements:
            QMessageBox.warning(self, "提示", "请先填写岗位匹配要求描述。")
            return
        # 获取用户编辑后的寻访方向
        selected_direction = self.search_direction_input.text().strip()
        criteria_items = self._criteria_items_from_editor()
        personas = self._personas_from_editor()
        # 继承并更新 ai_raw_response
        ai_raw = {}
        if criteria and criteria.get("ai_raw_response"):
            raw = criteria["ai_raw_response"]
            ai_raw = raw if isinstance(raw, dict) else {}
        if selected_direction:
            ai_raw["selected_direction"] = selected_direction
        # keywords_text is kept for compatibility but left empty
        keywords = ""
        session = self.store.get_session(self.selected_session_id) or {}
        if criteria:
            if str(criteria.get("status") or "") == "confirmed":
                criteria_id = self.store.create_criteria_version(
                    self.selected_session_id,
                    keywords,
                    requirements,
                    source_jd_text=str(session.get("jd_text") or ""),
                    source_user_notes=str(session.get("user_notes") or ""),
                    ai_raw_response=ai_raw,
                    created_by="human",
                )
                self.store.confirm_criteria_version(criteria_id)
            else:
                criteria_id = str(criteria["id"])
                self.store.update_criteria_version(
                    criteria_id, keywords, requirements, status="draft"
                )
                # 同步更新 ai_raw_response_json
                try:
                    from ..storage.sqlite_store import SQLiteStore
                    if isinstance(self.store, SQLiteStore):
                        with self.store.connect() as conn:
                            conn.execute(
                                "UPDATE match_criteria_versions SET ai_raw_response_json = ? WHERE id = ?",
                                (json.dumps(ai_raw, ensure_ascii=False), criteria_id),
                            )
                except Exception:
                    pass
                self.store.confirm_criteria_version(criteria_id)
        else:
            criteria_id = self.store.create_criteria_version(
                self.selected_session_id,
                keywords,
                requirements,
                source_jd_text=str(session.get("jd_text") or ""),
                source_user_notes=str(session.get("user_notes") or ""),
                ai_raw_response=ai_raw,
                created_by="human",
            )
            self.store.confirm_criteria_version(criteria_id)
        if not criteria_items or not personas:
            normalized_items, normalized_personas = normalize_job_profile(
                {
                    **ai_raw,
                    "requirements_text": requirements,
                    "criteria_items": criteria_items,
                    "personas": personas,
                }
            )
            criteria_items = criteria_items or normalized_items
            personas = personas or normalized_personas
        for item in criteria_items:
            item["human_confirmed"] = True
        self.store.replace_job_profile(criteria_id, criteria_items, personas)
        self.store.add_event(
            self.selected_session_id,
            None,
            "criteria_confirmed",
            "寻访基准已确认",
            "后续搜索、抓详情和匹配将基于当前岗位匹配要求执行。",
            {"requirements_text": requirements, "selected_direction": selected_direction},
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
            "系统正在后台生成岗位匹配要求，界面可继续操作。",
            {},
        )
        self._mark_dirty()
        future = self.runtime.match_queue.submit(self._generate_criteria_draft, session_id)

        def _done(done_future):
            self.regenerate_criteria_btn.setEnabled(True)
            self.regenerate_criteria_btn.setText("重新生成草案")
            try:
                done_future.result()
                self.event_bus.publish("criteria_ready", {"session_id": session_id})
            except Exception as exc:
                self.store.add_event(
                    session_id,
                    None,
                    "criteria_draft",
                    "寻访基准草案生成失败",
                    "可在右侧手动填写岗位匹配要求。错误：{}".format(exc),
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
        criteria_id = self.store.create_criteria_version(
            session_id,
            keywords,
            requirements,
            source_jd_text=str(session.get("jd_text") or ""),
            source_user_notes=str(session.get("user_notes") or ""),
            ai_raw_response=criteria,
            created_by="ai",
            status="draft",
        )
        criteria_items, personas = normalize_job_profile(criteria)
        self.store.replace_job_profile(criteria_id, criteria_items, personas)
        self.store.add_event(
            session_id,
            None,
            "criteria_draft",
            "AI 已生成寻访基准草案",
            "请人工确认或修改岗位匹配要求。",
            {"requirements_text": requirements},
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
        self._update_feedback_controls_state()
        self._update_reevaluate_button_state()
        self._mark_dirty()

    def _add_criterion_row(self) -> None:
        row = self.criteria_items_table.rowCount()
        self.criteria_items_table.insertRow(row)
        for column, value in enumerate(
            ["preferred", "", "0.6", "", "", "", "resume", "需要简历直接事实证据"]
        ):
            self.criteria_items_table.setItem(row, column, QTableWidgetItem(value))
        self.criteria_items_table.setCurrentCell(row, 1)

    def _remove_criterion_row(self) -> None:
        rows = sorted(
            {index.row() for index in self.criteria_items_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self.criteria_items_table.removeRow(row)

    def _add_persona_row(self) -> None:
        row = self.personas_table.rowCount()
        self.personas_table.insertRow(row)
        for column, value in enumerate(["新人才原型", "", "", "", "0.5"]):
            self.personas_table.setItem(row, column, QTableWidgetItem(value))
        self.personas_table.setCurrentCell(row, 0)

    def _remove_persona_row(self) -> None:
        rows = sorted(
            {index.row() for index in self.personas_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self.personas_table.removeRow(row)

    def _criteria_items_from_editor(self) -> List[Dict[str, object]]:
        result: List[Dict[str, object]] = []
        for row in range(self.criteria_items_table.rowCount()):
            values = [
                self.criteria_items_table.item(row, column).text().strip()
                if self.criteria_items_table.item(row, column)
                else ""
                for column in range(8)
            ]
            if not values[1]:
                continue
            try:
                weight = max(0.0, min(1.0, float(values[2] or 0.5)))
            except ValueError:
                weight = 0.5
            try:
                time_window = int(values[5]) if values[5] else None
            except ValueError:
                time_window = None
            result.append(
                {
                    "criterion_type": values[0] or "preferred",
                    "criterion_text": values[1],
                    "weight": weight,
                    "alternatives": self._split_editor_terms(values[3]),
                    "search_aliases": self._split_editor_terms(values[4]),
                    "time_window_years": time_window,
                    "observability": values[6] or "resume",
                    "evidence_policy": values[7],
                    "confidence": 1.0,
                    "human_confirmed": True,
                }
            )
        return result

    def _personas_from_editor(self) -> List[Dict[str, object]]:
        result: List[Dict[str, object]] = []
        for row in range(self.personas_table.rowCount()):
            values = [
                self.personas_table.item(row, column).text().strip()
                if self.personas_table.item(row, column)
                else ""
                for column in range(5)
            ]
            if not values[0]:
                continue
            try:
                priority = max(0.0, min(1.0, float(values[4] or 0.5)))
            except ValueError:
                priority = 0.5
            title_skills = self._split_editor_terms(values[2])
            result.append(
                {
                    "name": values[0],
                    "description": values[1],
                    "titles": title_skills[:3],
                    "skills": title_skills,
                    "company_patterns": self._split_editor_terms(values[3]),
                    "priority": priority,
                    "status": "active",
                }
            )
        return result

    @staticmethod
    def _split_editor_terms(value: str) -> List[str]:
        return [
            item.strip()
            for item in re.split(r"[，,、;；\n]+", value or "")
            if item.strip()
        ]

    def _on_candidate_selected(self) -> None:
        candidate_ids = self._selected_candidate_ids()
        if not candidate_ids:
            self.selected_candidate_id = None
            self.detail_view.clear()
            self._update_manual_greeting_button_state()
            self._update_feedback_controls_state()
            self._update_reevaluate_button_state()
            return
        self.selected_candidate_id = candidate_ids[0]
        self._render_candidate_detail(candidate_ids[0])
        self._update_manual_greeting_button_state()
        self._update_feedback_controls_state()
        self._update_reevaluate_button_state()
        # Auto-switch to detail tab when a candidate is selected
        self.right_tabs.setCurrentIndex(self.detail_tab_index)

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

    def _select_candidates_by_greeting_scope(self) -> None:
        state_counts: Dict[str, int] = {}
        row_states: Dict[int, str] = {}
        for row in range(self.candidate_table.rowCount()):
            state_item = self.candidate_table.item(row, 8)
            state = parse_recommendation_state(state_item.text() if state_item else "")
            if not state:
                continue
            row_states[row] = state
            state_counts[state] = state_counts.get(state, 0) + 1

        dialog = GreetingScopeDialog(state_counts, self)
        if dialog.exec() != QDialog.Accepted:
            return
        selected_states = set(dialog.selected_states())
        selection_model = self.candidate_table.selectionModel()
        if selection_model is None:
            return
        self.candidate_table.clearSelection()
        for row, state in row_states.items():
            if state not in selected_states:
                continue
            selection_model.select(
                self.candidate_table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
        self._on_candidate_selected()

    def _select_feedback_label(self, label: str) -> None:
        self._pending_feedback_label = label
        for value, button in self.feedback_buttons.items():
            button.setChecked(value == label)

    def _update_feedback_controls_state(self) -> None:
        if not hasattr(self, "save_feedback_btn"):
            return
        candidate_ids = self._selected_candidate_ids()
        enabled = bool(candidate_ids)
        self.pairwise_feedback_btn.setEnabled(len(candidate_ids) == 2)
        self.outcome_combo.setEnabled(len(candidate_ids) == 1)
        self.save_outcome_btn.setEnabled(len(candidate_ids) == 1)
        for button in self.feedback_buttons.values():
            button.setEnabled(enabled)
        self.feedback_reason_combo.setEnabled(enabled)
        self.feedback_note_input.setEnabled(enabled)
        self.save_feedback_btn.setEnabled(enabled)

        if len(candidate_ids) != 1:
            self._pending_feedback_label = ""
            self.feedback_button_group.setExclusive(False)
            for button in self.feedback_buttons.values():
                button.setChecked(False)
            self.feedback_button_group.setExclusive(True)
            self.feedback_reason_combo.setCurrentIndex(0)
            self.feedback_note_input.clear()
            return

        feedback = self.store.get_latest_candidate_feedback(candidate_ids[0]) or {}
        label = str(feedback.get("feedback_label") or "")
        self._pending_feedback_label = label
        self.feedback_button_group.setExclusive(False)
        for value, button in self.feedback_buttons.items():
            button.setChecked(value == label)
        self.feedback_button_group.setExclusive(True)
        reasons = feedback.get("reason_codes") or []
        reason = str(reasons[0] if reasons else "")
        reason_index = self.feedback_reason_combo.findData(reason)
        self.feedback_reason_combo.setCurrentIndex(max(0, reason_index))
        self.feedback_note_input.setText(str(feedback.get("note") or ""))

    def _save_candidate_feedback(self) -> None:
        candidate_ids = self._selected_candidate_ids()
        if not candidate_ids:
            return
        if not self._pending_feedback_label:
            QMessageBox.information(self, "人工判断", "请先选择推荐、待确认或不合适。")
            return
        reason = str(self.feedback_reason_combo.currentData() or "")
        reasons = [reason] if reason else []
        note = self.feedback_note_input.text().strip()
        for candidate_id in candidate_ids:
            self.store.save_candidate_feedback(
                candidate_id,
                self._pending_feedback_label,
                reason_codes=reasons,
                note=note,
            )
        if self.selected_session_id:
            self.runtime.ranking_service.refresh_session(self.selected_session_id)
        if self.selected_session_id:
            self.store.add_event(
                self.selected_session_id,
                None,
                "candidate_feedback",
                "已记录人工判断",
                "已标注 {} 位候选人。".format(len(candidate_ids)),
                {
                    "candidate_ids": candidate_ids,
                    "feedback_label": self._pending_feedback_label,
                    "reason_codes": reasons,
                },
            )
        self._mark_dirty()

    def _save_pairwise_feedback(self) -> None:
        candidate_ids = self._selected_candidate_ids()
        if len(candidate_ids) != 2 or not self.selected_session_id:
            return
        self.store.save_ranking_feedback(
            self.selected_session_id,
            candidate_ids[0],
            candidate_ids[1],
            reason=self.feedback_note_input.text().strip(),
        )
        self.runtime.ranking_service.refresh_session(self.selected_session_id)
        self._mark_dirty()

    def _save_candidate_outcome(self) -> None:
        candidate_ids = self._selected_candidate_ids()
        outcome = str(self.outcome_combo.currentData() or "")
        if len(candidate_ids) != 1 or not outcome:
            return
        self.store.save_candidate_outcome(
            candidate_ids[0],
            outcome,
            note=self.feedback_note_input.text().strip(),
        )
        self.outcome_combo.setCurrentIndex(0)
        self._render_candidate_detail(candidate_ids[0])

    def _update_reevaluate_button_state(self) -> None:
        if not hasattr(self, "reevaluate_btn"):
            return
        candidate_ids = self._selected_candidate_ids()
        eligible = any(
            str((self.store.get_candidate_detail(candidate_id) or {}).get("resume_text") or "").strip()
            for candidate_id in candidate_ids
        )
        self.reevaluate_btn.setEnabled(bool(candidate_ids) and eligible)
        self.reevaluate_btn.setToolTip(
            "按当前已确认的结构化岗位画像重新评估选中候选人。"
            if eligible
            else "候选人尚未抓取有效简历详情。"
        )

    def _reevaluate_selected_candidates(self) -> None:
        if not self.selected_session_id:
            return
        session = self.store.get_session(self.selected_session_id) or {}
        criteria = dict(self.store.get_latest_criteria(self.selected_session_id) or {})
        if not criteria.get("criteria_version_id"):
            QMessageBox.warning(self, "重新评估", "请先确认结构化岗位画像。")
            return
        criteria["jd_text"] = str(session.get("jd_text") or "")
        criteria["user_notes"] = str(session.get("user_notes") or "")
        queued = 0
        candidates = {
            str(item.get("id") or ""): item
            for item in self.store.get_candidates_by_ids(self._selected_candidate_ids())
        }
        for candidate_id, candidate in candidates.items():
            detail = self.store.get_candidate_detail(candidate_id) or {}
            resume_text = str(detail.get("resume_text") or "").strip()
            if not resume_text:
                continue
            structured, quality = self.runtime._detail_match_context(detail, resume_text)
            self.runtime.match_queue.submit(
                self.runtime._match_and_persist,
                self.selected_session_id,
                str(candidate.get("round_id") or ""),
                candidate_id,
                resume_text,
                criteria,
                None,
                None,
                structured,
                quality,
            )
            queued += 1
        self.stage_label.setText("已提交 {} 位候选人重新评估".format(queued))

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
                    str(c.get("id") or ""): c for c in candidates
                }
                # Batch-fetch all details to avoid N+1 queries
                all_details = {
                    cid: (self.store.get_candidate_detail(cid) or {})
                    for cid in candidate_ids
                }
                eligible = 0
                pending = 0
                missing = 0
                for candidate_id in candidate_ids:
                    detail = all_details.get(candidate_id) or {}
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
        self._force_refresh = True
        QTimer.singleShot(0, self._refresh_if_dirty)

    def _queue_runtime_event(
        self, event_type: str, payload: Dict[str, object]
    ) -> None:
        payload = payload or {}
        coalesced_events = {"event_added", "session_updated"}
        ui_only_events = {
            "browser_ready",
            "browser_closed",
            "browser_error",
            "excel_greeting_progress",
            "excel_greeting_done",
            "excel_greeting_error",
        }
        with self._runtime_events_lock:
            if event_type not in coalesced_events:
                self._runtime_events.append((event_type, payload))
        if event_type not in ui_only_events:
            self._dirty = True
            self._runtime_dirty = True
        if (
            event_type in {"criteria_ready", "manual_greeting_done"}
            or str(payload.get("event_type") or "")
            in {"session_completed", "session_failed", "session_cancelled"}
        ):
            self._force_refresh = True

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
        elif event_type == "excel_greeting_progress":
            current = int(payload.get("current") or 0)
            total = int(payload.get("total") or 0)
            self._pending_status_text = "Excel 批量打招呼：{}/{} {}".format(
                current, total, payload.get("name") or "",
            )
            if total > 0:
                self.greeting_progress.setMaximum(total)
                self.greeting_progress.setValue(current)
        elif event_type == "excel_greeting_done":
            self.batch_greeting_btn.setEnabled(True)
            self.batch_greeting_btn.setText("Excel 批量打招呼")
            self.cancel_greeting_btn.setVisible(False)
            self.cancel_greeting_btn.setEnabled(True)
            self.cancel_greeting_btn.setText("取消打招呼")
            self.greeting_progress.setVisible(False)
            self._active_greeting_service = None
            self._pending_status_text = "Excel 批量打招呼完成"
            QMessageBox.information(
                self,
                "批量打招呼完成",
                str(payload.get("summary") or "完成")
                if "dry-run" in str(payload.get("summary") or "")
                else "{}\n\n结果已回写：{}".format(
                    payload.get("summary") or "完成", payload.get("excel_path") or ""
                ),
            )
        elif event_type == "excel_greeting_error":
            self.batch_greeting_btn.setEnabled(True)
            self.batch_greeting_btn.setText("Excel 批量打招呼")
            self.cancel_greeting_btn.setVisible(False)
            self.cancel_greeting_btn.setEnabled(True)
            self.cancel_greeting_btn.setText("取消打招呼")
            self.greeting_progress.setVisible(False)
            self._active_greeting_service = None
            self._pending_status_text = "Excel 批量打招呼失败"
            QMessageBox.warning(self, "批量打招呼失败", str(payload.get("error") or "未知错误"))
        elif event_type == "criteria_ready":
            session_id = str(payload.get("session_id") or "")
            if session_id:
                # Always show notification when criteria is generated
                session = self.store.get_session(session_id)
                title = str(session.get("title") or "未命名") if session else "未命名"
                self._show_criteria_notification(session_id, title)
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
            now = time.monotonic()
            if (
                self._runtime_dirty
                and not self._force_refresh
                and now - self._last_refresh_monotonic
                < self._runtime_refresh_interval
            ):
                self._check_queue_advance()
                return
            lightweight = self._runtime_dirty and not self._force_refresh
            self.refresh_all(lightweight=lightweight)
            self._last_refresh_monotonic = now
            self._runtime_dirty = False
            self._force_refresh = False
        self._check_queue_advance()

    def refresh_all(self, lightweight: bool = False) -> None:
        self._dirty = False
        now = time.monotonic()
        if (
            lightweight
            and now - self._last_heavy_refresh_monotonic
            >= self._heavy_refresh_interval
        ):
            lightweight = False
        if not lightweight:
            self._last_heavy_refresh_monotonic = now
        self._refresh_pool()
        self._refresh_sessions()
        self._refresh_selected_session(lightweight=lightweight)

    def _refresh_sessions(self) -> None:
        sessions = self.store.list_sessions()
        new_ids = [str(s["id"]) for s in sessions]
        self._session_rows_by_id = {str(s["id"]): s for s in sessions}

        # Fast path: only update widgets in-place when the session list hasn't changed
        # to avoid destroying/recreating widgets (prevents scroll reset and flicker).
        if new_ids == self._session_list_ids:
            for index in range(self.session_list.count()):
                item = self.session_list.item(index)
                if item is None:
                    continue
                session_id = str(item.data(Qt.UserRole) or "")
                session = self._session_rows_by_id.get(session_id)
                if session is None:
                    continue
                existing_widget = self.session_list.itemWidget(item)
                if isinstance(existing_widget, SessionListItemWidget):
                    existing_widget.update_from_session(session)
                else:
                    new_widget = SessionListItemWidget(session, self)
                    item.setSizeHint(new_widget.sizeHint())
                    self.session_list.setItemWidget(item, new_widget)
            return

        # Full rebuild when the session list changes (add/remove)
        current_id = self.selected_session_id
        self.session_list.blockSignals(True)
        self.session_list.clear()
        first_item = None
        for session in sessions:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, session["id"])
            widget = SessionListItemWidget(session, self)
            item.setSizeHint(widget.sizeHint())
            self.session_list.addItem(item)
            self.session_list.setItemWidget(item, widget)
            if first_item is None:
                first_item = item
            if session["id"] == current_id:
                self.session_list.setCurrentItem(item)
        self.session_list.blockSignals(False)
        self._session_list_ids = new_ids

        # Bug fix: when no session was pre-selected, select the first one and
        # properly update selected_session_id so subsequent operations work correctly.
        if not current_id and first_item is not None:
            self.session_list.setCurrentItem(first_item)
            self.selected_session_id = str(first_item.data(Qt.UserRole) or "")

    def _select_session_in_list(self, session_id: str) -> None:
        for index in range(self.session_list.count()):
            item = self.session_list.item(index)
            if item.data(Qt.UserRole) == session_id:
                self.session_list.setCurrentItem(item)
                break

    def _refresh_selected_session(self, lightweight: bool = False) -> None:
        if not self.selected_session_id:
            self.selected_candidate_id = None
            self.title_label.setText("未选择任务")
            self.stage_label.setText("就绪")
            self.timeline.clear()
            self.candidate_table.setRowCount(0)
            self.candidate_table_title.setText("候选人详情")
            self._candidate_table_initialized = False
            self.strategy_view.clear()
            self.detail_view.clear()
            self.log_view.clear()
            self._update_manual_greeting_button_state()
            self._update_feedback_controls_state()
            self._update_chat_bar_state()
            return
        session = self._session_rows_by_id.get(self.selected_session_id) or {}
        if not session:
            session = self.store.get_session(self.selected_session_id) or {}
        aggregate = session
        metrics = self.store.session_efficiency_metrics(self.selected_session_id)
        if lightweight:
            feedback_summary = self._feedback_summary_snapshot
        else:
            feedback_summary = self.store.session_feedback_summary(
                self.selected_session_id
            )
            self._feedback_summary_snapshot = dict(feedback_summary)
        events = self.store.list_events(self.selected_session_id)
        self.title_label.setText(str(session.get("title") or "未命名任务"))
        latest_event = events[-1] if events else {}
        stage_text = latest_event.get("title") if latest_event else ""
        if session.get("error_message"):
            stage_text = "{} | {}".format(
                stage_text or "注意", session.get("error_message")
            )
        self.stage_label.setText(
            "状态：{}{}".format(
                STATUS_LABELS.get(
                    str(session.get("status") or ""), str(session.get("status") or "")
                ),
                " | {}".format(stage_text) if stage_text else "",
            )
        )
        self.stats_label.setText(
            "轮次 {} | 读卡 {} | 候选人 {} | 详情 {} | 有效池 {} | 已标注 {}".format(
                metrics.get("search_round_count") or 0,
                metrics.get("raw_candidate_count") or 0,
                aggregate.get("candidate_count") or 0,
                aggregate.get("detail_count") or 0,
                metrics.get("effective_pool_score") or 0,
                feedback_summary.get("labeled_candidate_count") or 0,
            )
        )
        self.stats_label.setToolTip(
            "人工判断一致率：{}".format(
                feedback_summary.get("agreement_rate")
                if feedback_summary.get("agreement_rate") is not None
                else "样本不足",
            )
        )
        self._render_timeline(events, limit=30 if lightweight else 80)
        self._render_candidates()
        if not lightweight:
            self._render_criteria_editor()
            self._render_strategy()
            self._render_logs(events)
            self._render_quality_dashboard()
        self._update_manual_greeting_button_state()
        self._update_feedback_controls_state()
        self._update_chat_bar_state()

    def _render_timeline(
        self, events: List[Dict[str, object]], limit: int = 80
    ) -> None:
        lines = []
        for event in events[-max(1, int(limit)):]:
            event_type = str(event.get("event_type") or "")
            if event_type in {"user_message", "agent_reply"}:
                role = "user" if event_type == "user_message" else "assistant"
                label = "我" if role == "user" else "寻访 Agent"
                lines.append(
                    bubble_html(
                        role, str(event.get("message") or ""), label=label
                    )
                )
                continue
            lines.append(
                "<p><b>{}</b> <span style='color:#8a8070'>{}</span><br>{}</p>".format(
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
        if self.criteria_requirements_input.hasFocus():
            return
        self.criteria_requirements_input.blockSignals(True)
        self.criteria_requirements_input.setPlainText(
            str(criteria.get("requirements_text") or "")
        )
        self.criteria_requirements_input.blockSignals(False)
        self._criteria_dirty = False
        is_confirmed = str(criteria.get("status") or "") == "confirmed"
        self.confirm_criteria_btn.setText(
            "已确认" if is_confirmed else "确认寻访基准"
        )
        # 填充寻访方向输入框
        ai_raw = criteria.get("ai_raw_response") or {}
        if isinstance(ai_raw, dict):
            direction = str(ai_raw.get("search_direction") or "").strip()
            if not direction:
                direction = str(ai_raw.get("selected_direction") or "").strip()
        else:
            direction = ""
        self.search_direction_input.setText(direction)
        self.search_direction_input.setEnabled(bool(direction))
        self.criteria_items_table.blockSignals(True)
        criteria_items = criteria.get("criteria_items") or []
        self.criteria_items_table.setRowCount(len(criteria_items))
        for row, item in enumerate(criteria_items):
            values = [
                item.get("criterion_type") or "preferred",
                item.get("criterion_text") or "",
                "{:.2f}".format(float(item.get("weight") or 0.5)),
                "、".join(item.get("alternatives") or []),
                "、".join(item.get("search_aliases") or []),
                item.get("time_window_years") or "",
                item.get("observability") or "resume",
                item.get("evidence_policy") or "",
            ]
            for column, value in enumerate(values):
                self.criteria_items_table.setItem(
                    row, column, QTableWidgetItem(str(value))
                )
        self.criteria_items_table.blockSignals(False)

        self.personas_table.blockSignals(True)
        personas = criteria.get("personas") or []
        self.personas_table.setRowCount(len(personas))
        for row, persona in enumerate(personas):
            title_skills = list(
                dict.fromkeys(
                    [*(persona.get("titles") or []), *(persona.get("skills") or [])]
                )
            )
            values = [
                persona.get("name") or "",
                persona.get("description") or "",
                "、".join(title_skills),
                "、".join(persona.get("company_patterns") or []),
                "{:.2f}".format(float(persona.get("priority") or 0.5)),
            ]
            for column, value in enumerate(values):
                self.personas_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.personas_table.blockSignals(False)

    def _render_logs(self, events: List[Dict[str, object]]) -> None:
        lines = []
        for event in events[-120:]:
            payload = event.get("payload") or {}
            payload_text = ""
            if payload:
                payload_text = (
                    "<pre style='white-space: pre-wrap; color:#6a6050'>{}</pre>".format(
                        self._html(json.dumps(payload, ensure_ascii=False, indent=2))
                    )
                )
            lines.append(
                "<div style='margin-bottom:10px'>"
                "<b>{}</b> <span style='color:#8a8070'>{} / {}</span><br>"
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

    def _render_candidates(self) -> None:
        selected_ids = set(self._selected_candidate_ids())
        if self.selected_candidate_id:
            selected_ids.add(self.selected_candidate_id)
        candidates = self.store.list_candidates(
            self.selected_session_id, detail_only=True
        )
        self.candidate_table_title.setText("候选人详情 ({})".format(len(candidates)))
        available_ids = {str(c.get("id") or "") for c in candidates}
        selected_ids = {cid for cid in selected_ids if cid in available_ids}

        self.candidate_table.blockSignals(True)
        self.candidate_table.setUpdatesEnabled(False)
        new_count = len(candidates)
        self.candidate_table.setRowCount(new_count)

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
                recommendation_label(candidate.get("recommendation_state")),
                "{:.0f}".format(float(candidate.get("known_fit_score") or 0)),
                "{:.0f}".format(float(candidate.get("potential_fit_score") or 0)),
                "{:.0f}".format(float(candidate.get("evidence_coverage_score") or 0)),
                "{:.0f}".format(float(candidate.get("rank_score") or 0)),
                self._feedback_label(candidate.get("feedback_label") or ""),
                self._greeting_status_label(candidate.get("greeting_status") or ""),
                candidate.get("status") or "",
                candidate.get("summary_text") or "",
            ]
            for column, value in enumerate(values):
                str_value = str(value)
                existing = self.candidate_table.item(row, column)
                same_candidate = (
                    column != 0
                    or existing is None
                    or str(existing.data(Qt.UserRole) or "") == candidate_id
                )
                if (
                    existing is not None
                    and existing.text() == str_value
                    and same_candidate
                ):
                    continue  # skip unchanged cells
                table_item = QTableWidgetItem(str_value)
                if column == 0:
                    table_item.setData(Qt.UserRole, candidate_id)
                if column in {6, 7, 8, 9, 10, 11, 12, 13, 14, 15}:
                    table_item.setTextAlignment(Qt.AlignCenter)

                # Keep operational states readable without turning cells into badges.
                if column == 8:
                    state_colors = {
                        "优先沟通": "#24543d",
                        "高潜待确认": "#8f5429",
                        "可迁移探索": "#3b6b55",
                        "信息不足": "#667085",
                        "明确不匹配": "#a33f3f",
                    }
                    fg = state_colors.get(str(value).strip())
                    if fg:
                        table_item.setForeground(QBrush(QColor(fg)))
                # Human review is expressed by the label first, then a restrained color.
                elif column == 13:
                    feedback_colors = {
                        "推荐": "#24543d",
                        "待确认": "#8f5429",
                        "不合适": "#667085",
                    }
                    fg = feedback_colors.get(str(value).strip())
                    if fg:
                        table_item.setForeground(QBrush(QColor(fg)))

                # Greeting status uses text color without covering the table surface.
                elif column == 14:
                    greeting = str(value).strip()
                    color_map = {
                        "已发送": "#2f6b4f",
                        "已打过": "#475467",
                        "已跳过": "#667085",
                        "失败": "#a33f3f",
                        "待处理": "#475467",
                    }
                    fg = color_map.get(greeting)
                    if fg:
                        table_item.setForeground(QBrush(QColor(fg)))

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
        self.candidate_table.setUpdatesEnabled(True)

        selected_after_refresh = self._selected_candidate_ids()
        self.selected_candidate_id = (
            selected_after_refresh[0] if selected_after_refresh else None
        )
        # Keep the comparison columns stable instead of resizing on every refresh.
        if not self._candidate_table_initialized and new_count > 0:
            self._candidate_table_initialized = True

    def _render_strategy(self) -> None:
        self._render_search_coverage()
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
        <p><b>岗位要求：</b>{requirements}</p>
        <p><b>搜索栏：</b>{query}</p>
        <p><b>职位栏：</b>{position}</p>
        <p><b>搜索假设：</b>{hypothesis_type} / {hypothesis_text}</p>
        <p><b>范围：</b>{scope} / {match_mode}</p>
        <p><b>城市：</b>{city}</p>
        <p><b>意图：</b>{intent}</p>
        <p><b>统计：</b>结果 {raw_count}，建议抓详情 {prequalified_count}，抓详情 {detail_fetch_count}</p>
        """.format(
            round_index=self._html(last.get("round_index")),
            status=self._html(last.get("status")),
            auto_greeting=self._html(self._greeting_policy_text()),
            criteria_version=self._html(criteria.get("version") or ""),
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
        )
        self.strategy_view.setHtml(html)

    def _render_search_coverage(self) -> None:
        if not self.selected_session_id or not hasattr(self, "coverage_table"):
            return
        criteria = self.store.get_latest_criteria(self.selected_session_id)
        self.store.ensure_search_hypotheses(self.selected_session_id, criteria)
        summary = self.store.search_coverage_summary(self.selected_session_id)
        hypotheses = summary.get("hypotheses") or []
        self.coverage_table.setRowCount(len(hypotheses))
        status_labels = {
            "pending": "待探索",
            "active": "搜索中",
            "completed": "已探索",
            "paused": "已暂停",
            "disabled": "已关闭",
        }
        for row, item in enumerate(hypotheses):
            values = [
                item.get("title") or "",
                item.get("query") or "",
                status_labels.get(item.get("status"), item.get("status") or ""),
                item.get("attempt_count") or 0,
                item.get("new_count") or 0,
                item.get("relevant_count") or 0,
                "{:.0f}%".format(float(item.get("duplicate_rate") or 0) * 100),
                "{:.2f}".format(float(item.get("priority") or 0)),
            ]
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(str(value))
                if column == 0:
                    table_item.setData(Qt.UserRole, str(item.get("id") or ""))
                self.coverage_table.setItem(row, column, table_item)
        self.coverage_summary_label.setText(
            "覆盖 {}/{} | 新增 {} | 有效 {}".format(
                summary.get("completed") or 0,
                summary.get("total") or 0,
                summary.get("new_count") or 0,
                summary.get("relevant_count") or 0,
            )
        )

    def _set_selected_hypothesis_status(self, status: str) -> None:
        row = self.coverage_table.currentRow()
        item = self.coverage_table.item(row, 0) if row >= 0 else None
        hypothesis_id = str(item.data(Qt.UserRole) or "") if item else ""
        if not hypothesis_id:
            return
        self.store.update_search_hypothesis(hypothesis_id, status=status)
        self._render_search_coverage()

    def _adjust_selected_hypothesis_priority(self, delta: float) -> None:
        row = self.coverage_table.currentRow()
        id_item = self.coverage_table.item(row, 0) if row >= 0 else None
        priority_item = self.coverage_table.item(row, 7) if row >= 0 else None
        hypothesis_id = str(id_item.data(Qt.UserRole) or "") if id_item else ""
        if not hypothesis_id or not priority_item:
            return
        try:
            priority = float(priority_item.text())
        except ValueError:
            priority = 0.5
        self.store.update_search_hypothesis(
            hypothesis_id, priority=max(0.0, min(1.0, priority + delta))
        )
        self._render_search_coverage()

    def _refresh_candidate_ranking(self) -> None:
        if not self.selected_session_id:
            return
        self.runtime.ranking_service.refresh_session(self.selected_session_id)
        self._mark_dirty()

    def _render_quality_dashboard(self) -> None:
        if not self.selected_session_id or not hasattr(self, "quality_view"):
            return
        dashboard = self.runtime.ranking_service.quality_dashboard(
            self.selected_session_id
        )
        feedback = dashboard.get("feedback") or {}
        coverage = dashboard.get("search_coverage") or {}
        candidate_pool = dashboard.get("candidate_pool") or {}
        state_counts = candidate_pool.get("state_counts") or {}
        calibration = dashboard.get("calibration") or {}
        calibration_metrics = calibration.get("metrics") or {}

        def percent(value):
            return "样本不足" if value is None else "{:.1f}%".format(float(value) * 100)

        ranking_rows = "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{:.0f}</td><td>{}</td></tr>".format(
                self._html(item.get("rank_position") or ""),
                self._html(item.get("name") or ""),
                self._html(recommendation_label(item.get("recommendation_state"))),
                float(item.get("rank_score") or 0),
                percent(item.get("calibrated_probability")),
            )
            for item in (dashboard.get("top_rankings") or [])[:10]
        )
        self.quality_view.setHtml(
            """
            <p><b>人工标注：</b>{labeled} / {total}</p>
            <p><b>模型与人工一致率：</b>{agreement}</p>
            <p><b>已标注样本精确率：</b>{precision}　<b>召回率：</b>{recall}</p>
            <p><b>假阳性：</b>{fp}　<b>假阴性：</b>{fn}</p>
            <p><b>校准样本：</b>{calibration_samples}　<b>Brier：</b>{brier}</p>
            <p><b>搜索覆盖：</b>{completed}/{coverage_total}　新增 {new_count}　有效 {relevant_count}</p>
            <p><b>有效候选池：</b>{effective_pool}　优先沟通 {priority}　高潜待确认 {verify}　可迁移探索 {transferable}　信息不足 {insufficient}　明确不匹配 {mismatch}</p>
            <p><b>当前排序：</b></p>
            <table cellspacing="0" cellpadding="4" border="1">
              <tr><th>排名</th><th>候选人</th><th>建议状态</th><th>综合分</th><th>校准概率</th></tr>
              {rankings}
            </table>
            """.format(
                labeled=feedback.get("labeled_candidate_count") or 0,
                total=feedback.get("candidate_count") or 0,
                agreement=percent(feedback.get("agreement_rate")),
                precision=percent(feedback.get("precision")),
                recall=percent(feedback.get("recall")),
                fp=feedback.get("false_positive") or 0,
                fn=feedback.get("false_negative") or 0,
                calibration_samples=calibration.get("sample_count") or 0,
                brier=calibration_metrics.get("brier_score", "样本不足"),
                completed=coverage.get("completed") or 0,
                coverage_total=coverage.get("total") or 0,
                new_count=coverage.get("new_count") or 0,
                relevant_count=coverage.get("relevant_count") or 0,
                effective_pool=candidate_pool.get("effective_pool_score") or 0,
                priority=state_counts.get("priority_contact") or 0,
                verify=state_counts.get("high_potential_verify") or 0,
                transferable=state_counts.get("transferable_explore") or 0,
                insufficient=state_counts.get("information_insufficient") or 0,
                mismatch=state_counts.get("explicit_mismatch") or 0,
                rankings=ranking_rows
                or "<tr><td colspan='5'>尚未生成排序</td></tr>",
            )
        )

    @staticmethod
    def _evidence_source_label(item: Dict[str, object]) -> str:
        if item.get("source_type") == "inferred":
            return "推断"
        grounding_status = item.get("grounding_status")
        if grounding_status == "exact":
            return "原文证据"
        if grounding_status == "model_summary":
            return "模型概括"
        return "匹配证据"

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
        ranking = next(
            (
                item
                for item in self.store.list_current_rankings(self.selected_session_id)
                if item.get("candidate_id") == candidate_id
            ),
            {},
        )
        feedback = self.store.get_latest_candidate_feedback(candidate_id) or {}
        outcomes = self.store.list_candidate_outcomes(candidate_id)
        sources = self.store.list_candidate_sources(candidate_id)
        evidence = match.get("matched_evidence") or []
        evidence_html = "".join(
            "<li><b>[{}] {}</b>：{} <span style='color:#8a8070'>{}</span></li>".format(
                self._evidence_source_label(item),
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
        criteria = self.store.get_latest_criteria(self.selected_session_id)
        evaluations = self.store.list_criterion_evaluations(
            candidate_id, str(criteria.get("criteria_version_id") or "")
        )
        verification_questions = list(
            dict.fromkeys(
                [
                    *[str(item) for item in (match.get("questions_to_verify") or []) if str(item)],
                    *[
                        str(item.get("verification_question") or "")
                        for item in evaluations
                        if str(item.get("verification_question") or "")
                    ],
                ]
            )
        )
        questions_html = "".join(
            "<li>{}</li>".format(self._html(item))
            for item in verification_questions
        )
        evaluation_labels = {
            "direct_met": "直接满足",
            "met": "直接满足",
            "inferred_met": "推断满足",
            "partial": "部分满足",
            "explicit_not_met": "明确不满足",
            "not_met": "明确不满足",
            "conflict": "明确冲突",
            "unknown": "未知",
        }
        evaluations_html = "".join(
            "<tr><td>{}</td><td>{}</td><td>{:.0f}%</td><td>{}</td><td>{}</td></tr>".format(
                self._html(evaluation.get("criterion_text") or ""),
                self._html(evaluation_labels.get(evaluation.get("status"), "未知")),
                float(evaluation.get("confidence") or 0) * 100,
                self._html(
                    "；".join(
                        str(item.get("quote") or "")
                        for item in (evaluation.get("evidence") or [])[:2]
                    )
                ),
                self._html(
                    "；".join(
                        item
                        for item in (
                            str(evaluation.get("reason") or ""),
                            str(evaluation.get("verification_question") or ""),
                        )
                        if item
                    )
                ),
            )
            for evaluation in evaluations
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
        outcome_html = "、".join(
            "{}({})".format(item.get("outcome") or "", item.get("occurred_at") or "")
            for item in outcomes[:5]
        )
        html = """
        <p><b>{name}</b> / {title}</p>
        <p>{company} | {city} | {work_years} | {education}</p>
        <p><b>金领：</b>{gold} | <b>打招呼：</b>{greeting_status}</p>
        <p><b>卡片判断：</b>{card_decision}</p>
        <p><b>建议状态：</b>{recommendation_state}</p>
        <p><b>量化视图：</b>已知适配 {known_fit_score} / 潜在上界 {potential_fit_score} / 证据覆盖 {evidence_coverage_score} / 综合排序 {rank_score} / 第 {rank_position} 位</p>
        <p><b>匹配建议：</b>{recommendation}</p>
        <p><b>人工判断：</b>{human_feedback}　<b>业务结果：</b>{outcomes}</p>
        <p><b>摘要：</b>{summary}</p>
        <p><b>风险：</b>{risks}</p>
        <p><b>逐条件评估：</b></p>
        <table cellspacing="0" cellpadding="4" border="1">
          <tr><th>岗位条件</th><th>判断</th><th>置信度</th><th>原文证据</th><th>说明</th></tr>
          {evaluations}
        </table>
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
            recommendation_state=self._html(
                recommendation_label(ranking.get("recommendation_state"))
            ),
            recommendation=self._html(match.get("recommendation")),
            known_fit_score=self._html(ranking.get("known_fit_score") or 0),
            potential_fit_score=self._html(
                ranking.get("potential_fit_score") or 0
            ),
            evidence_coverage_score=self._html(
                ranking.get("evidence_coverage_score") or 0
            ),
            rank_score=self._html(ranking.get("rank_score") or 0),
            rank_position=self._html(ranking.get("rank_position") or "-"),
            human_feedback=self._html(
                self._feedback_label(feedback.get("feedback_label") or "") or "未标注"
            ),
            outcomes=self._html(outcome_html or "暂无"),
            summary=self._html(
                match.get("summary") or candidate.get("summary_text") or ""
            ),
            risks=self._html(match.get("risks") or "暂无"),
            evaluations=evaluations_html
            or "<tr><td colspan='5'>尚未生成逐条件评估</td></tr>",
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

    @staticmethod
    def _feedback_label(value: object) -> str:
        return {
            "recommended": "推荐",
            "uncertain": "待确认",
            "not_suitable": "不合适",
        }.get(str(value or "").strip().lower(), "")

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
            elif status in {"running", "user_dialog", "waiting_approval", "criteria_draft", "criteria_confirmed", "ready", "paused"}:
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

    def _show_criteria_notification(self, session_id: str, title: str) -> None:
        """Show notification dialog when criteria is generated."""
        dialog = PoolNotificationDialog(title, session_id, self)
        result = dialog.exec()
        if result == 100:
            self.selected_session_id = session_id
            self._select_session_in_list(session_id)
            self.refresh_all()

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
                item.setForeground(Qt.GlobalColor.darkBlue)
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
                "user_dialog": "对话中",
                "waiting_approval": "等待确认",
                "paused": "已暂停",
            }
            extra = session_map.get(session_status, session_status)
            return "{}（{}）".format(base, extra)
        return base

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        active_session_ids = self.runtime.active_session_ids()
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
