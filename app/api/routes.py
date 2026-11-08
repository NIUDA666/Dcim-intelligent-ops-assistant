"""自定义 HTTP / SSE 接口（§12.1、§12.2）。"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.container import AppContainer
from app.schemas.api import (
    CancelRequest,
    ConfirmationAccepted,
    ConfirmationRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    FeedbackRequest,
    QueryAccepted,
    QueryRequest,
    QuerySnapshot,
)
from app.services.import_service import ImportValidationError
from app.services.query_service import QueryServiceError
from app.services.clock import clock
from app.settings import settings

router = APIRouter(prefix="/api/v1")


def _container(request: Request) -> AppContainer:
    return request.app.state.container


def _events_url(request_id: str) -> str:
    return f"/api/v1/queries/{request_id}/events"


def _raise(exc: QueryServiceError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


@router.post("/sessions", response_model=CreateSessionResponse, status_code=201)
async def create_session(
    request: Request, body: CreateSessionRequest | None = None
) -> CreateSessionResponse:
    session = await _container(request).query_service.create_session(
        body.name if body else None
    )
    return CreateSessionResponse(
        session_id=session.session_id,
        name=session.name,
        revision=session.revision,
        created_at=session.created_at,
    )


@router.post("/queries", response_model=QueryAccepted, status_code=202)
async def create_query(request: Request, body: QueryRequest) -> QueryAccepted:
    try:
        record = await _container(request).query_service.submit_query(
            session_id=body.session_id,
            query=body.query,
            client_request_id=body.client_request_id,
        )
    except QueryServiceError as exc:
        _raise(exc)
    return QueryAccepted(
        request_id=record.request_id,
        session_id=record.session_id,
        status="accepted",
        revision=record.revision,
        events_url=_events_url(record.request_id),
    )


@router.get("/queries/{request_id}", response_model=QuerySnapshot)
async def get_query(request: Request, request_id: str) -> QuerySnapshot:
    try:
        record = _container(request).query_service.require_query(request_id)
    except QueryServiceError as exc:
        _raise(exc)
    return _container(request).query_service.snapshot(record)


@router.get("/queries/{request_id}/events")
async def query_events(
    request: Request,
    request_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    since: str | None = None,
) -> StreamingResponse:
    service = _container(request).query_service
    try:
        service.require_query(request_id)
    except QueryServiceError as exc:
        _raise(exc)
    return StreamingResponse(
        service.stream_events(request_id, last_event_id or since),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/confirmations/{confirmation_id}",
    response_model=ConfirmationAccepted,
    status_code=202,
)
async def confirm(
    request: Request, confirmation_id: str, body: ConfirmationRequest
) -> ConfirmationAccepted:
    try:
        record, _ = await _container(request).query_service.confirm(
            confirmation_id=confirmation_id,
            selected_id=body.selected_id,
            session_revision=body.session_revision,
            client_request_id=body.client_request_id,
        )
    except QueryServiceError as exc:
        _raise(exc)
    return ConfirmationAccepted(
        request_id=record.request_id,
        confirmation_id=confirmation_id,
        status="accepted",
        revision=record.revision,
        events_url=_events_url(record.request_id),
    )


@router.post("/queries/{request_id}/cancel", response_model=QuerySnapshot)
async def cancel_query(
    request: Request, request_id: str, body: CancelRequest | None = None
) -> QuerySnapshot:
    try:
        record = await _container(request).query_service.cancel(
            request_id, body.reason if body else None
        )
    except QueryServiceError as exc:
        _raise(exc)
    return _container(request).query_service.snapshot(record)


@router.post("/feedback", status_code=201)
async def submit_feedback(request: Request, body: FeedbackRequest) -> dict:
    try:
        record = _container(request).query_service.record_feedback(
            request_id=body.request_id,
            task_id=body.task_id,
            label=body.label,
            comment=body.comment,
        )
    except QueryServiceError as exc:
        _raise(exc)
    return {
        "feedback_id": record.feedback_id,
        "status": "recorded",
        "created_at": record.created_at.isoformat(),
    }


@router.post("/documents", status_code=202)
async def import_document(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    doc_type: str = Form(default="sop"),
    doc_version: str | None = Form(default=None),
    device_types: str | None = Form(default=None),
    site_scope: str | None = Form(default=None),
) -> dict:
    content = await file.read()
    try:
        record = await _container(request).import_service.create(
            filename=file.filename or "",
            content=content,
            title=title,
            doc_type=doc_type,
            doc_version=doc_version,
            device_types=device_types,
            site_scope=site_scope,
        )
    except ImportValidationError as exc:
        raise HTTPException(
            status_code=422, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    return {
        "import_task_id": record.import_task_id,
        "doc_id": record.doc_id,
        "status": record.status,
        "stages": record.stages,
        "warnings": record.warnings,
    }


@router.get("/import-tasks/{import_task_id}")
async def get_import_task(request: Request, import_task_id: str) -> dict:
    try:
        record = _container(request).import_service.get(import_task_id)
    except ImportValidationError as exc:
        raise HTTPException(
            status_code=404, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    return {
        "import_task_id": record.import_task_id,
        "doc_id": record.doc_id,
        "filename": record.filename,
        "status": record.status,
        "stages": record.stages,
        "chunks": record.chunks,
        "warnings": record.warnings,
        "metadata": record.metadata,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


@router.get("/health")
async def health(request: Request) -> dict:
    container = _container(request)
    bundle = container.bundle
    return {
        "status": "ok",
        "app_name": settings.app_name,
        "short_name": settings.short_name,
        "timezone": str(settings.business_tz),
        "source_mode": container.gateway.source_mode,
        "fixed_query_time": clock.now().isoformat(),
        "demo_fixed_now": settings.demo_fixed_now,
        "config_bundle_version": bundle.bundle_version,
        "scenarios": [
            {"id": item.id, "version": item.version} for item in bundle.scenarios
        ],
        "fallback": bundle.fallback.id,
        "llm_enabled": bool(container.llm.enabled),
    }
