"""候选方向与来源状态（§8.2、§8.5）。

候选方向只是待核查方向；平台报告、程序特征和用户主张分别保存，不互相冒充。
"""

from __future__ import annotations

from typing import Any

from app.evidence.collector import CollectedEvidence
from app.evidence.features import FeatureComputer
from app.schemas.analysis import (
    AnomalyAssertion,
    EvidenceFeature,
    EvidenceGap,
    Hypothesis,
)
from app.schemas.execution import ExecutionTask


def _features_by_name(features: list[EvidenceFeature]) -> dict[str, EvidenceFeature]:
    return {feature.name: feature for feature in features}


def build_assertions(
    *,
    task: ExecutionTask,
    collected: CollectedEvidence,
    features: list[EvidenceFeature],
) -> tuple[list[AnomalyAssertion], list[EvidenceGap], list[str]]:
    assertions: list[AnomalyAssertion] = []
    gaps: list[EvidenceGap] = []
    warnings: list[str] = []
    sequence = 0
    context = task.analysis_context or {}
    match_kind = context.get("match_anomaly_kind")

    for alarm_type_id in sorted({record["alarm_type_id"] for record in collected.alarm_records}):
        sequence += 1
        evidence_ids = [
            collected.alarm_fact_ids[record["episode_id"]]
            for record in collected.alarm_records
            if record["alarm_type_id"] == alarm_type_id
            and record.get("episode_id") in collected.alarm_fact_ids
        ]
        assertions.append(
            AnomalyAssertion(
                id=f"an_{sequence:02d}",
                source="platform_alarm",
                status="reported_by_platform",
                anomaly_kind=match_kind,
                evidence_ids=evidence_ids,
                statement="平台告警记录报告过该类型告警；平台报告不等于现场根因已确认。",
            )
        )

    by_name = _features_by_name(features)
    sustained = by_name.get("sustained_threshold")
    quality = by_name.get("data_quality")
    if sustained is not None and sustained.status == "matched":
        sequence += 1
        assertions.append(
            AnomalyAssertion(
                id=f"an_{sequence:02d}",
                source="backend_evaluation",
                status="confirmed_abnormal",
                anomaly_kind=match_kind,
                evidence_ids=list(sustained.evidence_ids),
                statement=sustained.reason,
            )
        )
    elif sustained is not None and sustained.status == "not_matched" and quality is not None and quality.status == "matched":
        sequence += 1
        assertions.append(
            AnomalyAssertion(
                id=f"an_{sequence:02d}",
                source="backend_evaluation",
                status="confirmed_normal",
                anomaly_kind=match_kind,
                evidence_ids=list(sustained.evidence_ids),
                statement=(
                    "程序按已固定阈值与持续样本参数计算，核查窗口内未满足持续超限条件；"
                    "该结论只覆盖本次窗口，不代表窗口外或历史状态。"
                ),
            )
        )
    elif context.get("match_anomaly_kind"):
        sequence += 1
        assertions.append(
            AnomalyAssertion(
                id=f"an_{sequence:02d}",
                source="backend_evaluation",
                status="unknown",
                anomaly_kind=match_kind,
                evidence_ids=list(sustained.evidence_ids) if sustained else [],
                statement=(
                    sustained.reason
                    if sustained is not None
                    else "本次计划未注册可复算的异常判定规则，无法给出确认状态。"
                ),
            )
        )

    platform_ids = [item.id for item in assertions if item.source == "platform_alarm"]
    backend_normal = [
        item for item in assertions if item.source == "backend_evaluation" and item.status == "confirmed_normal"
    ]
    active_platform = any(
        record.get("recovered_at") is None for record in collected.alarm_records
    )
    if platform_ids and backend_normal and active_platform:
        warnings.append("source_conflict_platform_active_vs_backend_normal")
        gaps.append(
            EvidenceGap(
                id=f"gap_conflict_{len(gaps) + 1:02d}",
                type="source_conflict",
                item="平台活动告警与程序未超限结论",
                impact="两类来源结论不一致，按原样保留，不交由模型投票或覆盖",
            )
        )
    return assertions, gaps, warnings


def build_hypotheses(
    *,
    features: list[EvidenceFeature],
    collected: CollectedEvidence,
) -> list[Hypothesis]:
    by_name = _features_by_name(features)
    hypotheses: list[Hypothesis] = []
    sequence = 0

    local = by_name.get("local_temperature_elevation")
    peer_stability = by_name.get("peer_stability")
    if (
        local is not None
        and local.status == "matched"
        and peer_stability is not None
        and peer_stability.status == "matched"
    ):
        sequence += 1
        hypotheses.append(
            Hypothesis(
                id=f"hp_{sequence:02d}",
                code="local_device_condition",
                label="优先核查本机运行状态、送回风与风机反馈",
                support_feature_ids=[local.id, peer_stability.id],
                conflicting_evidence_ids=[],
                assessment="supported_direction",
                confirmation="pending_field_check",
            )
        )

    common = by_name.get("common_change")
    if common is not None and common.status == "matched":
        sequence += 1
        upstream = by_name.get("upstream_cooling_observation")
        hypotheses.append(
            Hypothesis(
                id=f"hp_{sequence:02d}",
                code="common_upstream_cooling",
                label="补查共同供冷上游、冷冻水支路及机房环境因素",
                support_feature_ids=[common.id],
                conflicting_evidence_ids=[],
                assessment="supported_direction",
                confirmation="pending_field_check",
            )
        )

    quality = by_name.get("data_quality")
    spike = by_name.get("short_spike")
    quality_problem = quality is not None and quality.status == "not_matched"
    spike_problem = spike is not None and spike.status == "matched"
    if quality_problem or spike_problem:
        sequence += 1
        support = [
            feature.id
            for feature in (quality, spike)
            if feature is not None and feature.status in {"matched", "not_matched"}
        ]
        hypotheses.append(
            Hypothesis(
                id=f"hp_{sequence:02d}",
                code="sensor_or_data_quality",
                label="核查测点绑定、采集链路与短时跳变",
                support_feature_ids=support,
                conflicting_evidence_ids=[],
                assessment="supported_direction",
                confirmation="pending_field_check",
            )
        )

    # 只保留实际有支持特征的候选；未匹配或未知不等同于排除故障。
    return hypotheses


def build_feature_gap(
    *,
    features: list[EvidenceFeature],
    existing: list[EvidenceGap],
) -> EvidenceGap | None:
    by_name = _features_by_name(features)
    sustained = by_name.get("sustained_threshold")
    if sustained is None or sustained.status != "unknown":
        return None
    if any(gap.type == "missing_rule_parameters" for gap in existing):
        return None
    return EvidenceGap(
        id=f"gap_param_{len(existing) + 1:02d}",
        type="missing_rule_parameters",
        item="持续超限判定所需的阈值或连续样本参数",
        impact="持续超限特征为 unknown；平台告警事实和可用读数仍保留",
    )


def evaluate(
    *,
    task: ExecutionTask,
    collected: CollectedEvidence,
    features: list[EvidenceFeature],
) -> tuple[list[AnomalyAssertion], list[Hypothesis], list[EvidenceGap], list[str]]:
    assertions, conflict_gaps, warnings = build_assertions(
        task=task, collected=collected, features=features
    )
    hypotheses = build_hypotheses(features=features, collected=collected)
    gaps = list(conflict_gaps)
    feature_gap = build_feature_gap(features=features, existing=list(collected.gaps) + gaps)
    if feature_gap is not None:
        gaps.append(feature_gap)
    return assertions, hypotheses, gaps, warnings
