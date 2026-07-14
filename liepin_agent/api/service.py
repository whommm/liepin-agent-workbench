"""Non-Qt workbench service used by the HTTP API."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from ..agent.brain import LLMAgentBrain
from ..agent.runtime import AgentRuntime
from ..core.config import ConfigManager
from ..domain.states import SessionStatus
from ..services.event_bus import EventBus
from ..storage.sqlite_store import SQLiteStore
from ..tools.exporter import ExportService
from ..tools.real_liepin import RealLiepinTool
from ..tools.real_matcher import RealMatchService
from .events import EventBroadcaster
from .schemas import ConfigUpdateRequest, CriteriaConfirmRequest, PoolReorderRequest, SessionCreateRequest


class ApiError(Exception):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class WorkbenchService:
    def __init__(self, workspace_root: str | Path | None = None):
        self.workspace_root = self._resolve_workspace_root(workspace_root)
        self.db_path = self.workspace_root / "liepin_agent_workbench.db"
        self.config_manager = ConfigManager(str(self.workspace_root / "config.json"))
        self.store = SQLiteStore(str(self.db_path))
        self.store.recover_interrupted_sessions()
        self.event_bus = EventBus()
        self.events = EventBroadcaster()
        self.event_bus.subscribe(self._on_runtime_event)
        self.runtime = self._build_runtime()

    def _build_runtime(self) -> AgentRuntime:
        return AgentRuntime(
            store=self.store,
            event_bus=self.event_bus,
            liepin_tool=RealLiepinTool(self.config_manager),
            matcher=RealMatchService.from_config(self.config_manager),
            agent_brain=LLMAgentBrain.from_config(self.config_manager),
        )

    def _on_runtime_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        self.events.publish(event_type, payload)

    @staticmethod
    def _resolve_workspace_root(workspace_root: str | Path | None) -> Path:
        if workspace_root:
            return Path(workspace_root).resolve()
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parents[2]

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "app": "liepin-agent-workbench",
            "backend": "python",
            "version": "0.1.0",
            "workspace_root": str(self.workspace_root),
            "db_path": str(self.db_path),
            "active_session_ids": self.runtime.active_session_ids(),
        }

    def list_sessions(self) -> list[Dict[str, Any]]:
        rows = self.store.list_sessions()
        active = set(self.runtime.active_session_ids())
        for row in rows:
            row["runtime_active"] = row.get("id") in active
        return rows

    def create_session(self, payload: SessionCreateRequest) -> Dict[str, Any]:
        session_id = self.store.create_session(
            title=payload.title,
            jd_text=payload.jd_text,
            user_notes=payload.user_notes,
            mode=payload.mode,
            max_rounds=payload.max_rounds,
            max_detail_fetches=payload.max_detail_fetches,
            max_runtime_minutes=payload.max_runtime_minutes,
            target_ab_count=payload.target_ab_count,
        )
        if payload.add_to_pool and hasattr(self.store, "add_session_to_pool"):
            self.store.add_session_to_pool(session_id)
        self.event_bus.publish("session_updated", {"session_id": session_id})
        return {"ok": True, "session_id": session_id}

    def get_session(self, session_id: str) -> Dict[str, Any]:
        session = self.store.get_session(session_id)
        if not session:
            raise ApiError("session_not_found", "未找到该寻访任务。")
        session["runtime_active"] = self.runtime.is_active(session_id)
        session["latest_criteria"] = self.store.get_latest_criteria_version(session_id)
        session["confirmed_criteria"] = self.store.get_latest_criteria_version(session_id, "confirmed")
        return session

    def delete_session(self, session_id: str) -> Dict[str, Any]:
        if self.runtime.is_active(session_id):
            raise ApiError("runtime_active", "任务运行中，先暂停或取消后再删除。")
        if not self.store.delete_session(session_id):
            raise ApiError("session_not_found", "未找到该寻访任务。")
        self.event_bus.publish("session_updated", {"session_id": session_id})
        return {"ok": True}

    def list_candidates(self, session_id: str) -> list[Dict[str, Any]]:
        self._require_session(session_id)
        return self.store.list_candidates(session_id)

    def get_candidate(self, candidate_id: str) -> Dict[str, Any]:
        candidates = self.store.get_candidates_by_ids([candidate_id])
        if not candidates:
            raise ApiError("candidate_not_found", "未找到该候选人。")
        detail = self.store.get_candidate_detail(candidate_id) or {}
        sources = self.store.list_candidate_sources(candidate_id)
        return {"summary": candidates[0], "detail": detail, "sources": sources}

    def list_events(self, session_id: str) -> list[Dict[str, Any]]:
        self._require_session(session_id)
        return self.store.list_events(session_id)

    def get_criteria(self, session_id: str) -> Dict[str, Any]:
        self._require_session(session_id)
        return {
            "latest": self.store.get_latest_criteria_version(session_id),
            "draft": self.store.get_latest_criteria_version(session_id, "draft"),
            "confirmed": self.store.get_latest_criteria_version(session_id, "confirmed"),
            "resolved": self.store.get_latest_criteria(session_id),
        }

    def generate_criteria_draft(self, session_id: str) -> Dict[str, Any]:
        session = self._require_session(session_id)
        self.store.add_event(
            session_id,
            None,
            "criteria_draft",
            "AI 正在生成寻访基准草案",
            "系统正在后台生成岗位匹配要求，界面可继续操作。",
            {},
        )
        future = self.runtime.match_queue.submit(self._generate_criteria_draft, session_id)
        future.add_done_callback(lambda done: self._on_criteria_draft_done(session_id, done))
        self.event_bus.publish("session_updated", {"session_id": session_id, "title": session.get("title")})
        return {"ok": True, "session_id": session_id, "message": "已开始生成寻访基准草案。"}

    def _generate_criteria_draft(self, session_id: str) -> None:
        session = self.store.get_session(session_id) or {}
        try:
            criteria = self.runtime.brain.build_criteria(
                str(session.get("jd_text") or ""),
                str(session.get("user_notes") or ""),
            )
        except Exception:
            criteria = {}
        keywords = str(criteria.get("keywords_text") or "").strip()
        requirements = str(criteria.get("requirements_text") or "").strip()
        if not keywords:
            keywords = "\n".join(str(item) for item in criteria.get("core_terms", []) if item)
        if not requirements:
            requirements = "请人工填写本岗位最关键的匹配要求。"
        self.store.create_criteria_version(
            session_id,
            keywords,
            requirements,
            source_jd_text=str(session.get("jd_text") or ""),
            source_user_notes=str(session.get("user_notes") or ""),
            ai_raw_response=criteria,
            created_by="ai",
            status="draft",
        )
        self.store.add_event(
            session_id,
            None,
            "criteria_draft",
            "AI 已生成寻访基准草案",
            "请人工确认或修改岗位匹配要求。",
            {"requirements_text": requirements},
        )

    def _on_criteria_draft_done(self, session_id: str, future) -> None:
        try:
            future.result()
            self.event_bus.publish("criteria_ready", {"session_id": session_id})
        except Exception as exc:
            self.store.add_event(
                session_id,
                None,
                "criteria_draft",
                "寻访基准草案生成失败",
                "可手动填写岗位匹配要求。错误：{}".format(exc),
                {},
            )
            self.event_bus.publish("criteria_error", {"session_id": session_id})

    def confirm_criteria(self, session_id: str, payload: CriteriaConfirmRequest) -> Dict[str, Any]:
        session = self._require_session(session_id)
        requirements = payload.requirements_text.strip()
        if not requirements:
            raise ApiError("criteria_empty", "请先填写岗位匹配要求描述。")
        criteria = self.store.get_latest_criteria_version(session_id)
        ai_raw: Dict[str, Any] = {}
        if criteria and isinstance(criteria.get("ai_raw_response"), dict):
            ai_raw = dict(criteria.get("ai_raw_response") or {})
        if payload.selected_direction.strip():
            ai_raw["selected_direction"] = payload.selected_direction.strip()
        keywords = payload.keywords_text.strip()
        if criteria and str(criteria.get("status") or "") != "confirmed":
            criteria_id = str(criteria["id"])
            self.store.update_criteria_version(criteria_id, keywords, requirements, status="draft")
            with self.store.connect() as connection:
                from ..storage.sqlite_store import to_json
                connection.execute(
                    "UPDATE match_criteria_versions SET ai_raw_response_json = ? WHERE id = ?",
                    (to_json(ai_raw), criteria_id),
                )
            self.store.confirm_criteria_version(criteria_id)
        else:
            criteria_id = self.store.create_criteria_version(
                session_id,
                keywords,
                requirements,
                source_jd_text=str(session.get("jd_text") or ""),
                source_user_notes=str(session.get("user_notes") or ""),
                ai_raw_response=ai_raw,
                created_by="human",
            )
            self.store.confirm_criteria_version(criteria_id)
        self.store.add_event(
            session_id,
            None,
            "criteria_confirmed",
            "寻访基准已确认",
            "后续搜索、抓详情和匹配将基于当前岗位匹配要求执行。",
            {"requirements_text": requirements, "selected_direction": payload.selected_direction.strip()},
        )
        self.event_bus.publish("criteria_confirmed", {"session_id": session_id})
        return {"ok": True, "criteria_version_id": criteria_id}

    def get_config(self) -> Dict[str, Any]:
        config = self.config_manager.config
        data = config.model_dump()
        data["api_key"] = ""
        data["backend_api_key"] = ""
        data["api_key_configured"] = bool(config.api_key)
        data["backend_api_key_configured"] = bool(config.backend_api_key)
        return data

    def update_config(self, payload: ConfigUpdateRequest) -> Dict[str, Any]:
        values = payload.model_dump()
        if not values.get("api_key"):
            values["api_key"] = self.config_manager.config.api_key
        if not values.get("backend_api_key"):
            values["backend_api_key"] = self.config_manager.config.backend_api_key
        self.config_manager.update(**values)
        if not self.config_manager.save_config():
            raise ApiError("config_save_failed", "配置文件写入失败。")
        self._rebuild_runtime_tools()
        return {"ok": True, "message": "设置已保存，后端工具已重新加载。"}

    def test_llm_connection(self, profile: str = "default") -> Dict[str, Any]:
        if profile not in {"default", "backend"}:
            raise ApiError("invalid_profile", "profile 只能是 default 或 backend。")
        return self.config_manager.test_llm_connection(profile)

    def _rebuild_runtime_tools(self) -> None:
        try:
            self.runtime.liepin_tool.close()
        except Exception:
            pass
        self.runtime.browser_queue.shutdown()
        self.runtime.match_queue.shutdown()
        self.runtime = self._build_runtime()

    def start_session(self, session_id: str) -> Dict[str, Any]:
        session = self._require_session(session_id)
        if self.runtime.is_active(session_id):
            self.runtime.resume_session(session_id)
            return {"ok": True, "session_id": session_id, "status": "resumed"}
        if not self.store.get_latest_criteria_version(session_id, "confirmed"):
            raise ApiError("criteria_not_confirmed", "请先确认岗位匹配要求，再开始寻访。")
        config = self.config_manager.config
        if not (config.api_base_url and config.api_key and config.model_name):
            raise ApiError("missing_llm_config", "请先配置 API Base URL、API Key 和模型名称。")
        try:
            logged_in = self.runtime.browser_queue.run(self.runtime.liepin_tool.check_login)
        except Exception as exc:
            try:
                self.runtime.browser_queue.submit(self.runtime.liepin_tool.open_for_login_or_search)
            except Exception:
                pass
            raise ApiError("browser_error", "检查猎聘登录失败：{}".format(exc)) from exc
        if not logged_in:
            try:
                self.runtime.browser_queue.submit(self.runtime.liepin_tool.open_for_login_or_search)
            except Exception:
                pass
            raise ApiError("liepin_login_required", "请先在打开的猎聘浏览器中完成登录。")
        if str(session.get("status") or "") == SessionStatus.PAUSED.value:
            self.runtime.resume_session(session_id)
        self.runtime.start_session(session_id)
        return {"ok": True, "session_id": session_id, "status": "started"}

    def pause_session(self, session_id: str) -> Dict[str, Any]:
        self._require_session(session_id)
        self.runtime.pause_session(session_id)
        return {"ok": True, "session_id": session_id}

    def resume_session(self, session_id: str) -> Dict[str, Any]:
        self._require_session(session_id)
        if self.runtime.is_active(session_id):
            self.runtime.resume_session(session_id)
            return {"ok": True, "session_id": session_id, "status": "resumed"}
        return self.start_session(session_id)

    def cancel_session(self, session_id: str) -> Dict[str, Any]:
        self._require_session(session_id)
        self.runtime.cancel_session(session_id)
        return {"ok": True, "session_id": session_id}

    def open_browser(self) -> Dict[str, Any]:
        future = self.runtime.browser_queue.submit(self.runtime.liepin_tool.open_for_login_or_search)
        return {"ok": True, "message": "正在打开猎聘浏览器。", "future_id": str(id(future))}

    def close_browser(self) -> Dict[str, Any]:
        self.runtime.browser_queue.run(self.runtime.liepin_tool.close_browser)
        self.event_bus.publish("browser_closed", {})
        return {"ok": True}

    def browser_status(self) -> Dict[str, Any]:
        try:
            state = self.runtime.browser_queue.run(
                self.runtime.liepin_tool.browser_manager.get_state
            )
            return {
                "ok": True,
                "profile_dir": state.profile_dir,
                "channel": state.channel,
                "headless": state.headless,
                "is_running": state.is_running,
                "logged_in": state.logged_in,
                "current_url": state.current_url,
            }
        except Exception as exc:
            return {"ok": False, "error_code": "browser_error", "message": str(exc)}

    def export_session(self, session_id: str) -> Dict[str, Any]:
        self._require_session(session_id)
        export_dir = self.workspace_root / "exports"
        exporter = ExportService(self.store, export_dir)
        path = exporter.export_session(session_id)
        return {
            "ok": True,
            "path": str(path),
            "reports_dir": str(exporter.last_candidate_reports_dir or ""),
        }

    def list_pool(self) -> list[Dict[str, Any]]:
        return self.store.list_pool_entries()

    def add_to_pool(self, session_id: str) -> Dict[str, Any]:
        self._require_session(session_id)
        self.store.add_session_to_pool(session_id)
        self.event_bus.publish("pool_updated", {"session_id": session_id})
        return {"ok": True}

    def remove_from_pool(self, session_id: str) -> Dict[str, Any]:
        self.store.remove_session_from_pool(session_id)
        self.event_bus.publish("pool_updated", {"session_id": session_id})
        return {"ok": True}

    def reorder_pool(self, payload: PoolReorderRequest) -> Dict[str, Any]:
        self.store.reorder_pool(payload.session_ids)
        self.event_bus.publish("pool_updated", {})
        return {"ok": True}

    def clear_pool(self) -> Dict[str, Any]:
        count = self.store.clear_pool_by_status(["completed", "failed"])
        self.event_bus.publish("pool_updated", {})
        return {"ok": True, "count": count}

    def start_next_pool_item(self) -> Dict[str, Any]:
        active = self.store.get_active_pool_session()
        if active:
            session_id = str(active.get("session_id") or "")
            session = self.store.get_session(session_id) or {}
            status = str(session.get("status") or "")
            if status in {"completed", "failed", "cancelled"}:
                self.store.update_pool_status(session_id, "completed")
            else:
                return {"ok": True, "session_id": session_id, "status": "active"}
        next_entry = self.store.get_next_queued_session()
        if not next_entry:
            return {"ok": True, "done": True, "message": "项目池已处理完毕。"}
        session_id = str(next_entry.get("session_id") or "")
        self.store.update_pool_status(session_id, "active")
        session = self.store.get_session(session_id) or {}
        status = str(session.get("status") or "")
        if status == "criteria_draft" and not self.store.get_latest_criteria_version(session_id, "draft") and not self.store.get_latest_criteria_version(session_id, "confirmed"):
            self.generate_criteria_draft(session_id)
            message = "已开始生成寻访基准，请确认后继续。"
        elif status in {"criteria_confirmed", "ready", "paused"}:
            try:
                self.start_session(session_id)
                message = "已开始处理项目池中的任务。"
            except ApiError as exc:
                message = exc.message
        else:
            message = "项目已设为处理中。"
        self.event_bus.publish("pool_updated", {"session_id": session_id})
        return {"ok": True, "session_id": session_id, "status": "active", "message": message}

    def stop_pool(self) -> Dict[str, Any]:
        active = self.store.get_active_pool_session()
        if active:
            self.store.update_pool_status(str(active.get("session_id") or ""), "queued")
        self.event_bus.publish("pool_updated", {})
        return {"ok": True}

    def _require_session(self, session_id: str) -> Dict[str, Any]:
        session = self.store.get_session(session_id)
        if not session:
            raise ApiError("session_not_found", "未找到该寻访任务。")
        return session
