from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from threading import Thread
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .rules import fallback_reply
from .schemas import (
    AnalyzeRequest,
    DraftRequest,
    IncomingMessageRequest,
    QualityRequest,
    RiskUpdateRequest,
    SendRequest,
    ServiceModeRequest,
)
from .service import EmpathyService


service = EmpathyService(settings)
startup_report: dict[str, Any] = {}
settings.uploads_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global startup_report
    startup_report = service.initialize()
    Thread(
        target=service.ai.warmup_text_model,
        name="qwen-text-warmup",
        daemon=True,
    ).start()
    yield


app = FastAPI(
    title="共情双舱 API",
    version="0.1.0",
    description="面向美妆电商人工客服的服务记忆与风险协同系统",
    lifespan=lifespan,
)


def _not_found(kind: str, identifier: str | int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"找不到{kind}: {identifier}")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {**service.stats(), "status": "ok", "startup": startup_report}


@app.get("/api/evaluation")
def evaluation() -> dict[str, Any]:
    return service.evaluation_summary()


@app.get("/api/conversations")
def conversations(
    search: str = Query(default="", max_length=100),
    limit: int = Query(default=80, ge=1, le=200),
) -> dict[str, Any]:
    items = service.list_conversations(search=search, limit=limit)
    return {"items": items, "total": len(items)}


@app.get("/api/conversations/{session_id}")
def conversation(session_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        analyzed = service.snapshot(session_id)
    except KeyError:
        raise _not_found("会话", session_id)
    if analyzed["analysis"].get("run", {}).get("pending"):
        background_tasks.add_task(service.analyze, session_id)
    draft = fallback_reply(analyzed["bundle"], analyzed["analysis"])
    return {**analyzed, "draft": draft}


@app.get("/api/conversations/{session_id}/bundle")
def conversation_bundle(session_id: str) -> dict[str, Any]:
    try:
        return {"bundle": service.get_bundle(session_id)}
    except KeyError:
        raise _not_found("会话", session_id)


@app.post("/api/conversations/{session_id}/analyze")
def analyze(session_id: str, request: AnalyzeRequest) -> dict[str, Any]:
    try:
        result = service.analyze(session_id, force=request.force)
        return {
            **result,
            "draft": fallback_reply(result["bundle"], result["analysis"]),
        }
    except KeyError:
        raise _not_found("会话", session_id)


@app.post("/api/conversations/{session_id}/draft")
def draft(session_id: str, request: DraftRequest) -> dict[str, Any]:
    try:
        return service.draft(session_id, tone=request.tone)
    except KeyError:
        raise _not_found("会话", session_id)


@app.post("/api/conversations/{session_id}/quality-check")
def quality_check(session_id: str, request: QualityRequest) -> dict[str, Any]:
    try:
        return service.quality_check(session_id, request.text)
    except KeyError:
        raise _not_found("会话", session_id)


@app.post("/api/conversations/{session_id}/send")
def send(session_id: str, request: SendRequest) -> dict[str, Any]:
    try:
        return service.send(session_id, request.text, request.actor)
    except KeyError:
        raise _not_found("会话", session_id)


@app.post("/api/conversations/{session_id}/read")
def mark_read(session_id: str) -> dict[str, Any]:
    try:
        return service.mark_conversation_read(session_id)
    except KeyError:
        raise _not_found("会话", session_id)


@app.patch("/api/conversations/{session_id}/service-mode")
def service_mode(session_id: str, request: ServiceModeRequest) -> dict[str, Any]:
    try:
        return service.set_service_mode(session_id, request.mode, request.reason)
    except KeyError:
        raise _not_found("会话", session_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.post("/api/conversations/{session_id}/incoming")
def incoming(session_id: str, request: IncomingMessageRequest) -> dict[str, Any]:
    try:
        return service.add_incoming(
            session_id,
            request.text,
            image_data_url=request.image_data_url,
            image_name=request.image_name,
        )
    except KeyError:
        raise _not_found("会话", session_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.get("/api/consumer/conversations/{session_id}")
def consumer_conversation(session_id: str) -> dict[str, Any]:
    try:
        return service.get_consumer_view(session_id)
    except KeyError:
        raise _not_found("会话", session_id)


@app.post("/api/consumer/conversations/{session_id}/messages")
def consumer_message(
    session_id: str,
    request: IncomingMessageRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    try:
        received = service.add_incoming(
            session_id,
            request.text,
            analyze=False,
            image_data_url=request.image_data_url,
            image_name=request.image_name,
        )
        background_tasks.add_task(service.handle_incoming, session_id)
        return {
            **received,
            "analysis_status": "queued",
            "conversation": service.get_consumer_view(session_id),
        }
    except KeyError:
        raise _not_found("会话", session_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.get("/api/risks")
def risks(status: str = Query(default="open", pattern="^(open|all)$")) -> dict[str, Any]:
    return service.list_risks(status=status)


@app.patch("/api/risks/{risk_id}")
def update_risk(risk_id: int, request: RiskUpdateRequest) -> dict[str, Any]:
    try:
        return service.update_risk(
            risk_id,
            owner=request.owner,
            deadline=request.deadline,
            status=request.status,
        )
    except KeyError:
        raise _not_found("风险事件", risk_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.get("/api/evidence/{evidence_id}")
def evidence(evidence_id: str) -> dict[str, Any]:
    try:
        return service.get_evidence(evidence_id)
    except KeyError:
        raise _not_found("证据", evidence_id)


@app.post("/api/demo/reset")
def reset_demo() -> dict[str, Any]:
    report = service.initialize(force_import=True)
    return {"status": "reset", "report": report}


app.mount("/static", StaticFiles(directory=settings.frontend_dir), name="static")
app.mount("/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(Path(settings.frontend_dir) / "index.html")


@app.get("/consumer", include_in_schema=False)
def consumer_index() -> FileResponse:
    return FileResponse(Path(settings.frontend_dir) / "consumer.html")


@app.get("/evaluation", include_in_schema=False)
def evaluation_index() -> FileResponse:
    return FileResponse(Path(settings.frontend_dir) / "evaluation.html")
