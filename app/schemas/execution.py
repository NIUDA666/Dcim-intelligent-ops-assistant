"""执行层模型。

IntentPlan 与 ExecutionPlan 分开存储（§9.1）：
- IntentPlan 只有原始表达和临时 semantic_id；
- ExecutionPlan 才包含真实 asset_id、point_id、alarm_type_id、时间窗口和工具绑定。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.intent import Intent, IntentPlan


class SchedulingStatus(str, Enum):
    pending = "pending"
    waiting_user = "waiting_user"
    waiting_dependency = "waiting_dependency"
    ready = "ready"
    running = "running"
    done = "done"
    blocked = "blocked"
    skipped_condition = "skipped_condition"
    cancelled = "cancelled"


class TaskResultStatus(str, Enum):
    success = "success"
    empty = "empty"
    partial = "partial"
    error = "error"
    needs_confirmation = "needs_confirmation"
    skipped_condition = "skipped_condition"
    blocked = "blocked"


class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _check_window(self) -> "TimeWindow":
        for name, value in (("start", self.start), ("end", self.end)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name}_must_be_timezone_aware")
        # 统一使用半开区间 [start, end)；允许午夜开始的空窗口。
        if self.start > self.end:
            raise ValueError("invalid_window_start_after_end")
        return self

    def contains(self, value: datetime) -> bool:
        return self.start <= value < self.end


class CountPolicy(str, Enum):
    new_occurrences_in_window = "new_occurrences_in_window"
    active_at_query_time = "active_at_query_time"
    recovered_in_window = "recovered_in_window"


class ExecutionTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    semantic_id: str
    intent: Intent
    dependencies: list[str] = Field(default_factory=list)
    input_bindings: dict[str, Any] = Field(default_factory=dict)
    run_condition: dict[str, Any] = Field(default_factory=dict)
    scheduling_status: SchedulingStatus = SchedulingStatus.pending
    execution_id: str | None = None
    task_ver: int = 1
    result_refs: list[str] = Field(default_factory=list)

    device_refs: list[dict[str, Any]] = Field(default_factory=list)
    metric_binding: dict[str, Any] | None = None
    alarm_binding: dict[str, Any] | None = None
    time_window: TimeWindow | None = None
    count_policy: CountPolicy | None = None

    validated_tool_calls: list[str] = Field(default_factory=list)
    status: str = "pending"

    # 模式 A 运行快照。固定版本后，后续配置发布不热更新旧任务（§8.9.4）。
    config_bundle_version: str | None = None
    scenario_version: str | None = None
    snippet_versions: dict[str, str] = Field(default_factory=dict)
    expanded_plan: dict[str, Any] | None = None
    plan_hash: str | None = None
    feature_profile_version: str | None = None
    fallback_used: bool = False

    analysis_context: dict[str, Any] | None = None


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    session_id: str
    semantic_plan: IntentPlan
    fixed_query_time: datetime
    timezone: str = "Asia/Shanghai"
    tasks: list[ExecutionTask]
    errors: list[dict[str, Any]] = Field(default_factory=list)
    pending_confirmations: list["PendingConfirmation"] = Field(default_factory=list)


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    task_id: str
    execution_id: str
    result_ref: str
    status: TaskResultStatus
    result_type: str
    source_mode: Literal["mock", "platform", "mixed"] = "mock"
    warnings: list[str] = Field(default_factory=list)
    data: Any = None
    unit: str | None = None
    aggregation: str | None = None
    requested_window: TimeWindow | None = None
    actual_window: TimeWindow | None = None
    complete: bool = False
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None


class ConfirmationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    asset_id: str | None = None
    point_id: str | None = None
    alarm_type_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PendingConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_id: str
    request_id: str
    session_id: str
    task_id: str
    field: Literal["device", "metric", "alarm"]
    prompt: str
    candidates: list[ConfirmationCandidate] = Field(default_factory=list)
    created_at: datetime
    expires_at: datetime
    session_revision: int


# 让 ExecutionPlan 的前向引用可解析。
ExecutionPlan.model_rebuild()
