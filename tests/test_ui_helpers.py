import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from liepin_agent.domain.models import CandidateDetail, CandidateSummary, SearchPlan
from liepin_agent.storage.sqlite_store import SQLiteStore
from liepin_agent.ui.main_window import MainWindow


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
        assert window.manual_greeting_btn.text() == "批量打招呼(2)"
        assert window.manual_greeting_btn.isEnabled()
    finally:
        window.close()
        app.processEvents()
