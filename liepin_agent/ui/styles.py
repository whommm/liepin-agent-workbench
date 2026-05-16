"""Stylesheet for the liepin workbench UI."""

MAIN_STYLESHEET = """
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
QPushButton:disabled {
    background: #93afd4;
    color: #e2e8f0;
}
QPushButton#DangerBtn {
    background: #dc2626;
}
QPushButton#DangerBtn:hover {
    background: #b91c1c;
}
QPushButton#SecondaryBtn {
    background: #64748b;
}
QPushButton#SecondaryBtn:hover {
    background: #475569;
}
QPushButton#SuccessBtn {
    background: #16a34a;
}
QPushButton#SuccessBtn:hover {
    background: #15803d;
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
QTabWidget::pane {
    border: 1px solid #d7e2f2;
    border-radius: 5px;
    background: #ffffff;
}
QTabBar::tab {
    background: #e8f0ff;
    color: #172033;
    padding: 5px 14px;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #2563eb;
    color: white;
    font-weight: 700;
}
QTabBar::tab:hover:!selected {
    background: #bfcfe8;
}
"""
