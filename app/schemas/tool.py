"""标准工具返回结构（§9.3）。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolStatus(str, Enum):
    success = "success"
    empty = "empty"
    partial = "partial"
    error = "error"
    needs_confirmation = "needs_confirmation"


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: str
    source_request_id: str | None = None
    device_id: str | None = None
    point_id: str | None = None
    alarm_type_id: str | None = None


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    task_id: str
    source_mode: str = "mock"
    status: ToolStatus
    result_type: str
    data: Any = None
    unit: str | None = None
    aggregation: str | None = None
    complete: bool = False
    requested_window: dict[str, Any] | None = None
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[SourceRef] = Field(default_factory=list)
    fetched_at: datetime
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
