"""Stylesheet for the liepin workbench UI - Warm Glassmorphism Theme."""

# 米黄暖色玻璃拟态主题
# 温暖舒适的奶油色背景 + 毛玻璃半透明面板 + 柔和渐变

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
    background: rgba(180, 160, 130, 0.2);
    width: 2px;
    height: 2px;
}

QSplitter::handle:hover {
    background: rgba(180, 140, 90, 0.5);
}

/* ============================================
   毛玻璃面板 - 奶油色玻璃效果
   ============================================ */
QFrame#TopBar {
    background: rgba(255, 252, 247, 0.85);
    border: 1px solid rgba(200, 180, 150, 0.3);
    border-radius: 12px;
}

QFrame#Panel {
    background: rgba(255, 253, 250, 0.75);
    border: 1px solid rgba(200, 180, 150, 0.25);
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

QLabel#SessionTitle {
    font-weight: 600;
    color: #3d3929;
    font-size: 13px;
}

QLabel#SessionInfo {
    color: #8a8070;
    font-size: 11px;
}

/* ============================================
   任务列表项 - 暖色卡片效果
   ============================================ */
QFrame#SessionItem {
    background: rgba(255, 250, 240, 0.7);
    border: 1px solid rgba(200, 180, 150, 0.3);
    border-radius: 8px;
    margin: 4px 2px;
}

QFrame#SessionItem:hover {
    background: rgba(255, 248, 235, 0.9);
    border: 1px solid rgba(200, 160, 100, 0.5);
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
    padding: 7px 14px;
    font-weight: 500;
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
    background: rgba(180, 170, 155, 0.5);
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
    background: rgba(160, 150, 130, 0.6);
    border: 1px solid rgba(180, 170, 150, 0.3);
    color: #ffffff;
}

QPushButton#SecondaryBtn:hover {
    background: rgba(175, 165, 145, 0.75);
    border: 1px solid rgba(180, 170, 150, 0.5);
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

/* ============================================
   输入控件 - 奶油色输入框
   ============================================ */
QLineEdit, QTextEdit, QSpinBox, QComboBox {
    background: rgba(255, 253, 248, 0.8);
    border: 1px solid rgba(190, 175, 150, 0.4);
    border-radius: 6px;
    padding: 6px 10px;
    color: #3d3929;
    selection-background-color: rgba(212, 165, 116, 0.4);
}

QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid rgba(200, 160, 100, 0.7);
    background: rgba(255, 255, 252, 0.95);
}

QLineEdit::placeholder, QTextEdit::placeholder {
    color: #a09585;
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
    border: 1px solid rgba(190, 175, 150, 0.5);
    border-radius: 6px;
    selection-background-color: rgba(212, 165, 116, 0.35);
    outline: none;
}

/* ============================================
   列表控件 - 暖色列表
   ============================================ */
QListWidget {
    background: rgba(255, 252, 245, 0.5);
    border: 1px solid rgba(190, 175, 150, 0.3);
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
    background: rgba(212, 165, 116, 0.25);
    border: 1px solid rgba(200, 160, 100, 0.4);
}

QListWidget::item:hover:!selected {
    background: rgba(220, 200, 170, 0.2);
}

/* ============================================
   富文本浏览器 - 日志/时间线面板
   ============================================ */
QTextBrowser {
    background: rgba(255, 253, 248, 0.6);
    border: 1px solid rgba(190, 175, 150, 0.3);
    border-radius: 8px;
    padding: 8px;
    color: #4a4637;
}

/* ============================================
   表格控件 - 数据网格
   ============================================ */
QTableWidget {
    background: rgba(255, 252, 245, 0.5);
    border: 1px solid rgba(190, 175, 150, 0.3);
    border-radius: 8px;
    gridline-color: rgba(190, 175, 150, 0.2);
    outline: none;
}

QTableWidget::item {
    padding: 6px 8px;
    border: none;
}

QTableWidget::item:selected {
    background: rgba(212, 165, 116, 0.3);
    color: #2d2a1e;
}

QTableWidget::item:hover:!selected {
    background: rgba(220, 200, 170, 0.15);
}

QHeaderView::section {
    background: rgba(240, 230, 215, 0.9);
    color: #5a5445;
    border: none;
    border-bottom: 1px solid rgba(190, 175, 150, 0.4);
    border-right: 1px solid rgba(190, 175, 150, 0.2);
    padding: 8px 6px;
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
   标签页 - 暖色标签栏
   ============================================ */
QTabWidget::pane {
    background: rgba(255, 253, 248, 0.6);
    border: 1px solid rgba(190, 175, 150, 0.3);
    border-radius: 8px;
    top: -1px;
}

QTabBar::tab {
    background: rgba(240, 230, 215, 0.5);
    color: #7a7060;
    border: 1px solid rgba(190, 175, 150, 0.2);
    border-bottom: none;
    padding: 8px 18px;
    margin-right: 2px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 500;
}

QTabBar::tab:selected {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(212, 165, 116, 0.7),
        stop:1 rgba(212, 165, 116, 0.4)
    );
    color: #2d2a1e;
    border: 1px solid rgba(200, 160, 100, 0.5);
    border-bottom: none;
    font-weight: 600;
}

QTabBar::tab:hover:!selected {
    background: rgba(235, 220, 195, 0.7);
    color: #4a4637;
}

/* ============================================
   滚动条 - 极简暖色风格
   ============================================ */
QScrollBar:vertical {
    background: rgba(220, 210, 190, 0.2);
    width: 10px;
    border-radius: 5px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: rgba(180, 160, 130, 0.4);
    border-radius: 4px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(180, 150, 110, 0.6);
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background: rgba(220, 210, 190, 0.2);
    height: 10px;
    border-radius: 5px;
    margin: 2px;
}

QScrollBar::handle:horizontal {
    background: rgba(180, 160, 130, 0.4);
    border-radius: 4px;
    min-width: 30px;
}

QScrollBar::handle:horizontal:hover {
    background: rgba(180, 150, 110, 0.6);
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* ============================================
   消息框与对话框
   ============================================ */
QMessageBox, QDialog {
    background: rgba(255, 252, 245, 0.98);
    border: 1px solid rgba(190, 175, 150, 0.4);
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
    border: 1px solid rgba(200, 160, 100, 0.5);
    border-radius: 6px;
    padding: 6px 10px;
}

/* ============================================
   菜单
   ============================================ */
QMenu {
    background: rgba(255, 252, 245, 0.98);
    border: 1px solid rgba(190, 175, 150, 0.4);
    border-radius: 8px;
    padding: 4px;
}

QMenu::item {
    padding: 8px 24px;
    border-radius: 4px;
    color: #3d3929;
}

QMenu::item:selected {
    background: rgba(212, 165, 116, 0.35);
}

QMenu::separator {
    height: 1px;
    background: rgba(190, 175, 150, 0.4);
    margin: 4px 8px;
}

/* ============================================
   进度条
   ============================================ */
QProgressBar {
    background: rgba(240, 235, 225, 0.6);
    border: 1px solid rgba(190, 175, 150, 0.3);
    border-radius: 6px;
    text-align: center;
    color: #3d3929;
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
    border: 1px solid rgba(180, 130, 90, 0.3);
}

QPushButton#AccentBtn:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #d4a584,
        stop:0.5 #c89574,
        stop:1 #b88564
    );
    border: 1px solid rgba(200, 150, 100, 0.5);
}
"""
