"""Stylesheet for the liepin workbench UI - Refined Warm Business Theme."""

# 精致暖色商务风主题
# 基于原有米黄基调，提升信息层级、统一间距、增强状态辨识度

MAIN_STYLESHEET = """
/* ============================================
   全局基础样式 - 米黄暖色调
   ============================================ */
QMainWindow {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #faf6f0,
        stop:0.5 #f5efe6,
        stop:1 #efe8dc
    );
}

QWidget {
    color: #3d3929;
    font-family: "Microsoft YaHei UI", "Segoe UI", "SF Pro Display";
    font-size: 13px;
}

QSplitter::handle {
    background: rgba(180, 160, 130, 0.25);
    width: 3px;
    height: 3px;
}

QSplitter::handle:hover {
    background: rgba(180, 140, 90, 0.55);
}

/* ============================================
   面板 - 温润奶油色，适度不透明提升可读性
   ============================================ */
QFrame#TopBar {
    background: rgba(255, 252, 247, 0.95);
    border: 1px solid rgba(200, 180, 150, 0.35);
    border-radius: 10px;
}

QFrame#Panel {
    background: rgba(255, 253, 250, 0.92);
    border: 1px solid rgba(200, 180, 150, 0.30);
    border-radius: 10px;
}

/* ============================================
   标题与标签
   ============================================ */
QLabel {
    color: #4a4637;
    background: transparent;
}

QLabel#TitleLabel {
    font-weight: 700;
    font-size: 17px;
    color: #2d2a1e;
    letter-spacing: 0.5px;
}

QLabel#SectionTitle {
    font-weight: 700;
    font-size: 13px;
    color: #5a5445;
    padding: 2px 0px 4px 0px;
    border-bottom: 2px solid rgba(212, 165, 116, 0.45);
    margin-bottom: 2px;
}

QLabel#SessionTitle {
    font-weight: 600;
    color: #3d3929;
    font-size: 13px;
}

QLabel#SessionInfo {
    color: #8a8070;
    font-size: 11px;
}

QLabel#HintLabel {
    color: #9a9080;
    font-size: 11px;
    padding: 2px 4px;
}

/* 状态小圆点 */
QLabel#StatusDot {
    min-width: 8px;
    max-width: 8px;
    min-height: 8px;
    max-height: 8px;
    border-radius: 4px;
    background: #b8a890;
}

/* 状态Badge */
QLabel#StatusBadge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    color: white;
    background: #b8a890;
}

/* 左侧状态色带 */
QFrame#StatusStripe {
    min-width: 3px;
    max-width: 3px;
    border-radius: 2px;
    background: #c4956a;
}

/* ============================================
   任务列表项 - 暖色卡片效果
   ============================================ */
QFrame#SessionItem {
    background: rgba(255, 250, 240, 0.80);
    border: 1px solid rgba(200, 180, 150, 0.30);
    border-radius: 8px;
    margin: 3px 2px;
}

QFrame#SessionItem:hover {
    background: rgba(255, 248, 235, 0.95);
    border: 1px solid rgba(200, 160, 100, 0.50);
}

/* ============================================
   主按钮 - 温暖渐变效果
   ============================================ */
QPushButton {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #d4a574,
        stop:1 #c4956a
    );
    color: white;
    border: none;
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 500;
    min-height: 28px;
}

QPushButton:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #e0b584,
        stop:1 #d4a574
    );
}

QPushButton:pressed {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #b8865a,
        stop:1 #a87850
    );
}

QPushButton:disabled {
    background: rgba(180, 170, 155, 0.45);
    color: #a09080;
}

/* 危险按钮 - 柔和红色 */
QPushButton#DangerBtn {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #d97b7b,
        stop:1 #c56a6a
    );
}

QPushButton#DangerBtn:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #e58c8c,
        stop:1 #d97b7b
    );
}

/* 次要按钮 - 米色玻璃风格 */
QPushButton#SecondaryBtn {
    background: rgba(160, 150, 130, 0.55);
    border: 1px solid rgba(180, 170, 150, 0.25);
    color: #ffffff;
}

QPushButton#SecondaryBtn:hover {
    background: rgba(175, 165, 145, 0.70);
    border: 1px solid rgba(180, 170, 150, 0.45);
}

/* 成功按钮 - 柔和绿色 */
QPushButton#SuccessBtn {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #7cb87c,
        stop:1 #6aa86a
    );
}

QPushButton#SuccessBtn:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #8cc88c,
        stop:1 #7cb87c
    );
}

/* 工具栏分隔线 */
QFrame#ToolbarSeparator {
    background: rgba(180, 160, 130, 0.35);
    min-width: 1px;
    max-width: 1px;
    min-height: 20px;
}

/* ============================================
   输入控件 - 奶油色输入框
   ============================================ */
QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: rgba(255, 253, 248, 0.85);
    border: 1px solid rgba(190, 175, 150, 0.40);
    border-radius: 6px;
    padding: 6px 10px;
    color: #3d3929;
    selection-background-color: rgba(212, 165, 116, 0.35);
}

QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid rgba(200, 160, 100, 0.70);
    background: rgba(255, 255, 252, 0.95);
}

QLineEdit::placeholder, QTextEdit::placeholder {
    color: #a09585;
}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 18px;
    border: none;
    background: transparent;
}

QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background: rgba(212, 165, 116, 0.25);
}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid #a09080;
    width: 0; height: 0;
}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #a09080;
    width: 0; height: 0;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #a09080;
    margin-right: 8px;
}

QComboBox QAbstractItemView {
    background: rgba(255, 252, 245, 0.98);
    border: 1px solid rgba(190, 175, 150, 0.50);
    border-radius: 6px;
    selection-background-color: rgba(212, 165, 116, 0.30);
    outline: none;
}

/* ============================================
   复选框 - 暖色风格
   ============================================ */
QCheckBox {
    spacing: 8px;
    color: #4a4637;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid rgba(190, 175, 150, 0.55);
    background: rgba(255, 253, 248, 0.85);
}

QCheckBox::indicator:hover {
    border: 1px solid rgba(200, 160, 100, 0.70);
    background: rgba(255, 250, 240, 0.95);
}

QCheckBox::indicator:checked {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #d4a574,
        stop:1 #c4956a
    );
    border: 1px solid rgba(180, 130, 90, 0.50);
    image: none;
}

QCheckBox::indicator:checked:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #e0b584,
        stop:1 #d4a574
    );
}

QCheckBox::indicator:disabled {
    background: rgba(220, 210, 195, 0.45);
    border: 1px solid rgba(190, 175, 150, 0.25);
}

/* ============================================
   列表控件 - 暖色列表
   ============================================ */
QListWidget {
    background: rgba(255, 252, 245, 0.55);
    border: 1px solid rgba(190, 175, 150, 0.30);
    border-radius: 8px;
    padding: 4px;
    outline: none;
}

QListWidget::item {
    background: transparent;
    border-radius: 6px;
    padding: 2px;
}

QListWidget::item:selected {
    background: rgba(212, 165, 116, 0.20);
    border: 1px solid rgba(200, 160, 100, 0.35);
}

QListWidget::item:hover:!selected {
    background: rgba(220, 200, 170, 0.18);
}

/* ============================================
   富文本浏览器 - 日志/时间线面板
   ============================================ */
QTextBrowser {
    background: rgba(255, 253, 248, 0.65);
    border: 1px solid rgba(190, 175, 150, 0.30);
    border-radius: 8px;
    padding: 8px;
    color: #4a4637;
}

/* ============================================
   表格控件 - 数据网格
   ============================================ */
QTableWidget {
    background: rgba(255, 252, 245, 0.55);
    border: 1px solid rgba(190, 175, 150, 0.30);
    border-radius: 8px;
    gridline-color: rgba(190, 175, 150, 0.18);
    outline: none;
    alternate-background-color: rgba(245, 240, 232, 0.45);
}

QTableWidget::item {
    padding: 4px 8px;
    border: none;
}

QTableWidget::item:selected {
    background: rgba(212, 165, 116, 0.28);
    color: #2d2a1e;
}

QTableWidget::item:hover:!selected {
    background: rgba(220, 200, 170, 0.12);
}

QHeaderView::section {
    background: rgba(240, 230, 215, 0.95);
    color: #5a5445;
    border: none;
    border-bottom: 1px solid rgba(190, 175, 150, 0.40);
    border-right: 1px solid rgba(190, 175, 150, 0.20);
    padding: 6px 6px;
    font-weight: 600;
    font-size: 12px;
}

QHeaderView::section:first {
    border-top-left-radius: 8px;
}

QHeaderView::section:last {
    border-top-right-radius: 8px;
    border-right: none;
}

/* ============================================
   标签页 - 紧凑暖色标签栏
   ============================================ */
QTabWidget::pane {
    background: rgba(255, 253, 248, 0.65);
    border: 1px solid rgba(190, 175, 150, 0.30);
    border-radius: 8px;
    top: -1px;
}

QTabBar::tab {
    background: rgba(240, 230, 215, 0.55);
    color: #7a7060;
    border: 1px solid rgba(190, 175, 150, 0.20);
    border-bottom: none;
    padding: 7px 14px;
    margin-right: 2px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 500;
    font-size: 12px;
}

QTabBar::tab:selected {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(212, 165, 116, 0.75),
        stop:1 rgba(212, 165, 116, 0.45)
    );
    color: #2d2a1e;
    border: 1px solid rgba(200, 160, 100, 0.50);
    border-bottom: none;
    font-weight: 600;
}

QTabBar::tab:hover:!selected {
    background: rgba(235, 220, 195, 0.75);
    color: #4a4637;
}

/* ============================================
   滚动条 - 极简暖色风格
   ============================================ */
QScrollBar:vertical {
    background: rgba(220, 210, 190, 0.25);
    width: 10px;
    border-radius: 5px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: rgba(180, 160, 130, 0.45);
    border-radius: 4px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(180, 150, 110, 0.65);
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background: rgba(220, 210, 190, 0.25);
    height: 10px;
    border-radius: 5px;
    margin: 2px;
}

QScrollBar::handle:horizontal {
    background: rgba(180, 160, 130, 0.45);
    border-radius: 4px;
    min-width: 30px;
}

QScrollBar::handle:horizontal:hover {
    background: rgba(180, 150, 110, 0.65);
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* ============================================
   消息框与对话框
   ============================================ */
QMessageBox, QDialog {
    background: rgba(255, 252, 245, 0.98);
    border: 1px solid rgba(190, 175, 150, 0.40);
    border-radius: 12px;
}

QMessageBox QLabel {
    color: #3d3929;
}

/* ============================================
   工具提示
   ============================================ */
QToolTip {
    background: rgba(255, 250, 240, 0.98);
    color: #3d3929;
    border: 1px solid rgba(200, 160, 100, 0.50);
    border-radius: 6px;
    padding: 6px 10px;
}

/* ============================================
   菜单
   ============================================ */
QMenu {
    background: rgba(255, 252, 245, 0.98);
    border: 1px solid rgba(190, 175, 150, 0.40);
    border-radius: 8px;
    padding: 4px;
}

QMenu::item {
    padding: 8px 24px;
    border-radius: 4px;
    color: #3d3929;
}

QMenu::item:selected {
    background: rgba(212, 165, 116, 0.30);
}

QMenu::separator {
    height: 1px;
    background: rgba(190, 175, 150, 0.40);
    margin: 4px 8px;
}

/* ============================================
   进度条
   ============================================ */
QProgressBar {
    background: rgba(240, 235, 225, 0.60);
    border: 1px solid rgba(190, 175, 150, 0.30);
    border-radius: 6px;
    text-align: center;
    color: #5a5445;
    font-size: 11px;
    font-weight: 600;
}

QProgressBar::chunk {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #d4a574,
        stop:1 #7cb87c
    );
    border-radius: 5px;
}

/* ============================================
   特殊状态指示器
   ============================================ */
QLabel#StatusRunning {
    color: #5a9a5a;
    font-weight: 600;
}

QLabel#StatusPaused {
    color: #c4956a;
    font-weight: 600;
}

QLabel#StatusError {
    color: #c56a6a;
    font-weight: 600;
}

/* ============================================
   高亮强调按钮 - 用于主要操作
   ============================================ */
QPushButton#AccentBtn {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #c49574,
        stop:0.5 #b88564,
        stop:1 #a87554
    );
    border: 1px solid rgba(180, 130, 90, 0.30);
}

QPushButton#AccentBtn:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #d4a584,
        stop:0.5 #c89574,
        stop:1 #b88564
    );
    border: 1px solid rgba(200, 150, 100, 0.50);
}

/* ============================================
   表单标签 - QFormLayout 中的标签
   ============================================ */
QLabel#FormSectionLabel {
    font-weight: 600;
    font-size: 12px;
    color: #6a6455;
    padding: 6px 0 2px 0;
}
"""
