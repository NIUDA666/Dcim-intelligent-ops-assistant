"""semantic-plan/0.5 契约。

字段定义严格对齐设计文档第 5.5、9.1、11.1 节：
- 模型只输出原始槽位、候选意图和受限 analyze_result 关系；
- 真实 asset_id / point_id / alarm_type_id / task_id 全部由后端生成或解析。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Intent(str, Enum):
    knowledge_qa = "knowledge_qa"
    metric_latest = "metric_latest"
    metric_trend = "metric_trend"
    alarm_list = "alarm_list"
    alarm_count = "alarm_count"
    alarm_analysis = "alarm_analysis"
    unsupported = "unsupported"


class SemanticTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_id: str = Field(pattern=r"^s[1-4]$")
    intent: Intent
    room: str | None
    device: str | None
    metric: str | None
    time: str | None
    count_kind: Literal["new", "active", "recovered"] | None = None
    context_ref: Literal["focus_result"] | None = None

    @model_validator(mode="after")
    def _check_optional_fields(self) -> "SemanticTask":
        # §9.1：count_kind 仅允许 alarm_count，context_ref 仅允许明确承接的 alarm_analysis。
        if self.count_kind is not None and self.intent is not Intent.alarm_count:
            raise ValueError("count_kind_only_allowed_for_alarm_count")
        if self.context_ref is not None and self.intent is not Intent.alarm_analysis:
            raise ValueError("context_ref_only_allowed_for_alarm_analysis")
        return self


class SemanticRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["analyze_result"]
    source: Literal["s1", "s2", "s3", "s4"]
    target: Literal["s1", "s2", "s3", "s4"]
    condition: Literal["always", "if_abnormal"]


class IntentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[SemanticTask] = Field(min_length=1, max_length=4)
    relations: list[SemanticRelation] = Field(max_length=6)
    ask: str | None
    action: Literal["modify"] | None = None
