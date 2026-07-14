"""FastAPI application for the Tauri workbench backend."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .schemas import ConfigUpdateRequest, CriteriaConfirmRequest, PoolReorderRequest, SessionCreateRequest
from .service import ApiError, WorkbenchService

_service: WorkbenchService | None = None


def create_app(workspace_root: str | None = None) -> FastAPI:
    global _service
    _service = WorkbenchService(workspace_root)
    app = FastAPI(title="Liepin Agent Workbench API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:1420",
            "http://127.0.0.1:1420",
            "tauri://localhost",
            "https://tauri.localhost",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request, exc: ApiError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=400,
            content={"ok": False, "error_code": exc.error_code, "message": exc.message},
        )

    def service() -> WorkbenchService:
        assert _service is not None
        return _service

    @app.get("/health")
    def health(svc: WorkbenchService = Depends(service)):
        return svc.health()

    @app.get("/sessions")
    def list_sessions(svc: WorkbenchService = Depends(service)):
        return svc.list_sessions()

    @app.post("/sessions")
    def create_session(payload: SessionCreateRequest, svc: WorkbenchService = Depends(service)):
        return svc.create_session(payload)

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.get_session(session_id)

    @app.delete("/sessions/{session_id}")
    def delete_session(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.delete_session(session_id)

    @app.get("/sessions/{session_id}/candidates")
    def list_candidates(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.list_candidates(session_id)

    @app.get("/candidates/{candidate_id}")
    def get_candidate(candidate_id: str, svc: WorkbenchService = Depends(service)):
        return svc.get_candidate(candidate_id)

    @app.get("/sessions/{session_id}/events")
    def list_events(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.list_events(session_id)

    @app.get("/sessions/{session_id}/criteria")
    def get_criteria(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.get_criteria(session_id)

    @app.post("/sessions/{session_id}/criteria/draft")
    def generate_criteria_draft(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.generate_criteria_draft(session_id)

    @app.post("/sessions/{session_id}/criteria/confirm")
    def confirm_criteria(
        session_id: str,
        payload: CriteriaConfirmRequest,
        svc: WorkbenchService = Depends(service),
    ):
        return svc.confirm_criteria(session_id, payload)

    @app.get("/config")
    def get_config(svc: WorkbenchService = Depends(service)):
        return svc.get_config()

    @app.put("/config")
    def update_config(payload: ConfigUpdateRequest, svc: WorkbenchService = Depends(service)):
        return svc.update_config(payload)

    @app.post("/config/test/{profile}")
    def test_llm_connection(profile: str, svc: WorkbenchService = Depends(service)):
        return svc.test_llm_connection(profile)

    @app.get("/pool")
    def list_pool(svc: WorkbenchService = Depends(service)):
        return svc.list_pool()

    @app.post("/pool/{session_id}")
    def add_to_pool(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.add_to_pool(session_id)

    @app.delete("/pool/{session_id}")
    def remove_from_pool(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.remove_from_pool(session_id)

    @app.post("/pool/reorder")
    def reorder_pool(payload: PoolReorderRequest, svc: WorkbenchService = Depends(service)):
        return svc.reorder_pool(payload)

    @app.post("/pool/clear")
    def clear_pool(svc: WorkbenchService = Depends(service)):
        return svc.clear_pool()

    @app.post("/pool/start-next")
    def start_next_pool_item(svc: WorkbenchService = Depends(service)):
        return svc.start_next_pool_item()

    @app.post("/pool/stop")
    def stop_pool(svc: WorkbenchService = Depends(service)):
        return svc.stop_pool()

    @app.post("/sessions/{session_id}/start")
    def start_session(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.start_session(session_id)

    @app.post("/sessions/{session_id}/pause")
    def pause_session(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.pause_session(session_id)

    @app.post("/sessions/{session_id}/resume")
    def resume_session(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.resume_session(session_id)

    @app.post("/sessions/{session_id}/cancel")
    def cancel_session(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.cancel_session(session_id)

    @app.post("/browser/open")
    def open_browser(svc: WorkbenchService = Depends(service)):
        return svc.open_browser()

    @app.post("/browser/close")
    def close_browser(svc: WorkbenchService = Depends(service)):
        return svc.close_browser()

    @app.get("/browser/status")
    def browser_status(svc: WorkbenchService = Depends(service)):
        return svc.browser_status()

    @app.post("/sessions/{session_id}/export")
    def export_session(session_id: str, svc: WorkbenchService = Depends(service)):
        return svc.export_session(session_id)

    @app.get("/events")
    async def stream_events(svc: WorkbenchService = Depends(service)):
        return StreamingResponse(svc.events.stream(), media_type="text/event-stream")

    return app
