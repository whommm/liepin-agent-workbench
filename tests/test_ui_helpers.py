import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from liepin_agent.domain.models import CandidateDetail, CandidateSummary, SearchPlan
from liepin_agent.storage.sqlite_store import SQLiteStore
from liepin_agent.ui.main_window import MainWindow
from liepin_agent.ui.dialogs import GreetingScopeDialog
from liepin_agent.domain.recommendation import (
    EXPLICIT_MISMATCH,
    HIGH_POTENTIAL_VERIFY,
    PRIORITY_CONTACT,
)


def test_candidate_profile_url_prefers_captured_detail_payload():
    candidate = {"profile_url": "https://www.liepin.com/card-link"}
    detail = {
        "raw_payload_json": json.dumps(
            {
                "raw_payload_json": json.dumps(
                    {"profile_url": "https://www.liepin.com/nested-detail-link"}
                ),
                "profile_url": "https://www.liepin.com/captured-detail-link",
            }
        )
    }

    assert (
        MainWindow._candidate_profile_url(candidate, detail)
        == "https://www.liepin.com/captured-detail-link"
    )


def test_candidate_profile_url_falls_back_to_candidate_card_link():
    candidate = {"profile_url": "https://www.liepin.com/card-link"}
    detail = {"raw_payload_json": "{}"}

    assert (
        MainWindow._candidate_profile_url(candidate, detail)
        == "https://www.liepin.com/card-link"
    )


