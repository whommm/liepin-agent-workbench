"""对话式打断：interrupt_for_dialog / end_dialog / 检查点状态保持 / 崩溃恢复。"""

import threading
import time

from liepin_agent.agent.brain import RuleBasedAgentBrain
from liepin_agent.agent.runtime import AgentRuntime
from liepin_agent.services.browser_queue import BrowserQueue
from liepin_agent.services.event_bus import EventBus
from liepin_agent.services.match_queue import MatchQueue
from liepin_agent.storage.sqlite_store import SQLiteStore
from liepin_agent.tools.rule_based_matcher import RuleBasedMatchService


def _make_runtime(store):
    return AgentRuntime(
        store=store,
        event_bus=EventBus(),
        browser_queue=BrowserQueue(),
        match_queue=MatchQueue(max_workers=1),
        liepin_tool=object(),
        matcher=RuleBasedMatchService(),
        agent_brain=RuleBasedAgentBrain(),
    )


def test_interrupt_requires_live_thread(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    runtime = _make_runtime(store)
    session_id = store.create_session(title="t", jd_text="jd")

    assert runtime.interrupt_for_dialog(session_id) is False
    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()


def test_interrupt_sets_user_dialog_and_end_dialog_resumes(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    runtime = _make_runtime(store)
    session_id = store.create_session(title="t", jd_text="jd")

    # 模拟一个存活的 runtime 线程
    alive = threading.Event()
    thread = threading.Thread(target=alive.wait, daemon=True)
    runtime._threads[session_id] = thread
    runtime._pause_events[session_id] = threading.Event()
    thread.start()

    assert runtime.interrupt_for_dialog(session_id) is True
    assert runtime._pause_events[session_id].is_set()
    assert store.get_session(session_id)["status"] == "user_dialog"

    runtime.end_dialog(session_id)
    assert not runtime._pause_events[session_id].is_set()
    assert store.get_session(session_id)["status"] == "running"

    alive.set()
    thread.join(timeout=2)
    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()


def test_control_flags_preserve_user_dialog_while_parked(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    runtime = _make_runtime(store)
    session_id = store.create_session(title="t", jd_text="jd")
    store.update_session_status(session_id, "user_dialog")

    cancel_event = threading.Event()
    pause_event = threading.Event()
    pause_event.set()
    runtime._pause_events[session_id] = pause_event
    finished = threading.Event()

    def _park():
        runtime._respect_control_flags(session_id, cancel_event, pause_event)
        finished.set()

    thread = threading.Thread(target=_park, daemon=True)
    thread.start()
    time.sleep(0.8)

    # 线程停在等待循环里，且状态没有被改写成 paused
    assert not finished.is_set()
    assert store.get_session(session_id)["status"] == "user_dialog"

    # 结束对话：清除 pause 后线程退出，状态恢复 running
    runtime.end_dialog(session_id)
    assert finished.wait(timeout=3)
    thread.join(timeout=2)
    assert store.get_session(session_id)["status"] == "running"
    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()


def test_recover_interrupted_sessions_covers_user_dialog(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id = store.create_session(title="t", jd_text="jd")
    store.update_session_status(session_id, "user_dialog")

    assert store.recover_interrupted_sessions() == 1
    assert store.get_session(session_id)["status"] == "paused"
