"""Main desktop workbench window."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
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
        self.max_rounds.setValue(4)
        form.addRow("最大轮次", self.max_rounds)

        self.max_details = QSpinBox()
        self.max_details.setRange(1, 200)
        self.max_details.setValue(35)
        form.addRow("最大详情抓取", self.max_details)

        self.target_ab = QSpinBox()
        self.target_ab.setRange(1, 100)
        self.target_ab.setValue(8)
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
        self.resize(640, 360)

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

        self.browser_channel = QComboBox()
        self.browser_channel.addItems(["msedge", "chrome", "chromium"])
        index = self.browser_channel.findText(config.liepin_browser_channel or "msedge")
        self.browser_channel.setCurrentIndex(max(0, index))
        form.addRow("浏览器通道", self.browser_channel)

        self.profile_dir = QLineEdit(
            config.liepin_browser_profile_dir or "browser_profile/liepin"
        )
        form.addRow("猎聘 Profile", self.profile_dir)

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
            liepin_browser_channel=self.browser_channel.currentText(),
            liepin_browser_profile_dir=self.profile_dir.text().strip()
            or "browser_profile/liepin",
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

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self.continue_btn = QPushButton("继续")
        self.stop_btn = QPushButton("终止")
        self.export_btn = QPushButton("导出")
        self.delete_btn = QPushButton("删除")
        for button in [
            self.continue_btn,
            self.stop_btn,
            self.export_btn,
            self.delete_btn,
        ]:
            button.setFixedHeight(26)
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.continue_btn.clicked.connect(
            lambda: parent_window.continue_session(self.session_id)
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
        self._dirty = True
        self._criteria_dirty = False
        self._pending_status_text = ""
        self._pending_browser_error = ""

        self.setWindowTitle("猎聘寻访 Agent 工作台")
        self._build_ui()
        self._connect_events()
        self._apply_style()

        # Use event-driven refresh with a long-interval heartbeat fallback
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(10000)
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
        self.start_btn = QPushButton("开始")
        self.pause_btn = QPushButton("暂停")
        self.resume_btn = QPushButton("继续")
        self.cancel_btn = QPushButton("停止")
        self.export_btn = QPushButton("导出")
        self.open_liepin_btn = QPushButton("打开猎聘")
        self.close_liepin_btn = QPushButton("关闭浏览器")
        self.settings_btn = QPushButton("设置")
        for button in [
            self.new_btn,
            self.start_btn,
            self.pause_btn,
            self.resume_btn,
            self.cancel_btn,
            self.export_btn,
            self.open_liepin_btn,
            self.close_liepin_btn,
            self.settings_btn,
        ]:
            layout.addWidget(button)
        return frame

    def _build_left_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.addWidget(QLabel("寻访任务"))
        self.session_list = QListWidget()
        self.session_list.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.session_list, 1)
        return frame

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
        self.candidate_table = QTableWidget(0, 10)
        self.candidate_table.setHorizontalHeaderLabels(
            [
                "姓名",
                "公司",
                "职位",
                "城市",
                "年限",
                "学历",
                "卡片判断",
                "匹配",
                "状态",
                "摘要",
            ]
        )
        self.candidate_table.setSelectionBehavior(QAbstractItemView.SelectRows)
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
        layout.addWidget(QLabel("候选人详情"))
        self.detail_view = QTextBrowser()
        layout.addWidget(self.detail_view, 2)
        layout.addWidget(QLabel("详细日志"))
        self.log_view = QTextBrowser()
        layout.addWidget(self.log_view, 2)
        return frame

    def _connect_events(self) -> None:
        self.new_btn.clicked.connect(self.create_session)
        self.start_btn.clicked.connect(self.start_selected_session)
        self.pause_btn.clicked.connect(self.pause_selected_session)
        self.resume_btn.clicked.connect(self.resume_selected_session)
        self.cancel_btn.clicked.connect(self.cancel_selected_session)
        self.export_btn.clicked.connect(self.export_selected_session)
        self.open_liepin_btn.clicked.connect(self.open_liepin_browser)
        self.close_liepin_btn.clicked.connect(self.close_liepin_browser)
        self.settings_btn.clicked.connect(self.open_settings)
        self.regenerate_criteria_btn.clicked.connect(self.regenerate_criteria_draft)
        self.confirm_criteria_btn.clicked.connect(self.confirm_current_criteria)
        self.confirm_and_start_btn.clicked.connect(self.confirm_criteria_and_start)
        self.criteria_keywords_input.textChanged.connect(self._mark_criteria_dirty)
        self.criteria_requirements_input.textChanged.connect(self._mark_criteria_dirty)
        self.session_list.currentItemChanged.connect(self._on_session_changed)
        self.candidate_table.itemSelectionChanged.connect(self._on_candidate_selected)
        self.event_bus.subscribe(self._handle_runtime_event)

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

    def start_selected_session(self) -> None:
        if not self.selected_session_id:
            QMessageBox.information(self, "提示", "请先选择或新建任务")
            return
        self.continue_session(self.selected_session_id)

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

    def export_session(self, session_id: str) -> None:
        exporter = ExportService(self.store, self.workspace_root / "exports")
        path = exporter.export_session(session_id)
        QMessageBox.information(self, "导出完成", "已导出到：\n{}".format(path))

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
        self._mark_dirty()

    def _on_candidate_selected(self) -> None:
        selected = self.candidate_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        item = self.candidate_table.item(row, 0)
        if not item:
            return
        candidate_id = item.data(Qt.UserRole)
        self._render_candidate_detail(candidate_id)

    def _mark_dirty(self) -> None:
        self._dirty = True
        # Trigger refresh on next event loop iteration instead of waiting for timer
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._refresh_if_dirty)

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
        else:
            self._mark_dirty()

    def _refresh_if_dirty(self) -> None:
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

    def refresh_all(self) -> None:
        self._dirty = False
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
            self.title_label.setText("未选择任务")
            self.stage_label.setText("就绪")
            self.timeline.clear()
            self.candidate_table.setRowCount(0)
            self.strategy_view.clear()
            self.detail_view.clear()
            self.log_view.clear()
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
        candidates = self.store.list_candidates(self.selected_session_id)
        self.candidate_table.setRowCount(len(candidates))
        for row, candidate in enumerate(candidates):
            values = [
                candidate.get("name") or "",
                candidate.get("current_company") or "",
                candidate.get("current_title") or "",
                candidate.get("city") or "",
                candidate.get("work_years") or "",
                candidate.get("education") or "",
                self._card_decision_label(candidate.get("card_decision") or ""),
                candidate.get("match_tier") or "",
                candidate.get("status") or "",
                candidate.get("summary_text") or "",
            ]
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                if column == 0:
                    table_item.setData(Qt.UserRole, candidate.get("id"))
                if column == 6:
                    table_item.setTextAlignment(Qt.AlignCenter)
                self.candidate_table.setItem(row, column, table_item)
        self.candidate_table.resizeColumnsToContents()
        self.candidate_table.setColumnWidth(9, 260)

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
        html = """
        <p><b>{name}</b> / {title}</p>
        <p>{company} | {city} | {work_years} | {education}</p>
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

    def _mark_criteria_dirty(self) -> None:
        self._criteria_dirty = True

    @staticmethod
    def _card_decision_label(value: object) -> str:
        return {
            "fetch": "值得抓详情",
            "maybe": "信息不足",
            "noise": "明显噪音",
        }.get(str(value or ""), "信息不足")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        try:
            self.runtime.liepin_tool.close()
        except Exception:
            pass
        self.runtime.browser_queue.shutdown()
        self.runtime.match_queue.shutdown()
        super().closeEvent(event)
