"""Reusable dialog classes for the liepin workbench UI."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from ..agent.planner import Planner
from ..core.config import ConfigManager
from ..tools.excel_greeting import ExcelGreetingService


class _GreetingGenerationSignals(QObject):
    done = Signal(int, list)
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
        info = QLabel("寻访基准（匹配词与岗位要求）已生成，请确认后开始搜索。")
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
        cancel_btn.setObjectName("SecondaryBtn")
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
        self.resize(760, 620)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("导入候选人 Excel，仅处理匹配档位 A/B、金领=是、未打过招呼、有猎聘简历链接的候选人。")
        )

        file_row = QHBoxLayout()
        self.excel_path = QLineEdit()
        self.excel_path.setPlaceholderText("选择导出的候选人 Excel")
        browse_btn = QPushButton("选择 Excel")
        browse_btn.setObjectName("SecondaryBtn")
        browse_btn.clicked.connect(self._pick_excel)
        file_row.addWidget(self.excel_path, 1)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        self.summary_label = QLabel("未导入 Excel。")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.dry_run_check = QCheckBox("仅预览 dry-run，不实际发送打招呼")
        self.dry_run_check.setChecked(True)
        self.verify_gold_check = QCheckBox("发送前重新打开页面复核金领状态")
        self.verify_gold_check.setChecked(True)
        self.verify_gold_check.setToolTip("建议保持开启，避免 Excel 被编辑或数据过期后误发。")
        layout.addWidget(self.dry_run_check)
        layout.addWidget(self.verify_gold_check)

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
        self.generate_batch_btn = QPushButton("生成 5 个版本")
        self.generate_batch_btn.setObjectName("SecondaryBtn")
        self.generate_batch_btn.clicked.connect(self._generate_batch)
        generate_row.addWidget(self.generate_btn)
        generate_row.addWidget(self.generate_batch_btn)
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
            self._candidates = ExcelGreetingService.load_greetable_candidates(path)
        except Exception as exc:
            self._candidates = []
            QMessageBox.warning(self, "读取失败", str(exc))
            return
        names = "、".join(item.name for item in self._candidates[:10])
        if len(self._candidates) > 10:
            names += " 等"
        self.summary_label.setText(
            "可处理候选人：{} 位（筛选：A/B + 金领 + 未打过 + 猎聘详情链接）。".format(
                len(self._candidates)
            )
        )
        self.preview.setPlainText(names or "没有符合条件的候选人。")

    def set_job_defaults(self, title: str, jd_text: str, city: str = "", salary: str = "") -> None:
        self.job_title.setText(title or "")
        self.city.setText(city or "")
        self.salary.setText(salary or "")
        self.jd_text.setPlainText(jd_text or "")

    def _generate_one(self) -> None:
        self._start_generation(count=1)

    def _generate_batch(self) -> None:
        self._start_generation(count=5)

    def _start_generation(self, count: int) -> None:
        self._set_generating(True)
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
                self._generation_signals.done.emit(count, texts)
            except Exception as exc:
                self._generation_signals.failed.emit(str(exc))

        threading.Thread(target=_run, daemon=True).start()

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
        self._set_generating(False)
        self.generate_status.setText("生成失败。")
        QMessageBox.warning(self, "生成失败", error or "未知错误")

    def _set_generating(self, generating: bool) -> None:
        self.generate_btn.setEnabled(not generating)
        self.generate_batch_btn.setEnabled(not generating)
        self.generate_btn.setText("生成中..." if generating else "生成打招呼文本")
        self.generate_batch_btn.setText("生成中..." if generating else "生成 5 个版本")
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
        if not self._candidates:
            self._load_preview(path)
        if not self._candidates:
            QMessageBox.warning(self, "提示", "没有符合 A/B + 金领 条件的候选人。")
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
        return {
            "excel_path": self.excel_path.text().strip(),
            "message": self.message_text.toPlainText().strip(),
            "candidate_count": len(self._candidates),
            "candidate_names": [item.name for item in self._candidates],
            "dry_run": self.dry_run_check.isChecked(),
            "verify_gold_on_page": self.verify_gold_check.isChecked(),
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
            "留空则使用环境变量 {}".format(
                config.api_key_env or "LIEPIN_AGENT_API_KEY"
            )
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
        backend_label = QLabel(
            "后端 LLM 配置（候选人匹配专用，留空则共用上方配置）"
        )
        backend_label.setObjectName("SessionInfo")
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

        layout.addLayout(form)

        buttons = QHBoxLayout()
        test_agent_btn = QPushButton("测试 Agent 模型")
        test_agent_btn.setObjectName("SecondaryBtn")
        test_matcher_btn = QPushButton("测试匹配模型")
        test_matcher_btn.setObjectName("SecondaryBtn")
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("SecondaryBtn")
        save_btn = QPushButton("保存")
        test_agent_btn.clicked.connect(lambda: self._test_connection("default"))
        test_matcher_btn.clicked.connect(lambda: self._test_connection("backend"))
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self._save)
        buttons.addWidget(test_agent_btn)
        buttons.addWidget(test_matcher_btn)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

    def _sync_config_from_inputs(self) -> None:
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
