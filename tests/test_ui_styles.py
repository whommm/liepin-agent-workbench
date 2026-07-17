import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QLabel

from liepin_agent.storage.sqlite_store import SQLiteStore
from liepin_agent.ui.main_window import MainWindow
from liepin_agent.ui.session_list_item import SessionListItemWidget
from liepin_agent.ui.styles import MAIN_STYLESHEET


def test_main_stylesheet_uses_flat_surfaces_and_small_radius_scale():
    assert "qlineargradient" not in MAIN_STYLESHEET
    assert "qradialgradient" not in MAIN_STYLESHEET
    assert "rgba(" not in MAIN_STYLESHEET

    radii = set(re.findall(r"border-radius:\s*(\d+)px", MAIN_STYLESHEET))
    assert radii <= {"3", "4", "6"}


def test_session_item_uses_one_status_marker():
    app = QApplication.instance() or QApplication([])
    item = SessionListItemWidget(
        {"id": "session-1", "title": "Search", "status": "running"},
        object(),
    )
    try:
        assert item.findChild(QFrame, "StatusStripe") is None
        assert item.findChild(QLabel, "StatusDot") is not None
    finally:
        item.close()
        app.processEvents()


def test_main_window_uses_compact_candidate_columns_and_inspector_tabs(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(SQLiteStore(str(tmp_path / "layout.db")), tmp_path)
    try:
        visible_columns = [
            column
            for column in range(window.candidate_table.columnCount())
            if not window.candidate_table.isColumnHidden(column)
        ]
        assert visible_columns == [0, 1, 2, 3, 8, 9, 10, 11, 12, 13, 14]
        assert [
            window.right_tabs.tabText(index)
            for index in range(window.right_tabs.count())
        ] == ["画像", "策略", "覆盖", "质量", "候选人", "日志"]
    finally:
        window.close()
        app.processEvents()
