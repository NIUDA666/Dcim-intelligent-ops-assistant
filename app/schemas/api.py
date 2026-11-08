"""FastAPI 请求 / 响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis import EvidenceBundle
from app.schemas.execution import PendingConfirmation, TaskResult, TimeWindow


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: str
    name: str | None
    revision: int
    created_at: datetime


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    query: str = Field(min_length=1, max_length=2000)
    client_request_id: str | None = None


class QueryAccepted(BaseModel):
    request_id: str
    session_id: str
    status: str = "accepted"
    revision: int
    events_url: str


class TaskCard(BaseModel):
    task_id: str
    semantic_id: str
    intent: str
    title: str
    status: str
    scheduling_status: str
    device_refs: list[dict[str, Any]] = Field(default_factory=list)
    time_window: TimeWindow | None = None
    complete: bool = False
    source_mode: str = "mock"
    warnings: list[str] = Field(default_factory=list)
    data: Any = None
    unit: str | None = None
    aggregation: str | None = None
    error_code: str | None = None
    clarification: str | None = None
    result_type: str | None = None
    analysis: EvidenceBundle | None = None
    result: TaskResult | None = None


class QuerySnapshot(BaseModel):
    request_id: str
    session_id: str
    original_query: str
    overall_status: Literal[
        "accepted",
        "running",
        "success",
        "partial",
        "waiting_user",
        "failed",
        "cancelled",
    ]
    revision: int
    fixed_query_time: datetime
    tasks: list[TaskCard] = Field(default_factory=list)
    pending_confirmations: list[PendingConfirmation] = Field(default_factory=list)
    answer: str | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_id: str
    session_revision: int
    client_request_id: str | None = None


class ConfirmationAccepted(BaseModel):
    request_id: str
    confirmation_id: str
    status: str = "accepted"
    revision: int
    events_url: str


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    task_id: str | None = None
    label: str
    comment: str | None = None


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None