def test_candidate_table_supports_multi_selection_for_manual_greeting(tmp_path):
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id = store.create_session(title="测试任务", jd_text="JD")
    round_id = store.create_round(session_id, 1, SearchPlan(query="产品经理"))
    for index in range(3):
        candidate_id = store.save_candidate_summary(
            CandidateSummary(
                id="c{}".format(index),
                session_id=session_id,
                round_id=round_id,
                profile_url="https://www.liepin.com/card-{}".format(index),
                name="候选人{}".format(index),
                current_title="产品经理",
                result_index=index,
            )
        )
        store.save_candidate_detail(
            CandidateDetail(
                candidate_id=candidate_id,
                resume_text="文创 产品",
                raw_payload={
                    "profile_url": "https://www.liepin.com/detail-{}".format(index)
                },
                capture_status="success",
            )
        )

    window = MainWindow(store, tmp_path)
    try:
        window.selected_session_id = session_id
        window.refresh_all()
        selection_model = window.candidate_table.selectionModel()
        for row in (0, 1):
            selection_model.select(
                window.candidate_table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
        window._on_candidate_selected()

        assert window._selected_candidate_ids() == ["c0", "c1"]
        assert window.right_tabs.currentIndex() == window.detail_tab_index
        assert "候选人0" in window.detail_view.toPlainText()
        assert "文创 产品" in window.detail_view.toPlainText()
        assert window.manual_greeting_btn.text() == "批量打招呼(2)"
        assert window.manual_greeting_btn.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_greeting_scope_defaults_and_blocks_explicit_mismatch():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dialog = GreetingScopeDialog(
        {
            PRIORITY_CONTACT: 3,
            HIGH_POTENTIAL_VERIFY: 2,
            EXPLICIT_MISMATCH: 1,
        }
    )
    try:
        assert dialog.selected_states() == [
            PRIORITY_CONTACT,
            HIGH_POTENTIAL_VERIFY,
        ]
        assert not dialog.state_checks[EXPLICIT_MISMATCH].isEnabled()
        assert "3 人" in dialog.state_checks[PRIORITY_CONTACT].text()
    finally:
        dialog.close()
        app.processEvents()


def test_manual_greeting_scope_selects_matching_candidate_rows(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog, QTableWidgetItem

    import liepin_agent.ui.main_window as main_window_module
    from liepin_agent.domain.recommendation import TRANSFERABLE_EXPLORE

    class AcceptedScopeDialog:
        def __init__(self, state_counts, parent=None):
            assert state_counts[PRIORITY_CONTACT] == 1
            assert state_counts[TRANSFERABLE_EXPLORE] == 1

        def exec(self):
            return QDialog.Accepted

        def selected_states(self):
            return [PRIORITY_CONTACT, TRANSFERABLE_EXPLORE]

    monkeypatch.setattr(main_window_module, "GreetingScopeDialog", AcceptedScopeDialog)
    app = QApplication.instance() or QApplication([])
    store = SQLiteStore(str(tmp_path / "scope-select.db"))
    window = MainWindow(store, tmp_path)
    try:
        rows = [
            ("priority", "优先沟通"),
            ("potential", "高潜待确认"),
            ("transfer", "可迁移探索"),
        ]
        window.candidate_table.setRowCount(len(rows))
        for row, (candidate_id, state_label) in enumerate(rows):
            name_item = QTableWidgetItem(candidate_id)
            name_item.setData(Qt.UserRole, candidate_id)
            window.candidate_table.setItem(row, 0, name_item)
            window.candidate_table.setItem(row, 8, QTableWidgetItem(state_label))

        window._select_candidates_by_greeting_scope()

        assert window._selected_candidate_ids() == ["priority", "transfer"]
    finally:
        window.close()
        app.processEvents()


def test_refresh_reuses_session_and_event_snapshots(tmp_path):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    store = SQLiteStore(str(tmp_path / "refresh.db"))
    session_id = store.create_session(title="刷新测试", jd_text="JD")
    store.create_round(session_id, 1, SearchPlan(query="产品经理"))
    window = MainWindow(store, tmp_path)
    calls = {name: 0 for name in ("list_sessions", "get_session", "list_events", "list_rounds")}

    for name in calls:
        original = getattr(store, name)

        def counted(*args, _name=name, _original=original, **kwargs):
            calls[_name] += 1
            return _original(*args, **kwargs)

        setattr(store, name, counted)

    try:
        window.selected_session_id = session_id
        window.refresh_all()

        assert calls == {
            "list_sessions": 1,
            "get_session": 1,
            "list_events": 1,
            "list_rounds": 1,
        }
    finally:
        window.close()
        app.processEvents()


def test_runtime_refresh_is_coalesced_and_throttled(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import liepin_agent.ui.main_window as main_window_module

    app = QApplication.instance() or QApplication([])
    store = SQLiteStore(str(tmp_path / "runtime-refresh.db"))
    window = MainWindow(store, tmp_path)
    try:
        for _ in range(100):
            window._queue_runtime_event(
                "event_added",
                {"session_id": "session-1", "event_type": "detail_fetched"},
            )

        assert len(window._runtime_events) == 0
        assert window._dirty is True
        assert window._runtime_dirty is True

        refresh_modes = []
        def record_refresh(lightweight=False):
            refresh_modes.append(lightweight)
            window._dirty = False

        monkeypatch.setattr(
            window,
            "refresh_all",
            record_refresh,
        )
        monkeypatch.setattr(window, "_check_queue_advance", lambda: None)
        window._last_refresh_monotonic = 100.0
        monkeypatch.setattr(main_window_module.time, "monotonic", lambda: 100.5)

        window._refresh_if_dirty()
        assert refresh_modes == []
        assert window._dirty is True

        monkeypatch.setattr(main_window_module.time, "monotonic", lambda: 101.1)
        window._refresh_if_dirty()
        assert refresh_modes == [True]
        assert window._dirty is False
        assert window._runtime_dirty is False
    finally:
        window.close()
        app.processEvents()


def test_runtime_completion_forces_full_refresh(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import liepin_agent.ui.main_window as main_window_module

    app = QApplication.instance() or QApplication([])
    store = SQLiteStore(str(tmp_path / "runtime-complete-refresh.db"))
    window = MainWindow(store, tmp_path)
    try:
        refresh_modes = []
        monkeypatch.setattr(
            window,
            "refresh_all",
            lambda lightweight=False: refresh_modes.append(lightweight),
        )
        monkeypatch.setattr(window, "_check_queue_advance", lambda: None)
        window._last_refresh_monotonic = 100.0
        monkeypatch.setattr(main_window_module.time, "monotonic", lambda: 100.1)

        window._queue_runtime_event(
            "event_added",
            {"session_id": "session-1", "event_type": "session_completed"},
        )
        window._refresh_if_dirty()

        assert refresh_modes == [False]
    finally:
        window.close()
        app.processEvents()


def test_evidence_source_labels_are_explicit_and_backward_compatible():
    assert MainWindow._evidence_source_label(
        {"source_type": "direct", "grounding_status": "exact"}
    ) == "原文证据"
    assert MainWindow._evidence_source_label(
        {"source_type": "direct", "grounding_status": "model_summary"}
    ) == "模型概括"
    assert MainWindow._evidence_source_label(
        {"source_type": "direct"}
    ) == "匹配证据"
    assert MainWindow._evidence_source_label(
        {"source_type": "inferred"}
    ) == "推断"


def test_greeting_context_from_candidate_row():
    context = MainWindow._greeting_context_from_row(
        {
            "matched_evidence": [{"evidence": "负责天然气客户开发"}],
            "questions_to_verify": ["是否销售过压缩机设备？"],
            "match_risks": "设备深度待确认",
        }
    )

    assert context["matched_evidence"] == [{"evidence": "负责天然气客户开发"}]
    assert context["questions_to_verify"] == ["是否销售过压缩机设备？"]
    assert context["match_risks"] == "设备深度待确认"


def test_candidate_feedback_controls_save_batch_labels(tmp_path):
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    store = SQLiteStore(str(tmp_path / "feedback-ui.db"))
    session_id = store.create_session(title="反馈测试", jd_text="JD")
    round_id = store.create_round(session_id, 1, SearchPlan(query="产品经理"))
    for index in range(2):
        candidate_id = store.save_candidate_summary(
            CandidateSummary(
                id="feedback-c{}".format(index),
                session_id=session_id,
                round_id=round_id,
                profile_url="https://example.com/feedback-{}".format(index),
                name="候选人{}".format(index),
                current_title="产品经理",
                result_index=index,
            )
        )
        store.save_candidate_detail(
            CandidateDetail(
                candidate_id=candidate_id,
                resume_text="产品规划和用户研究",
                capture_status="success",
            )
        )
    store.save_candidate_summary(
        CandidateSummary(
            id="card-only",
            session_id=session_id,
            round_id=round_id,
            profile_url="https://www.liepin.com/card-only",
            name="未抓详情候选人",
            current_title="产品经理",
            result_index=99,
        )
    )

    window = MainWindow(store, tmp_path)
    try:
        window.selected_session_id = session_id
        window.refresh_all()
        assert window.candidate_table.rowCount() == 2
        assert window.candidate_table_title.text() == "候选人详情 (2)"
        selection_model = window.candidate_table.selectionModel()
        for row in (0, 1):
            selection_model.select(
                window.candidate_table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
        window._on_candidate_selected()
        window._select_feedback_label("not_suitable")
        reason_index = window.feedback_reason_combo.findData("行业不匹配")
        window.feedback_reason_combo.setCurrentIndex(reason_index)
        window.feedback_note_input.setText("批量复核")
        window._save_candidate_feedback()

        for candidate_id in ("feedback-c0", "feedback-c1"):
            feedback = store.get_latest_candidate_feedback(candidate_id)
            assert feedback["feedback_label"] == "not_suitable"
            assert feedback["reason_codes"] == ["行业不匹配"]
            assert feedback["note"] == "批量复核"
    finally:
        window.close()
        app.processEvents()


def test_structured_profile_editor_creates_confirmed_version(tmp_path):
    from PySide6.QtWidgets import QApplication, QTableWidgetItem

    from liepin_agent.domain.job_profile import normalize_job_profile

    app = QApplication.instance() or QApplication([])
    store = SQLiteStore(str(tmp_path / "profile-ui.db"))
    session_id = store.create_session(title="画像测试", jd_text="工业设备销售")
    criteria_id = store.create_criteria_version(
        session_id,
        "设备销售",
        "有工业设备销售经验",
        created_by="human",
    )
    items, personas = normalize_job_profile(
        {
            "requirements_text": "有工业设备销售经验",
            "criteria_items": [
                {
                    "type": "must",
                    "criterion": "有工业设备销售经验",
                    "weight": 0.9,
                    "search_aliases": ["设备销售"],
                }
            ],
            "personas": [
                {
                    "name": "直接对口",
                    "titles": ["销售经理"],
                    "skills": ["设备销售"],
                }
            ],
        }
    )
    store.replace_job_profile(criteria_id, items, personas)
    store.confirm_criteria_version(criteria_id)

    window = MainWindow(store, tmp_path)
    try:
        window.selected_session_id = session_id
        window.refresh_all()
        assert window.criteria_items_table.rowCount() == 1
        assert window.personas_table.rowCount() == 1
        window.criteria_items_table.setItem(
            0, 1, QTableWidgetItem("有压缩机设备销售经验")
        )
        window.confirm_current_criteria()

        latest = store.get_latest_criteria_version(session_id, "confirmed")
        assert latest["id"] != criteria_id
        assert latest["criteria_items"][0]["criterion_text"] == "有压缩机设备销售经验"
        assert latest["criteria_items"][0]["human_confirmed"] is True
    finally:
        window.close()
        app.processEvents()
