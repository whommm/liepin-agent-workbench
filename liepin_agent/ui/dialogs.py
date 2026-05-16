"""Reusable dialog classes for the liepin workbench UI."""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
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
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("SecondaryBtn")
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
