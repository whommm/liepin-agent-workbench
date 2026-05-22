"""Main desktop workbench window."""

from __future__ import annotations

import json
import re
import threading
from collections import deque
from html import escape
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
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
    QVBoxLayout,
    QWidget,
)

from ..agent.runtime import AgentRuntime
from ..agent.brain import LLMAgentBrain
from ..core.config import ConfigManager
from ..services.event_bus import EventBus
from ..storage.sqlite_store import SQLiteStore, from_json
from ..tools.exporter import ExportService
from ..tools.excel_greeting import ExcelGreetingService
from ..tools.real_liepin import RealLiepinTool
from ..tools.real_matcher import RealMatchService
from .dialogs import BatchGreetingDialog, NewSessionDialog, PoolNotificationDialog, SettingsDialog
from .session_list_item import SessionListItemWidget
from .styles import MAIN_STYLESHEET



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

        # Cache for incremental session-list updates
        self._session_list_ids: List[str] = []

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
        self.add_to_pool_btn.setObjectName("SecondaryBtn")
        self.open_liepin_btn = QPushButton("打开猎聘")
        self.open_liepin_btn.setObjectName("SuccessBtn")
        self.close_liepin_btn = QPushButton("关闭浏览器")
        self.close_liepin_btn.setObjectName("DangerBtn")
        self.batch_greeting_btn = QPushButton("Excel 批量打招呼")
        self.batch_greeting_btn.setObjectName("SuccessBtn")
        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("SecondaryBtn")
        for button in [
            self.new_btn,
            self.add_to_pool_btn,
            self.open_liepin_btn,
            self.close_liepin_btn,
            self.batch_greeting_btn,
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
        self._candidate_table_initialized = False
        table_layout.addWidget(self.candidate_table, 1)

        splitter.addWidget(timeline_frame)
        splitter.addWidget(table_frame)
        splitter.setSizes([420, 300])
        return splitter

    def _build_right_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)

        layout.addWidget(QLabel("岗位匹配要求"))
        self.criteria_requirements_input = QTextEdit()
        self.criteria_requirements_input.setPlaceholderText(
            "用一段话描述本岗位最关键的匹配要求，例如：\n"
            "需要5年以上无刷电机设计经验，熟悉FOC控制算法，有小家电或新能源汽车行业背景优先。"
        )
        self.criteria_requirements_input.setMaximumHeight(150)
        layout.addWidget(self.criteria_requirements_input)

        layout.addWidget(QLabel("寻访方向（AI 对岗位的理解，可直接编辑修正）"))
        self.search_direction_input = QLineEdit()
        self.search_direction_input.setPlaceholderText("AI 生成草案后显示对岗位的理解方向")
        self.search_direction_input.setEnabled(False)
        layout.addWidget(self.search_direction_input)

        criteria_buttons = QHBoxLayout()
        self.regenerate_criteria_btn = QPushButton("重新生成草案")
        self.regenerate_criteria_btn.setObjectName("SecondaryBtn")
        self.confirm_criteria_btn = QPushButton("确认寻访基准")
        self.confirm_and_start_btn = QPushButton("确认并开始")
        self.confirm_and_start_btn.setObjectName("SuccessBtn")
        criteria_buttons.addWidget(self.regenerate_criteria_btn)
        criteria_buttons.addWidget(self.confirm_criteria_btn)
        criteria_buttons.addWidget(self.confirm_and_start_btn)
        layout.addLayout(criteria_buttons)

        # Tabbed lower area: 策略 / 候选人详情 / 日志
        self.right_tabs = QTabWidget()

        strategy_widget = QWidget()
        strategy_layout = QVBoxLayout(strategy_widget)
        strategy_layout.setContentsMargins(4, 4, 4, 4)
        self.strategy_view = QTextBrowser()
        self.strategy_view.setMinimumHeight(120)
        strategy_layout.addWidget(self.strategy_view)
        self.right_tabs.addTab(strategy_widget, "当前策略")

        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(4, 4, 4, 4)
        detail_header = QHBoxLayout()
        detail_header.addStretch(1)
        self.manual_greeting_btn = QPushButton("手动打招呼")
        self.manual_greeting_btn.setEnabled(False)
        detail_header.addWidget(self.manual_greeting_btn)
        detail_layout.addLayout(detail_header)
        self.detail_view = QTextBrowser()
        detail_layout.addWidget(self.detail_view, 1)
        self.right_tabs.addTab(detail_widget, "候选人详情")

        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(4, 4, 4, 4)
        self.log_view = QTextBrowser()
        log_layout.addWidget(self.log_view, 1)
        self.right_tabs.addTab(log_widget, "详细日志")

        layout.addWidget(self.right_tabs, 1)
        return frame

    def _connect_events(self) -> None:
        self.new_btn.clicked.connect(self.create_session)
        self.add_to_pool_btn.clicked.connect(self.create_session_and_add_to_pool)
        self.open_liepin_btn.clicked.connect(self.open_liepin_browser)
        self.close_liepin_btn.clicked.connect(self.close_liepin_browser)
        self.batch_greeting_btn.clicked.connect(self.open_batch_greeting_dialog)
        self.settings_btn.clicked.connect(self.open_settings)
        self.start_queue_btn.clicked.connect(self._start_queue)
        self.stop_queue_btn.clicked.connect(self._stop_queue)
        self.clear_completed_btn.clicked.connect(self._clear_completed_pool)
        self.pool_list.currentItemChanged.connect(self._on_pool_item_selected)
        self.manual_greeting_btn.clicked.connect(self.greet_selected_candidate)
        self.regenerate_criteria_btn.clicked.connect(self.regenerate_criteria_draft)
        self.confirm_criteria_btn.clicked.connect(self.confirm_current_criteria)
        self.confirm_and_start_btn.clicked.connect(self.confirm_criteria_and_start)
        self.criteria_requirements_input.textChanged.connect(self._mark_criteria_dirty)
        self.session_list.currentItemChanged.connect(self._on_session_changed)
        self.candidate_table.itemSelectionChanged.connect(self._on_candidate_selected)
        self.event_bus.subscribe(self._queue_runtime_event)

    def _apply_style(self) -> None:
        self.setStyleSheet(MAIN_STYLESHEET)

    def _create_session_from_dialog(self, add_to_pool: bool = False) -> Optional[str]:
        dialog = NewSessionDialog(self)
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
            target_ab_count=int(payload["target_ab_count"]),
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
        action_label = "预览 dry-run（不会实际发送）" if dry_run else "实际发送打招呼"
        gold_label = "发送前会重新打开页面复核金领状态" if verify_gold_on_page else "将信任 Excel 金领字段，不做页面复核"
        resume_label = "同时索要简历" if request_resume else "不索要简历"
        scope_label = "A/B + 金领" if gold_only else "A/B（全部）"
        reply = QMessageBox.question(
            self,
            "确认批量打招呼",
            "即将处理 Excel 中 {} 候选人。\n\n模式：{}\n安全复核：{}\n索要简历：{}\n文件：{}\n人数：{}\n候选人：{}\n\n是否继续？".format(
                scope_label,
                action_label,
                gold_label,
                resume_label,
                payload.get("excel_path") or "",
                count,
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
        )

    def _start_excel_batch_greeting(
        self,
        excel_path: str,
        message: str,
        dry_run: bool = False,
        verify_gold_on_page: bool = True,
        request_resume: bool = False,
        gold_only: bool = True,
    ) -> None:
        self.batch_greeting_btn.setEnabled(False)
        self.batch_greeting_btn.setText("预览中..." if dry_run else "打招呼中...")
        self.stage_label.setText("Excel 批量打招呼预览中" if dry_run else "Excel 批量打招呼进行中")

        def _run():
            try:
                service = ExcelGreetingService(self.runtime.liepin_tool)
                results = service.greet_from_excel(
                    excel_path,
                    message_template=message,
                    dry_run=dry_run,
                    verify_gold_on_page=verify_gold_on_page,
                    request_resume=request_resume,
                    gold_only=gold_only,
                    progress_callback=lambda current, total, name: self.event_bus.publish(
                        "excel_greeting_progress",
                        {"current": current, "total": total, "name": name},
                    ),
                )
                summary = ExcelGreetingService.generate_summary(results)
                self.event_bus.publish(
                    "excel_greeting_done",
                    {"summary": summary, "excel_path": excel_path},
                )
            except Exception as exc:
                self.event_bus.publish("excel_greeting_error", {"error": str(exc)})

        threading.Thread(target=_run, daemon=True).start()

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
                self.store.update_criteria_version(
                    str(criteria["id"]), keywords, requirements, status="draft"
                )
                # 同步更新 ai_raw_response_json
                try:
                    from ..storage.sqlite_store import SQLiteStore
                    if isinstance(self.store, SQLiteStore):
                        with self.store.connect() as conn:
                            conn.execute(
                                "UPDATE match_criteria_versions SET ai_raw_response_json = ? WHERE id = ?",
                                (json.dumps(ai_raw, ensure_ascii=False), str(criteria["id"])),
                            )
                except Exception:
                    pass
                self.store.confirm_criteria_version(str(criteria["id"]))
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
        # Auto-switch to detail tab when a candidate is selected
        self.right_tabs.setCurrentIndex(1)

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
        elif event_type == "excel_greeting_progress":
            self._pending_status_text = "Excel 批量打招呼：{}/{} {}".format(
                payload.get("current") or 0,
                payload.get("total") or 0,
                payload.get("name") or "",
            )
        elif event_type == "excel_greeting_done":
            self.batch_greeting_btn.setEnabled(True)
            self.batch_greeting_btn.setText("Excel 批量打招呼")
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
            self.refresh_all()
        self._check_queue_advance()

    def refresh_all(self) -> None:
        self._dirty = False
        self._refresh_pool()
        self._refresh_sessions()
        self._refresh_selected_session()

    def _refresh_sessions(self) -> None:
        sessions = self.store.list_sessions()
        new_ids = [str(s["id"]) for s in sessions]

        # Fast path: only update widgets in-place when the session list hasn't changed
        # to avoid destroying/recreating widgets (prevents scroll reset and flicker).
        if new_ids == self._session_list_ids:
            sessions_by_id = {str(s["id"]): s for s in sessions}
            for index in range(self.session_list.count()):
                item = self.session_list.item(index)
                if item is None:
                    continue
                session_id = str(item.data(Qt.UserRole) or "")
                session = sessions_by_id.get(session_id)
                if session is None:
                    continue
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

    def _refresh_selected_session(self) -> None:
        if not self.selected_session_id:
            self.selected_candidate_id = None
            self.title_label.setText("未选择任务")
            self.stage_label.setText("就绪")
            self.timeline.clear()
            self.candidate_table.setRowCount(0)
            self._candidate_table_initialized = False
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
            "轮次 {} | 读卡 {} | 候选人 {} | 详情 {} | A/B {}".format(
                len(self.store.list_rounds(self.selected_session_id)),
                metrics.get("raw_candidate_count") or 0,
                aggregate.get("candidate_count") or 0,
                aggregate.get("detail_count") or 0,
                aggregate.get("ab_count") or 0,
            )
        )
        self.stats_label.setToolTip(
            "A/B 每详情占比：{}".format(metrics.get("ab_per_detail_fetch") or 0)
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

    def _render_logs(self) -> None:
        events = self.store.list_events(self.selected_session_id)
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

    def _latest_event(self, session_id: str) -> Dict[str, object]:
        events = self.store.list_events(session_id)
        return events[-1] if events else {}

    def _render_candidates(self) -> None:
        selected_ids = set(self._selected_candidate_ids())
        if self.selected_candidate_id:
            selected_ids.add(self.selected_candidate_id)
        candidates = self.store.list_candidates(self.selected_session_id)
        available_ids = {str(c.get("id") or "") for c in candidates}
        selected_ids = {cid for cid in selected_ids if cid in available_ids}

        self.candidate_table.blockSignals(True)
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
                candidate.get("match_tier") or "",
                self._greeting_status_label(candidate.get("greeting_status") or ""),
                candidate.get("status") or "",
                candidate.get("summary_text") or "",
            ]
            for column, value in enumerate(values):
                str_value = str(value)
                existing = self.candidate_table.item(row, column)
                if existing is not None and existing.text() == str_value:
                    continue  # skip unchanged cells
                table_item = QTableWidgetItem(str_value)
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
        # Only resize columns on first population to prevent column-width jumps
        if not self._candidate_table_initialized and new_count > 0:
            self.candidate_table.resizeColumnsToContents()
            self.candidate_table.setColumnWidth(11, 260)
            self._candidate_table_initialized = True

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
            "<li><b>{}</b>：{} <span style='color:#8a8070'>{}</span></li>".format(
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
