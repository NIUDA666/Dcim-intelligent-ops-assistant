"""模式 A 告警辅助分析的证据包模型。

字段对齐设计文档第 8.5、8.7、8.9.1 节。facts 只承载外部观测和记录；
features 承载程序按已注册规则计算的结果；hypotheses 只表示候选排查方向。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AnomalyClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["user_statement"] = "user_statement"
    status: Literal["unverified"] = "unverified"
    message_ref: str
    statement: str


class AnomalyAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: Literal["platform_alarm", "backend_evaluation"]
    status: Literal[
        "reported_by_platform",
        "confirmed_abnormal",
        "confirmed_normal",
        "unknown",
    ]
    anomaly_kind: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    statement: str | None = None


class EvidenceSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    value: float | None
    quality: str = "good"


class EvidenceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    source_system: str
    source_record_id: str
    observed_at: datetime | None = None
    statement: str
    quality: str | None = None
    coverage: str | None = None

    # 以下为报告和前端展示需要的可复核定位信息；字段均可为空。
    asset_id: str | None = None
    point_id: str | None = None
    metric_code: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    unit: str | None = None
    aggregation: str | None = None
    data: list[EvidenceSample] = Field(default_factory=list)
    plan_step_id: str | None = None


class EvidenceFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    source: Literal["backend_evaluation"] = "backend_evaluation"
    status: Literal["matched", "not_matched", "unknown"]
    evidence_ids: list[str] = Field(default_factory=list)
    rule_version: str | None = None
    algorithm_version: str | None = None
    parameter_refs: list[dict[str, Any]] = Field(default_factory=list)
    reason: str | None = None
    value: float | str | None = None
    unit: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    code: str
    label: str
    support_feature_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    assessment: str = "supported_direction"
    confirmation: str = "pending_field_check"


class EvidenceGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    item: str
    impact: str
    related_step_id: str | None = None


class SopSnippet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    doc_version: str
    doc_type: Literal["sop", "manual", "case", "faq"]
    chunk_id: str
    title: str
    section: str | None = None
    text: str
    device_types: list[str] = Field(default_factory=list)
    site_scope: list[str] = Field(default_factory=list)
    review_status: str = "approved"
    publication_status: str = "published"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    score: float | None = None
    purpose: str | None = None


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    status: Literal["completed", "partial", "failed"]
    analysis_subject: str
    anomaly_claim: AnomalyClaim | None = None
    anomaly_assertions: list[AnomalyAssertion] = Field(default_factory=list)
    facts: list[EvidenceFact] = Field(default_factory=list)
    features: list[EvidenceFeature] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    gaps: list[EvidenceGap] = Field(default_factory=list)
    sop_snippets: list[SopSnippet] = Field(default_factory=list)

    # 计划版本与范围（§9.2 / §11.3）。
    config_bundle_version: str | None = None
    scenario_id: str | None = None
    scenario_version: str | None = None
    snippet_versions: dict[str, str] = Field(default_factory=dict)
    feature_profile_version: str | None = None
    plan_hash: str | None = None
    fallback_used: bool = False
    fixed_query_time: datetime | None = None
    evidence_window: dict[str, Any] | None = None
    source_mode: str = "mock"
    warnings: list[str] = Field(default_factory=list)
    topology_is_current_snapshot: bool = True


class AnalysisContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    platform_instance_id: str
    asset_id: str
    device_type: str
    model_code: str | None = None
    cooling_mode: str | None = None
    site_id: str | None = None
    room_id: str | None = None
    room_name: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    event_id: str | None = None
    metric_code: str | None = None
    analysis_subject: str
    anomaly_claim: AnomalyClaim | None = None
    requested_anomaly_kind: str | None = None
    anomaly_assertions: list[AnomalyAssertion] = Field(default_factory=list)
    match_anomaly_kind: str | None = None
    match_basis: str | None = None
    fixed_query_time: datetime
    evidence_window: dict[str, Any] | None = None
    config_bundle_version: str | None = None
