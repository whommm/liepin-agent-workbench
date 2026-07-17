"""Shared stylesheet for the Liepin sourcing workbench."""


MAIN_STYLESHEET = """
/* Base */
QMainWindow {
    background: #eef0f3;
}

QWidget {
    color: #20242a;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}

QSplitter::handle {
    background: #d9dde4;
    width: 3px;
    height: 3px;
}

QSplitter::handle:hover {
    background: #aeb5c0;
}

/* Surfaces */
QFrame#TopBar,
QFrame#Panel {
    background: #ffffff;
    border: 1px solid #d9dde4;
    border-radius: 6px;
}

/* Type hierarchy */
QLabel {
    color: #343a43;
    background: transparent;
}

QLabel#TitleLabel {
    color: #171a1f;
    font-size: 18px;
    font-weight: 700;
}

QLabel#SectionTitle {
    color: #252a32;
    font-size: 15px;
    font-weight: 700;
    padding: 2px 0 6px 0;
}

QLabel#SessionTitle {
    color: #252a32;
    font-size: 13px;
    font-weight: 600;
}

QLabel#SessionInfo,
QLabel#HintLabel {
    color: #667085;
    font-size: 11px;
}

QLabel#HintLabel {
    padding: 2px 0;
}

QLabel#StatusDot {
    min-width: 7px;
    max-width: 7px;
    min-height: 7px;
    max-height: 7px;
    border-radius: 3px;
    background: #98a2b3;
}

QLabel#StatusBadge {
    color: #475467;
    background: #f2f4f7;
    border: 1px solid #d9dde4;
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 11px;
    font-weight: 600;
}

QFrame#StatusStripe {
    min-width: 0;
    max-width: 0;
    background: transparent;
    border: none;
}

/* Session rows are list rows, not nested cards. */
QFrame#SessionItem {
    background: transparent;
    border: none;
    border-bottom: 1px solid #e5e7eb;
    border-radius: 0;
    margin: 0;
}

QFrame#SessionItem:hover {
    background: #f7f8fa;
}

/* Commands */
QPushButton {
    color: #344054;
    background: #f2f4f7;
    border: 1px solid #d0d5dd;
    border-radius: 4px;
    padding: 6px 12px;
    min-height: 28px;
    font-weight: 600;
}

QPushButton:hover {
    color: #1d2939;
    background: #e9ecf1;
    border-color: #b8c0cc;
}

QPushButton:pressed,
QPushButton:checked {
    background: #dde1e7;
    border-color: #98a2b3;
}

QPushButton:disabled {
    color: #98a2b3;
    background: #f7f8fa;
    border-color: #e4e7ec;
}

QPushButton#AccentBtn,
QPushButton#SuccessBtn {
    color: #ffffff;
    background: #a96632;
    border-color: #a96632;
}

QPushButton#AccentBtn:hover,
QPushButton#SuccessBtn:hover {
    background: #8f5429;
    border-color: #8f5429;
}

QPushButton#AccentBtn:pressed,
QPushButton#AccentBtn:checked,
QPushButton#SuccessBtn:pressed,
QPushButton#SuccessBtn:checked {
    background: #764522;
    border-color: #764522;
}

QPushButton#SecondaryBtn {
    color: #344054;
    background: #ffffff;
    border-color: #d0d5dd;
}

QPushButton#SecondaryBtn:hover {
    background: #f7f8fa;
    border-color: #b8c0cc;
}

QPushButton#DangerBtn {
    color: #a33f3f;
    background: #ffffff;
    border-color: #d7a3a3;
}

QPushButton#DangerBtn:hover,
QPushButton#DangerBtn:checked {
    color: #8f3030;
    background: #fff4f4;
    border-color: #c76f6f;
}

QFrame#ToolbarSeparator {
    background: #d9dde4;
    min-width: 1px;
    max-width: 1px;
    min-height: 20px;
}

/* Form controls */
QLineEdit,
QTextEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {
    color: #252a32;
    background: #ffffff;
    border: 1px solid #cfd4dc;
    border-radius: 4px;
    padding: 6px 9px;
    selection-background-color: #d9e5ef;
}

QLineEdit:focus,
QTextEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus {
    background: #ffffff;
    border-color: #a96632;
}

QLineEdit::placeholder,
QTextEdit::placeholder {
    color: #98a2b3;
}

QSpinBox::up-button,
QSpinBox::down-button,
QDoubleSpinBox::up-button,
QDoubleSpinBox::down-button {
    width: 18px;
    border: none;
    background: transparent;
}

QSpinBox::up-button:hover,
QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover {
    background: #eef0f3;
}

QSpinBox::up-arrow,
QDoubleSpinBox::up-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid #667085;
    width: 0;
    height: 0;
}

QSpinBox::down-arrow,
QDoubleSpinBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #667085;
    width: 0;
    height: 0;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #667085;
    margin-right: 8px;
}

QComboBox QAbstractItemView {
    color: #252a32;
    background: #ffffff;
    border: 1px solid #cfd4dc;
    border-radius: 4px;
    selection-background-color: #edf1f5;
    outline: none;
}

QCheckBox {
    color: #344054;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    background: #ffffff;
    border: 1px solid #b8c0cc;
    border-radius: 3px;
}

QCheckBox::indicator:hover {
    border-color: #a96632;
}

QCheckBox::indicator:checked {
    background: #a96632;
    border-color: #a96632;
}

QCheckBox::indicator:disabled {
    background: #f2f4f7;
    border-color: #d9dde4;
}

/* Lists and document surfaces */
QListWidget {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
    outline: none;
}

QListWidget::item {
    background: transparent;
    border-radius: 0;
    padding: 2px;
}

QListWidget::item:selected {
    background: #e8edf2;
}

QListWidget::item:hover:!selected {
    background: #f4f6f8;
}

QTextBrowser {
    color: #343a43;
    background: #fafbfc;
    border: 1px solid #e1e4e8;
    border-radius: 4px;
    padding: 8px;
}

/* Data tables */
QTableWidget {
    color: #252a32;
    background: #ffffff;
    alternate-background-color: #f7f8fa;
    border: 1px solid #d9dde4;
    border-radius: 4px;
    gridline-color: #e6e9ee;
    outline: none;
}

QTableWidget::item {
    padding: 4px 8px;
    border: none;
}

QTableWidget::item:selected {
    color: #1d2939;
    background: #dce6ef;
}

QTableWidget::item:hover:!selected {
    background: #eef2f6;
}

QHeaderView::section {
    color: #475467;
    background: #f2f4f7;
    border: none;
    border-bottom: 1px solid #d9dde4;
    border-right: 1px solid #e1e4e8;
    padding: 7px 6px;
    font-size: 12px;
    font-weight: 600;
}

QHeaderView::section:last {
    border-right: none;
}

/* Tabs sit inside a parent surface without adding another card. */
QTabWidget::pane {
    background: transparent;
    border: none;
    border-top: 1px solid #d9dde4;
    border-radius: 0;
    top: -1px;
}

QTabBar::tab {
    color: #667085;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    padding: 8px 12px;
    margin-right: 2px;
    font-size: 12px;
    font-weight: 600;
}

QTabBar::tab:selected {
    color: #8f5429;
    background: transparent;
    border-bottom-color: #a96632;
}

QTabBar::tab:hover:!selected {
    color: #344054;
    background: #f7f8fa;
}

/* Scrollbars */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #c4c9d2;
    border-radius: 4px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background: #98a2b3;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}

QScrollBar::handle:horizontal {
    background: #c4c9d2;
    border-radius: 4px;
    min-width: 30px;
}

QScrollBar::handle:horizontal:hover {
    background: #98a2b3;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    width: 0;
}

/* Overlays */
QMessageBox,
QDialog {
    background: #f7f8fa;
}

QMessageBox QLabel {
    color: #252a32;
}

QToolTip {
    color: #ffffff;
    background: #252a32;
    border: 1px solid #252a32;
    border-radius: 4px;
    padding: 6px 8px;
}

QMenu {
    color: #252a32;
    background: #ffffff;
    border: 1px solid #cfd4dc;
    border-radius: 4px;
    padding: 4px;
}

QMenu::item {
    padding: 7px 22px;
    border-radius: 3px;
}

QMenu::item:selected {
    background: #edf1f5;
}

QMenu::separator {
    height: 1px;
    background: #e1e4e8;
    margin: 4px 8px;
}

QProgressBar {
    color: #475467;
    background: #e9ecf1;
    border: none;
    border-radius: 4px;
    text-align: center;
    font-size: 11px;
    font-weight: 600;
}

QProgressBar::chunk {
    background: #a96632;
    border-radius: 4px;
}

/* Status text carries the state before color does. */
QLabel#StatusRunning {
    color: #2f6b4f;
    font-weight: 600;
}

QLabel#StatusPaused {
    color: #8f5429;
    font-weight: 600;
}

QLabel#StatusError {
    color: #a33f3f;
    font-weight: 600;
}

QLabel#FormSectionLabel {
    color: #475467;
    font-size: 12px;
    font-weight: 600;
    padding: 8px 0 2px 0;
}
"""
