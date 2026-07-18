"""Reusable dialog classes for the liepin workbench UI."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
)

from ..agent.planner import Planner
from ..services.jd_consultant import JDConsultant
from ..core.config import ConfigManager
from ..domain.greeting_policy import (
    DEFAULT_GREETING_STATES,
    GREETING_BLOCKED_STATES,
    GREETING_SELECTABLE_STATES,
)
from ..domain.recommendation import RECOMMENDATION_LABELS
from ..tools.excel_greeting import ExcelGreetingService
from .chat_bubbles import bubble_html


class _GreetingGenerationSignals(QObject):
    done = Signal(int, list)
    failed = Signal(str)


class _ChatSignals(QObject):
    replied = Signal(str)
    finalized = Signal(str)
    failed = Signal(str)


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
            QLabel('<b style="font-size:15px;">项目《{}》</b>'.format(title))
        )
        info = QLabel("岗位匹配要求已生成，请确认后开始搜索。")
        info.setWordWrap(True)
        layout.addWidget(info)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        self.later_btn = QPushButton("稍后")
        self.later_btn.setObjectName("SecondaryBtn")
        self.confirm_btn = QPushButton("去确认")
        self.confirm_btn.setDefault(True)
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
    """Chat-style new-task dialog.

    The user pastes a JD and discusses it with the JD consultant (chat model).
    "固定方案" unlocks after at least one discussion round; clicking it asks the
    consultant to produce the final 《寻访方案》, which becomes the session jd_text.
    """

    MIN_USER_MESSAGES = 2  # JD 本身 + 至少一轮回复

    def __init__(self, config_manager: ConfigManager, parent=None, consultant=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.consultant = consultant or JDConsultant.from_config(config_manager)
        self._history: List[Dict[str, str]] = []
        self._busy = False
        self._signals = _ChatSignals(self)
        self._signals.replied.connect(self._on_reply)
        self._signals.finalized.connect(self._on_finalized)
        self._signals.failed.connect(self._on_failed)

        self.setWindowTitle("新建寻访任务 · JD 讨论")
        self.resize(760, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(8)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("任务名称（留空则按方案自动命名），例如：文创产品经理 / 深圳")
        layout.addWidget(self.title_input)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["自动", "单步", "监督"])
        self.mode_combo.setFixedWidth(86)

        self.max_rounds = QSpinBox()
        self.max_rounds.setRange(1, 30)
        self.max_rounds.setValue(20)
        self.max_rounds.setFixedWidth(70)

        self.max_details = QSpinBox()
        self.max_details.setRange(1, 9999)
        self.max_details.setValue(999)
        self.max_details.setFixedWidth(84)

        self.target_effective = QSpinBox()
        self.target_effective.setRange(1, 9999)
        self.target_effective.setValue(999)
        self.target_effective.setFixedWidth(84)

        params_row = QHBoxLayout()
        params_row.setSpacing(14)
        for caption_text, widget in (
            ("运行模式", self.mode_combo),
            ("最大轮次", self.max_rounds),
            ("最大详情抓取", self.max_details),
            ("目标有效候选池", self.target_effective),
        ):
            caption = QLabel(caption_text)
            caption.setObjectName("SessionInfo")
            params_row.addWidget(caption)
            params_row.addWidget(widget)
        params_row.addStretch(1)
        layout.addLayout(params_row)

        self.round_label = QLabel("粘贴岗位 JD 发送，先与寻访顾问讨论确认方向。")
        self.round_label.setObjectName("SessionInfo")
        self.round_label.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.round_label)

        self.chat_view = QTextBrowser()
        layout.addWidget(self.chat_view, 1)

        self.plan_edit = QTextEdit()
        self.plan_edit.setPlaceholderText("《寻访方案》终稿，可直接编辑修正。")
        self.plan_edit.hide()
        layout.addWidget(self.plan_edit, 1)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.chat_input = QTextEdit()
        self.chat_input.setMinimumHeight(64)
        self.chat_input.setMaximumHeight(88)
        self.chat_input.setPlaceholderText("粘贴 JD 或回复顾问（Ctrl+Enter 发送）")
        self.chat_input.installEventFilter(self)
        input_row.addWidget(self.chat_input, 1)
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("AccentBtn")
        self.send_btn.setFixedWidth(84)
        self.send_btn.setMinimumHeight(64)
        self.send_btn.clicked.connect(self._send_message)
        input_row.addWidget(self.send_btn, 0, Qt.AlignBottom)
        layout.addLayout(input_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("SecondaryBtn")
        self.cancel_btn.clicked.connect(self.reject)
        self.back_btn = QPushButton("返回继续讨论")
        self.back_btn.setObjectName("SecondaryBtn")
        self.back_btn.clicked.connect(self._back_to_discussion)
        self.back_btn.hide()
        self.finalize_btn = QPushButton("固定方案")
        self.finalize_btn.setObjectName("SuccessBtn")
        self.finalize_btn.setEnabled(False)
        self.finalize_btn.setToolTip("请先回复顾问的问题，完成至少一轮讨论。")
        self.finalize_btn.clicked.connect(self._finalize)
        self.confirm_btn = QPushButton("确认创建任务")
        self.confirm_btn.setObjectName("SuccessBtn")
        self.confirm_btn.setDefault(True)
        self.confirm_btn.clicked.connect(self._confirm)
        self.confirm_btn.hide()
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.back_btn)
        buttons.addWidget(self.finalize_btn)
        buttons.addWidget(self.confirm_btn)
        layout.addLayout(buttons)

    # --------------------------------------------------------------
    # discussion flow
    # --------------------------------------------------------------
    def eventFilter(self, obj, event):
        if obj is self.chat_input and event.type() == QEvent.Type.KeyPress:
            if (
                event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() & Qt.ControlModifier
            ):
                self._send_message()
                return True
        return super().eventFilter(obj, event)

    def _user_message_count(self) -> int:
        return sum(1 for item in self._history if item.get("role") == "user")

    def _can_finalize(self) -> bool:
        return self._user_message_count() >= self.MIN_USER_MESSAGES

    def _append_message(self, role: str, content: str) -> None:
        self.chat_view.append(bubble_html(role, content))

    def _send_message(self) -> None:
        if self._busy:
            return
        content = self.chat_input.toPlainText().strip()
        if not content:
            return
        self._history.append({"role": "user", "content": content})
        self._append_message("user", content)
        self.chat_input.clear()
        self._set_busy(True, "顾问正在思考…")
        history_snapshot = list(self._history)

        def _run():
            try:
                reply = self.consultant.reply(history_snapshot)
                self._signals.replied.emit(reply)
            except Exception as exc:
                self._signals.failed.emit(str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _on_reply(self, content: str) -> None:
        self._history.append({"role": "assistant", "content": content})
        self._append_message("assistant", content)
        self._set_busy(False)
        self._update_round_state()

    def _on_failed(self, error: str) -> None:
        self._set_busy(False)
        self._update_round_state()
        QMessageBox.warning(
            self,
            "讨论失败",
            "{}\n\n请检查设置中的讨论模型配置。".format(error or "未知错误"),
        )

    def _set_busy(self, busy: bool, hint: str = "") -> None:
        self._busy = busy
        self.send_btn.setEnabled(not busy)
        self.send_btn.setText("思考中..." if busy else "发送")
        self.chat_input.setEnabled(not busy)
        self.finalize_btn.setEnabled((not busy) and self._can_finalize())
        if not busy:
            self.finalize_btn.setText("固定方案")
        self.back_btn.setEnabled(not busy)
        if busy and hint:
            self.round_label.setText(hint)

    def _update_round_state(self) -> None:
        rounds = self._user_message_count() - 1  # 第一条消息是 JD 本身
        if rounds >= 1:
            self.round_label.setText(
                "已讨论 {} 轮，可以固定方案，也可以继续讨论。".format(rounds)
            )
        else:
            self.round_label.setText("顾问已给出分析，请至少回复一轮后再固定方案。")

    # --------------------------------------------------------------
    # finalize flow
    # --------------------------------------------------------------
    def _finalize(self) -> None:
        if self._busy or not self._can_finalize():
            return
        self._set_busy(True, "正在生成《寻访方案》终稿…")
        self.finalize_btn.setText("生成中...")
        history_snapshot = list(self._history)

        def _run():
            try:
                plan = self.consultant.finalize_plan(history_snapshot)
                self._signals.finalized.emit(plan)
            except Exception as exc:
                self._signals.failed.emit(str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _on_finalized(self, plan: str) -> None:
        self.plan_edit.setPlainText(plan)
        self._show_plan_state(True)
        self._set_busy(False)

    def _show_plan_state(self, show_plan: bool) -> None:
        self.chat_view.setVisible(not show_plan)
        self.chat_input.setVisible(not show_plan)
        self.send_btn.setVisible(not show_plan)
        self.finalize_btn.setVisible(not show_plan)
        self.plan_edit.setVisible(show_plan)
        self.back_btn.setVisible(show_plan)
        self.confirm_btn.setVisible(show_plan)
        if show_plan:
            self.round_label.setText(
                "《寻访方案》已生成，可直接编辑修正；确认后以此为岗位方向创建任务。"
            )
        else:
            self._update_round_state()

    def _back_to_discussion(self) -> None:
        self._show_plan_state(False)

    def _confirm(self) -> None:
        if not self.plan_edit.toPlainText().strip():
            QMessageBox.warning(self, "提示", "方案内容为空，无法创建任务。")
            return
        self.accept()

    def payload(self) -> Dict[str, object]:
        jd_text = self.plan_edit.toPlainText().strip()
        title = self.title_input.text().strip() or Planner.infer_title(jd_text)
        return {
            "title": title,
            "jd_text": jd_text,
            "user_notes": "",
            "mode": self.mode_combo.currentText(),
            "max_rounds": self.max_rounds.value(),
            "max_detail_fetches": self.max_details.value(),
            "target_effective_count": self.target_effective.value(),
        }


class GreetingScopeDialog(QDialog):
    """Select recommendation states before populating the manual greeting queue."""

    def __init__(self, state_counts: Dict[str, int], parent=None):
        super().__init__(parent)
        self.setWindowTitle("按资格选择候选人")
        self.resize(420, 300)
        layout = QVBoxLayout(self)
        intro = QLabel("选择要加入手动打招呼名单的候选人资格。确认后仍可在表格中逐人取消。")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.state_checks: Dict[str, QCheckBox] = {}
        for state in (*GREETING_SELECTABLE_STATES, *GREETING_BLOCKED_STATES):
            checkbox = QCheckBox(
                "{}（{} 人）".format(
                    RECOMMENDATION_LABELS[state], int(state_counts.get(state, 0))
                )
            )
            checkbox.setChecked(state in DEFAULT_GREETING_STATES)
            if state in GREETING_BLOCKED_STATES:
                checkbox.setEnabled(False)
                checkbox.setToolTip("明确不匹配不进入批量名单；如确需联系，请单独选择候选人。")
            self.state_checks[state] = checkbox
            layout.addWidget(checkbox)

        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("SecondaryBtn")
        confirm_btn = QPushButton("选择候选人")
        confirm_btn.setDefault(True)
        cancel_btn.clicked.connect(self.reject)
        confirm_btn.clicked.connect(self._validate_and_accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(confirm_btn)
        layout.addLayout(buttons)

    def selected_states(self) -> List[str]:
        return [
            state
            for state in GREETING_SELECTABLE_STATES
            if self.state_checks[state].isChecked()
        ]

    def _validate_and_accept(self) -> None:
        if not self.selected_states():
            QMessageBox.warning(self, "提示", "请至少选择一个候选人资格。")
            return
        self.accept()


class BatchGreetingDialog(QDialog):
    def __init__(self, config_manager: ConfigManager, workspace_root: Path, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.workspace_root = Path(workspace_root)
        self._candidates = []
        self._generation_signals = _GreetingGenerationSignals(self)
        self._generation_signals.done.connect(self._on_generation_done)
        self._generation_signals.failed.connect(self._on_generation_failed)
        self.setWindowTitle("Excel 批量打招呼")
        self.resize(780, 780)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("导入候选人 Excel，按建议状态筛选未联系且有猎聘详情链接的候选人。")
        )

        file_row = QHBoxLayout()
        self.excel_path = QLineEdit()
        self.excel_path.setPlaceholderText("选择导出的候选人 Excel")
        last_path = config_manager.config.last_greeting_excel_path
        if last_path and Path(last_path).exists():
            self.excel_path.setText(last_path)
        browse_btn = QPushButton("选择 Excel")
        browse_btn.setObjectName("SecondaryBtn")
        browse_btn.clicked.connect(self._pick_excel)
        file_row.addWidget(self.excel_path, 1)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        self.summary_label = QLabel("未导入 Excel。")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        layout.addWidget(QLabel("候选人资格（可多选）"))
        scope_row = QHBoxLayout()
        self.state_checks: Dict[str, QCheckBox] = {}
        for state in GREETING_SELECTABLE_STATES:
            checkbox = QCheckBox(RECOMMENDATION_LABELS[state])
            checkbox.setChecked(state in DEFAULT_GREETING_STATES)
            checkbox.stateChanged.connect(self._on_scope_changed)
            self.state_checks[state] = checkbox
            scope_row.addWidget(checkbox)
        blocked_check = QCheckBox(RECOMMENDATION_LABELS[GREETING_BLOCKED_STATES[0]])
        blocked_check.setEnabled(False)
        blocked_check.setToolTip("明确不匹配不允许批量打招呼。")
        scope_row.addWidget(blocked_check)
        scope_row.addStretch(1)
        layout.addLayout(scope_row)

        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("本次打招呼人数："))
        self.max_candidates_spin = QSpinBox()
        self.max_candidates_spin.setRange(0, 9999)
        self.max_candidates_spin.setValue(0)
        self.max_candidates_spin.setSpecialValueText("全部")
        self.max_candidates_spin.setToolTip("0 = 全部处理；填写具体数字则按建议状态优先级处理前 N 位。")
        self.max_candidates_spin.setSuffix(" 人")
        limit_row.addWidget(self.max_candidates_spin)
        limit_row.addStretch(1)
        layout.addLayout(limit_row)

        self.dry_run_check = QCheckBox("仅预览 dry-run，不实际发送打招呼")
        self.dry_run_check.setChecked(True)
        self.gold_only_check = QCheckBox("仅处理金领候选人")
        self.gold_only_check.setChecked(config_manager.config.greet_gold_only)
        self.gold_only_check.setToolTip("开启后只处理 Excel 中标记为金领且符合所选资格的候选人。")
        self.gold_only_check.stateChanged.connect(self._on_gold_only_changed)
        self.verify_gold_check = QCheckBox("发送前重新打开页面复核金领状态")
        self.verify_gold_check.setChecked(True)
        self.verify_gold_check.setToolTip("建议保持开启，避免 Excel 被编辑或数据过期后误发。")
        self.request_resume_check = QCheckBox('同时索要简历（发送招呼后自动点击"索要简历"）')
        self.request_resume_check.setChecked(True)
        layout.addWidget(self.dry_run_check)
        layout.addWidget(self.gold_only_check)
        layout.addWidget(self.verify_gold_check)
        layout.addWidget(self.request_resume_check)

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("间隔延迟（秒）："))
        self.delay_min_spin = QDoubleSpinBox()
        self.delay_min_spin.setRange(0.5, 30.0)
        self.delay_min_spin.setSingleStep(0.5)
        self.delay_min_spin.setValue(config_manager.config.greet_delay_min)
        self.delay_min_spin.setToolTip("每个候选人之间的最小随机延迟")
        delay_row.addWidget(self.delay_min_spin)
        delay_row.addWidget(QLabel("~"))
        self.delay_max_spin = QDoubleSpinBox()
        self.delay_max_spin.setRange(1.0, 60.0)
        self.delay_max_spin.setSingleStep(0.5)
        self.delay_max_spin.setValue(config_manager.config.greet_delay_max)
        self.delay_max_spin.setToolTip("每个候选人之间的最大随机延迟")
        delay_row.addWidget(self.delay_max_spin)
        delay_row.addWidget(QLabel("  失败重试："))
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 5)
        self.retry_spin.setValue(config_manager.config.greet_max_retries)
        self.retry_spin.setToolTip("对临时性错误自动重试的次数")
        self.retry_spin.setSuffix(" 次")
        delay_row.addWidget(self.retry_spin)
        delay_row.addStretch(1)
        layout.addLayout(delay_row)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(100)
        self.preview.setPlaceholderText("导入后显示候选人预览")
        layout.addWidget(self.preview)

        layout.addWidget(QLabel("岗位信息（用于生成打招呼文本）"))
        form = QFormLayout()
        self.job_title = QLineEdit()
        self.city = QLineEdit()
        self.salary = QLineEdit()
        self.jd_text = QTextEdit()
        self.jd_text.setMaximumHeight(110)
        form.addRow("岗位名称", self.job_title)
        form.addRow("城市", self.city)
        form.addRow("薪资", self.salary)
        form.addRow("JD", self.jd_text)
        layout.addLayout(form)

        generate_row = QHBoxLayout()
        self.generate_btn = QPushButton("生成打招呼文本")
        self.generate_btn.clicked.connect(self._generate_one)
        generate_row.addWidget(self.generate_btn)
        generate_row.addStretch(1)
        layout.addLayout(generate_row)

        self.generate_status = QLabel("未生成话术。")
        self.generate_status.setObjectName("SessionInfo")
        layout.addWidget(self.generate_status)

        self.variant_combo = QComboBox()
        self.variant_combo.currentIndexChanged.connect(self._select_variant)
        layout.addWidget(self.variant_combo)

        layout.addWidget(QLabel("打招呼文本（可编辑，最终按这里发送）"))
        self.message_text = QTextEdit(config_manager.config.greeting_template or "")
        self.message_text.setMinimumHeight(120)
        self.message_text.setPlaceholderText("请先生成或手动填写打招呼文本。留空则使用平台默认打招呼。")
        layout.addWidget(self.message_text, 1)

        hint_label = QLabel("可用变量：{name} 姓名 · {current_company} 公司 · {current_title} 职位")
        hint_label.setObjectName("SessionInfo")
        hint_label.setToolTip("在文本中使用 {变量名} 会被自动替换为对应候选人信息。")
        layout.addWidget(hint_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("SecondaryBtn")
        start_btn = QPushButton("开始批量打招呼")
        start_btn.setObjectName("SuccessBtn")
        cancel_btn.clicked.connect(self.reject)
        start_btn.clicked.connect(self._validate_and_accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(start_btn)
        layout.addLayout(buttons)

        if last_path and Path(last_path).exists():
            self._load_preview(last_path)

    def _on_gold_only_changed(self, _state: int) -> None:
        path = self.excel_path.text().strip()
        if path and Path(path).exists():
            self._load_preview(path)

    def _on_scope_changed(self, _state: int) -> None:
        path = self.excel_path.text().strip()
        if path and Path(path).exists():
            self._load_preview(path)

    def selected_recommendation_states(self) -> List[str]:
        return [
            state
            for state in GREETING_SELECTABLE_STATES
            if self.state_checks[state].isChecked()
        ]

    def _pick_excel(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择候选人 Excel",
            str(self.workspace_root / "exports"),
            "Excel 文件 (*.xlsx)",
        )
        if not path:
            return
        self.excel_path.setText(path)
        self._load_preview(path)

    def _load_preview(self, path: str) -> None:
        try:
            self._candidates = ExcelGreetingService.load_greetable_candidates(
                path,
                gold_only=self.gold_only_check.isChecked(),
                recommendation_states=self.selected_recommendation_states(),
            )
        except Exception as exc:
            self._candidates = []
            QMessageBox.warning(self, "读取失败", str(exc))
            return
        names = "、".join(item.name for item in self._candidates[:10])
        if len(self._candidates) > 10:
            names += " 等"
        gold_text = "金领 + " if self.gold_only_check.isChecked() else ""
        state_counts = {}
        for item in self._candidates:
            state_counts[item.recommendation_state] = state_counts.get(item.recommendation_state, 0) + 1
        scope_summary = " / ".join(
            "{} {} 人".format(RECOMMENDATION_LABELS[state], state_counts.get(state, 0))
            for state in self.selected_recommendation_states()
        )
        self.summary_label.setText(
            "可处理候选人：{} 位（{}；筛选：{}未打过 + 猎聘详情链接）。".format(
                len(self._candidates),
                scope_summary or "未选择资格",
                gold_text,
            )
        )
        self.max_candidates_spin.setMaximum(len(self._candidates))
        if self.max_candidates_spin.value() == 0 or self.max_candidates_spin.value() > len(self._candidates):
            self.max_candidates_spin.setValue(0)
        self.preview.setPlainText(names or "没有符合条件的候选人。")

    def set_job_defaults(self, title: str, jd_text: str, city: str = "", salary: str = "") -> None:
        self.job_title.setText(title or "")
        self.city.setText(city or "")
        self.salary.setText(salary or "")
        self.jd_text.setPlainText(jd_text or "")

    def _generate_one(self) -> None:
        self._start_generation(count=1)

    def _start_generation(self, count: int) -> None:
        self._set_generating(True)
        self._generation_cancelled = False
        job_title = self.job_title.text().strip() or "目标岗位"
        city = self.city.text().strip() or "该城市"
        jd_text = self.jd_text.toPlainText().strip()
        salary = self.salary.text().strip()

        def _run():
            try:
                texts = self._generate_texts(
                    count=count,
                    job_title=job_title,
                    city=city,
                    jd_text=jd_text,
                    salary=salary,
                )
                if not getattr(self, "_generation_cancelled", False):
                    self._generation_signals.done.emit(count, texts)
            except Exception as exc:
                if not getattr(self, "_generation_cancelled", False):
                    self._generation_signals.failed.emit(str(exc))

        self._generation_thread = threading.Thread(target=_run, daemon=True)

        def _timeout():
            self._generation_cancelled = True
            self._generation_signals.failed.emit(
                "生成超时（90秒），请检查网络或 API 配置。"
            )

        self._generation_timer = threading.Timer(90.0, _timeout)
        self._generation_timer.start()
        self._generation_thread.start()

    def _generate_texts(
        self,
        count: int,
        job_title: str,
        city: str,
        jd_text: str,
        salary: str,
    ) -> List[str]:
        from ..tools.greeting_text import GreetingTextGenerationService

        service = GreetingTextGenerationService.from_config(self.config_manager)
        if count <= 1:
            return [
                service.generate(
                    job_title,
                    city,
                    jd_text,
                    salary,
                )
            ]
        return service.generate_batch(
            job_title,
            city,
            jd_text,
            salary,
            count=count,
        )

    def _on_generation_done(self, count: int, texts: List[str]) -> None:
        if hasattr(self, "_generation_timer"):
            self._generation_timer.cancel()
        self._set_generating(False)
        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        if count > 1:
            for index, text in enumerate(texts, start=1):
                self.variant_combo.addItem("版本 {}".format(index), text)
        self.variant_combo.blockSignals(False)
        if texts:
            if count > 1:
                self.variant_combo.setCurrentIndex(0)
            self.message_text.setPlainText(texts[0])
        self.generate_status.setText("已生成 {} 个版本。".format(len(texts)))

    def _on_generation_failed(self, error: str) -> None:
        if hasattr(self, "_generation_timer"):
            self._generation_timer.cancel()
        self._set_generating(False)
        self.generate_status.setText("生成失败。")
        QMessageBox.warning(self, "生成失败", error or "未知错误")

    def _set_generating(self, generating: bool) -> None:
        self.generate_btn.setEnabled(not generating)
        self.generate_btn.setText("生成中..." if generating else "生成打招呼文本")
        self.generate_status.setText("正在生成打招呼文本，请稍候..." if generating else "生成完成。")

    def _select_variant(self, index: int) -> None:
        text = self.variant_combo.itemData(index)
        if text:
            self.message_text.setPlainText(str(text))

    def _validate_and_accept(self) -> None:
        path = self.excel_path.text().strip()
        if not path:
            QMessageBox.warning(self, "提示", "请先选择候选人 Excel。")
            return
        if not self.selected_recommendation_states():
            QMessageBox.warning(self, "提示", "请至少选择一个候选人资格。")
            return
        if not self._candidates:
            self._load_preview(path)
        if not self._candidates:
            filter_text = "、".join(
                RECOMMENDATION_LABELS[state]
                for state in self.selected_recommendation_states()
            )
            if self.gold_only_check.isChecked():
                filter_text += " + 金领"
            QMessageBox.warning(self, "提示", "没有符合 {} 条件的候选人。".format(filter_text))
            return
        if not self.dry_run_check.isChecked() and not self.verify_gold_check.isChecked():
            reply = QMessageBox.warning(
                self,
                "风险确认",
                "你关闭了页面金领复核，将完全信任 Excel 中的金领字段。\n"
                "如果 Excel 被编辑或数据过期，可能误向非金领候选人打招呼。\n\n"
                "是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.accept()

    def payload(self) -> Dict[str, object]:
        max_candidates = self.max_candidates_spin.value()
        effective_count = max_candidates if max_candidates > 0 else len(self._candidates)
        return {
            "excel_path": self.excel_path.text().strip(),
            "message": self.message_text.toPlainText().strip(),
            "candidate_count": effective_count,
            "candidate_names": [item.name for item in self._candidates[:effective_count]],
            "dry_run": self.dry_run_check.isChecked(),
            "verify_gold_on_page": self.verify_gold_check.isChecked(),
            "request_resume": self.request_resume_check.isChecked(),
            "gold_only": self.gold_only_check.isChecked(),
            "recommendation_states": self.selected_recommendation_states(),
            "delay_min": self.delay_min_spin.value(),
            "delay_max": self.delay_max_spin.value(),
            "max_retries": self.retry_spin.value(),
            "max_candidates": max_candidates,
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
        self.api_base_url.setPlaceholderText("https://opencode.ai/zen/go/v1")
        form.addRow("API Base URL", self.api_base_url)

        self.api_key = QLineEdit(config.api_key)
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText(
            "留空则使用环境变量 {}".format(
                config.api_key_env or "LIEPIN_AGENT_API_KEY"
            )
        )
        form.addRow("API Key", self.api_key)

        self.model_name = QLineEdit(config.model_name or "deepseek-v4-flash")
        form.addRow("模型名称", self.model_name)

        self.llm_provider = QComboBox()
        self.llm_provider.addItems(["openai", "anthropic"])
        index = self.llm_provider.findText(config.llm_provider or "openai")
        self.llm_provider.setCurrentIndex(max(0, index))
        form.addRow("API 格式", self.llm_provider)

        self.timeout = QSpinBox()
        self.timeout.setRange(10, 600)
        self.timeout.setValue(int(config.timeout or 120))
        form.addRow("API 超时秒数", self.timeout)

        # Backend LLM (Matcher)
        form.addRow(QLabel(""))
        backend_label = QLabel(
            "后端 LLM 配置（候选人匹配专用，留空则共用上方配置）"
        )
        backend_label.setObjectName("SessionInfo")
        form.addRow(backend_label)

        self.backend_api_base_url = QLineEdit(config.backend_api_base_url)
        self.backend_api_base_url.setPlaceholderText("https://opencode.ai/zen/go/v1")
        form.addRow("后端 API Base URL", self.backend_api_base_url)

        self.backend_api_key = QLineEdit(config.backend_api_key)
        self.backend_api_key.setEchoMode(QLineEdit.Password)
        self.backend_api_key.setPlaceholderText("留空则使用上方 API Key")
        form.addRow("后端 API Key", self.backend_api_key)

        self.backend_model_name = QLineEdit(config.backend_model_name)
        self.backend_model_name.setPlaceholderText("留空则使用上方模型名称")
        form.addRow("后端模型名称", self.backend_model_name)

        self.backend_llm_provider = QComboBox()
        self.backend_llm_provider.addItems(["openai", "anthropic"])
        index = self.backend_llm_provider.findText(config.backend_llm_provider or "openai")
        self.backend_llm_provider.setCurrentIndex(max(0, index))
        form.addRow("后端 API 格式", self.backend_llm_provider)

        # Chat LLM (JD 讨论顾问)
        form.addRow(QLabel(""))
        chat_label = QLabel(
            "讨论 LLM 配置（JD 讨论顾问专用，留空则共用上方配置）"
        )
        chat_label.setObjectName("SessionInfo")
        form.addRow(chat_label)

        self.chat_api_base_url = QLineEdit(config.chat_api_base_url)
        self.chat_api_base_url.setPlaceholderText("https://opencode.ai/zen/go/v1")
        form.addRow("讨论 API Base URL", self.chat_api_base_url)

        self.chat_api_key = QLineEdit(config.chat_api_key)
        self.chat_api_key.setEchoMode(QLineEdit.Password)
        self.chat_api_key.setPlaceholderText("留空则使用上方 API Key")
        form.addRow("讨论 API Key", self.chat_api_key)

        self.chat_model_name = QLineEdit(config.chat_model_name)
        self.chat_model_name.setPlaceholderText("留空则使用上方模型名称")
        form.addRow("讨论模型名称", self.chat_model_name)

        self.chat_llm_provider = QComboBox()
        self.chat_llm_provider.addItems(["openai", "anthropic"])
        index = self.chat_llm_provider.findText(config.chat_llm_provider or "openai")
        self.chat_llm_provider.setCurrentIndex(max(0, index))
        form.addRow("讨论 API 格式", self.chat_llm_provider)

        self.browser_channel = QComboBox()
        self.browser_channel.addItems(["msedge", "chrome", "chromium"])
        index = self.browser_channel.findText(
            config.liepin_browser_channel or "msedge"
        )
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

        self.greet_gold_only = QCheckBox("仅对金领候选人打招呼（手动/Excel 批量均生效）")
        self.greet_gold_only.setChecked(config.greet_gold_only)
        form.addRow(self.greet_gold_only)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        test_agent_btn = QPushButton("测试 Agent 模型")
        test_agent_btn.setObjectName("SecondaryBtn")
        test_matcher_btn = QPushButton("测试匹配模型")
        test_matcher_btn.setObjectName("SecondaryBtn")
        test_chat_btn = QPushButton("测试讨论模型")
        test_chat_btn.setObjectName("SecondaryBtn")
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("SecondaryBtn")
        save_btn = QPushButton("保存")
        test_agent_btn.clicked.connect(lambda: self._test_connection("default"))
        test_matcher_btn.clicked.connect(lambda: self._test_connection("backend"))
        test_chat_btn.clicked.connect(lambda: self._test_connection("chat"))
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self._save)
        buttons.addWidget(test_agent_btn)
        buttons.addWidget(test_matcher_btn)
        buttons.addWidget(test_chat_btn)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

    def _sync_config_from_inputs(self) -> None:
        self.config_manager.update(
            api_base_url=self.api_base_url.text().strip(),
            api_key=self.api_key.text().strip(),
            model_name=self.model_name.text().strip() or "deepseek-v4-flash",
            timeout=self.timeout.value(),
            backend_api_base_url=self.backend_api_base_url.text().strip(),
            backend_api_key=self.backend_api_key.text().strip(),
            backend_model_name=self.backend_model_name.text().strip(),
            chat_api_base_url=self.chat_api_base_url.text().strip(),
            chat_api_key=self.chat_api_key.text().strip(),
            chat_model_name=self.chat_model_name.text().strip(),
            liepin_browser_channel=self.browser_channel.currentText(),
            liepin_browser_profile_dir=self.profile_dir.text().strip()
            or "browser_profile/liepin",
            greeting_template=self.greeting_template.toPlainText().strip(),
            llm_provider=self.llm_provider.currentText(),
            backend_llm_provider=self.backend_llm_provider.currentText(),
            chat_llm_provider=self.chat_llm_provider.currentText(),
            greet_gold_only=self.greet_gold_only.isChecked(),
        )

    def _test_connection(self, profile: str) -> None:
        self._sync_config_from_inputs()
        result = self.config_manager.test_llm_connection(profile)
        title = "连接成功" if result.get("ok") else "连接失败"
        source = result.get("source") or {}
        message = "模型：{}\nBase URL：{}\n耗时：{} ms\n来源：{}".format(
            result.get("model") or "",
            result.get("api_base_url") or "",
            result.get("latency_ms") or 0,
            ", ".join("{}={}".format(k, v) for k, v in source.items()),
        )
        if result.get("error"):
            message += "\n错误：{}".format(result.get("error"))
        if result.get("sample"):
            message += "\n返回：{}".format(result.get("sample"))
        if result.get("ok"):
            QMessageBox.information(self, title, message)
        else:
            QMessageBox.warning(self, title, message)

    def _save(self) -> None:
        self._sync_config_from_inputs()
        if not self.config_manager.save_config():
            QMessageBox.warning(self, "保存失败", "配置文件写入失败")
            return
        self.accept()
