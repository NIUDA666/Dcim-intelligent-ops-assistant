"""受证据约束的报告与引用校验（§8.7、§11.3）。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app.rag.llm_client import LLMClient, LLMError
from app.rag.prompts import build_report_prompt
from app.schemas.analysis import EvidenceBundle, EvidenceFact


_SECTIONS = [
    "## 对象与时段",
    "## 计划版本与范围",
    "## 观测事实",
    "## 程序特征",
    "## 候选方向",
    "## 冲突与缺口",
    "## 检查依据",
]
_PROHIBITED = ("根本原因", "一定是", "必然导致", "已确认故障", "故障概率")
_CITATION = re.compile(r"\[([A-Za-z][A-Za-z0-9_.#-]*)\]")
_DECIMAL = re.compile(r"\d+\.\d+")


def _source_phrase(fact: EvidenceFact) -> str:
    if fact.kind == "alarm_event":
        return "平台报告（reported_by_platform）"
    if fact.kind == "alarm_query":
        return "平台记录"
    if fact.kind == "topology_relation":
        return "平台当前拓扑快照"
    if fact.kind == "comparison_timeseries":
        return "平台观测记录（对照设备）"
    return "平台观测记录"


def _status_phrase(status: str) -> str:
    if status == "matched":
        return "满足本次规则条件"
    if status == "not_matched":
        return "未满足本次规则条件（仅限本次窗口）"
    return "未知（证据或参数不足）"


def _scope_text(expanded_plan: dict[str, Any]) -> str:
    labels = {
        "metric_history": "本机指标趋势",
        "alarm_history": "本机告警记录",
        "related_evidence": "已配置的上游关联证据",
        "peer_evidence": "同机房同类型对照证据",
    }
    actions = []
    for step in expanded_plan.get("steps") or []:
        label = labels.get(step.get("action"))
        if label and label not in actions:
            actions.append(label)
    return "、".join(actions) if actions else "无"


def build_template_report(
    bundle: EvidenceBundle,
    *,
    analysis_context: dict[str, Any],
    expanded_plan: dict[str, Any] | None = None,
) -> str:
    context = analysis_context or {}
    expanded = expanded_plan or {}
    device_name = context.get("analysis_subject") or bundle.analysis_subject
    asset_id = context.get("asset_id") or ""
    fixed = bundle.fixed_query_time.isoformat() if bundle.fixed_query_time else ""
    window = bundle.evidence_window or {}
    window_text = f"{window.get('start')} 至 {window.get('end')}" if window else "未提供"
    lines: list[str] = []

    lines.append("## 对象与时段")
    lines.append(
        f"核查对象：{device_name}（asset_id：{asset_id}）。查询锚点：{fixed}；"
        f"证据窗口：{window_text}；数据来源：{bundle.source_mode} 模拟数据。"
    )
    if bundle.anomaly_claim:
        lines.append(
            f"用户提出（待核实）：{bundle.anomaly_claim.statement} [claim_01]"
        )

    lines.append("## 计划版本与范围")
    if bundle.fallback_used:
        lines.append("未匹配专项分析场景，使用本机兜底取证。")
        lines.append("兜底不查询上下游，不能据此认定异常只在本机。")
    profile_id = expanded.get("feature_profile") or "无"
    lines.append(
        f"场景：{bundle.scenario_id or '无'} / v{bundle.scenario_version or '无'}；"
        f"配置包：{bundle.config_bundle_version or '无'}；"
        f"特征 profile：{profile_id} / v{bundle.feature_profile_version or '无'}；"
        f"计划哈希：{(bundle.plan_hash or '')[:16]}。"
    )
    lines.append(f"计划内取证范围：{_scope_text(expanded)}。")
    platform_count = len([item for item in bundle.anomaly_assertions if item.source == "platform_alarm"])
    if platform_count:
        lines.append("平台报告（reported_by_platform）与程序计算结果分别保留，不互相替代。")

    lines.append("## 观测事实")
    if not bundle.facts:
        lines.append("本次没有取得可复核的观测事实。")
    for fact in bundle.facts:
        lines.append(f"- {_source_phrase(fact)}：{fact.statement} [{fact.id}]")
    if not any(fact.kind == "alarm_event" for fact in bundle.facts):
        lines.append("平台在本次核查窗口没有返回告警事件；无告警不等于设备正常。")

    lines.append("## 程序特征")
    if not bundle.features:
        lines.append("本次未执行已注册特征算法。")
    for feature in bundle.features:
        refs = "、".join(
            f"{ref.get('parameter')}={ref.get('value')}{ref.get('unit') or ''}"
            for ref in feature.parameter_refs
            if ref.get("value") is not None
        )
        ref_text = f"；参数：{refs}" if refs else ""
        lines.append(
            f"- 程序按 {feature.algorithm_version or feature.rule_version or '已注册'} 规则计算："
            f"{feature.name} 为 {_status_phrase(feature.status)}；{feature.reason}{ref_text} [{feature.id}]"
        )

    lines.append("## 候选方向")
    if not bundle.hypotheses:
        lines.append("暂无可验证的候选方向；未匹配或 unknown 不等同于排除故障。")
    for hypothesis in bundle.hypotheses:
        support = "、".join(f"[{item}]" for item in hypothesis.support_feature_ids)
        conflict = "、".join(f"[{item}]" for item in hypothesis.conflicting_evidence_ids)
        extra = f"；冲突证据：{conflict}" if conflict else ""
        lines.append(
            f"- {hypothesis.label}（待现场确认，支持特征：{support}{extra}） [{hypothesis.id}]"
        )

    lines.append("## 冲突与缺口")
    if not bundle.gaps:
        lines.append("本次未记录证据缺口；仍需注意平台报告不等于现场根因确认。")
    for gap in bundle.gaps:
        lines.append(f"- {gap.item}：{gap.impact} [{gap.id}]")
    if bundle.topology_is_current_snapshot:
        lines.append("拓扑关系仅代表当前登记快照，不能证明异常时段的历史连接状态。")

    lines.append("## 检查依据")
    if not bundle.sop_snippets:
        lines.append("本轮未取得适用的有效 SOP 或手册片段；建议由现场人员按权限核查。")
    for snippet in bundle.sop_snippets:
        section = f" / {snippet.section}" if snippet.section else ""
        lines.append(
            f"- [{snippet.chunk_id}] {snippet.title}{section}：{snippet.text}"
            "（仅用于核查依据，不作为本次故障证据）"
        )
    return "\n".join(lines)


_METRIC_LABELS = {
    "supply_air_temperature": "送风温度",
    "return_air_temperature": "回风温度",
    "chilled_water_supply_temperature": "冷冻水供水温度",
    "fan_speed_feedback": "风机转速",
}
_ISO_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?"
)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _humanize_times(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        parsed = _parse_time(match.group(0))
        return parsed.strftime("%H:%M") if parsed else match.group(0)

    return _ISO_DATETIME.sub(replace, text)


def _window_text(window: dict[str, Any] | None) -> str:
    window = window or {}
    start = _parse_time(window.get("start"))
    end = _parse_time(window.get("end"))
    if start is None or end is None:
        return "未提供窗口"
    if start.date() == end.date():
        return f"{start:%Y-%m-%d %H:%M}–{end:%H:%M}"
    return f"{start:%Y-%m-%d %H:%M}–{end:%Y-%m-%d %H:%M}"


def _fmt_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{value:.1f}" if abs(value) < 100 else f"{value:.0f}"
    return f"{value:.2f}"


def _metric_label(fact: EvidenceFact | None) -> str:
    if fact is None:
        return "本机指标"
    if fact.metric_code:
        return _METRIC_LABELS.get(fact.metric_code, fact.metric_code)
    return "本机指标"


def _device_code(statement: str, fallback: str | None = None) -> str:
    match = re.search(r"\b([A-Z]{1,5}\d{2,5})\b", statement)
    return match.group(1) if match else (fallback or "关联设备")


def _fact_stats(fact: EvidenceFact) -> tuple[int, float, float, float] | None:
    values = [
        float(sample.value)
        for sample in fact.data
        if sample.value is not None and sample.quality == "good"
    ]
    if not values:
        values = [float(sample.value) for sample in fact.data if sample.value is not None]
    if not values:
        return None
    return len(values), sum(values) / len(values), min(values), max(values)


def _alarm_title(facts: list[EvidenceFact]) -> str:
    for fact in facts:
        match = re.search(r"[「“]([^」”]+)[」”]", fact.statement or "")
        if match:
            return match.group(1)
    return "相关告警"


def _alarm_phrases(bundle: EvidenceBundle) -> list[str]:
    phrases = []
    for fact in bundle.facts:
        if fact.kind != "alarm_event":
            continue
        text = _humanize_times(fact.statement or "")
        times = re.findall(r"\d{2}:\d{2}", text)
        if "恢复" in text and len(times) >= 2:
            phrases.append(f"{times[0]}–{times[1]} 已恢复")
        elif times:
            phrases.append(f"{times[0]} 起未恢复")
        else:
            phrases.append("发生时间未知")
    return phrases


def _abnormal_detail(bundle: EvidenceBundle) -> str:
    primary = next((fact for fact in bundle.facts if fact.kind == "timeseries"), None)
    label = _metric_label(primary)
    unit = primary.unit if primary and primary.unit else "℃"
    stats = _fact_stats(primary) if primary else None
    peak = f"，窗口最高 {_fmt_number(stats[3])}{unit}" if stats else ""
    threshold = None
    count = None
    for feature in bundle.features:
        if feature.name != "sustained_threshold":
            continue
        count = feature.value
        for ref in feature.parameter_refs:
            if ref.get("parameter") == "sustained_threshold_c":
                threshold = ref.get("value")
                break
        break
    assertion = next(
        (
            item
            for item in bundle.anomaly_assertions
            if item.status == "confirmed_abnormal" and item.statement
        ),
        None,
    )
    time_text = _humanize_times(assertion.statement or "")
    span = re.search(r"(\d{2}:\d{2})\s*(?:至|到|—|–|-)\s*(\d{2}:\d{2})", time_text)
    if threshold is not None and count and span:
        return (
            f"本机{label}在 {span.group(1)}–{span.group(2)} 连续 {int(float(count))} 个采样点"
            f"高于 {_fmt_number(float(threshold))}{unit}{peak}。"
        )
    if stats:
        return f"本机{label}出现持续偏高，窗口最高 {_fmt_number(stats[3])}{unit}。"
    if time_text:
        return f"本机{label}记录显示：{time_text}。"
    return "本机监测出现达到异常条件的持续偏高。"


def _conclusion(bundle: EvidenceBundle) -> tuple[str, str]:
    statuses = {item.status for item in bundle.anomaly_assertions}
    if bundle.status == "failed":
        return "分析未完成", "证据收集失败，本轮不能给出异常判断。"
    if "confirmed_abnormal" in statuses:
        return "发现异常", _abnormal_detail(bundle)
    if "confirmed_normal" in statuses:
        if any(item.source == "platform_alarm" for item in bundle.anomaly_assertions):
            return (
                "未发现异常",
                "本机窗口数据未达到异常条件；平台告警记录见依据，结论仅限本次窗口与已取证范围。",
            )
        return "未发现异常", "本次窗口内未发现达到异常条件的证据；结论仅限本次窗口与已取证范围。"
    if any(item.source == "platform_alarm" for item in bundle.anomaly_assertions):
        return "无法判定", "平台已有告警记录，但本机证据不足，不能确认异常，也不能按正常处理。"
    return "无法判定", "现有证据不足，不能按正常处理。"


def build_conclusion(bundle: EvidenceBundle) -> dict[str, str]:
    label, detail = _conclusion(bundle)
    return {"label": label, "detail": detail}


def _local_chat_phrase(bundle: EvidenceBundle) -> str | None:
    primary = next((fact for fact in bundle.facts if fact.kind == "timeseries"), None)
    if primary is None:
        return None
    stats = _fact_stats(primary)
    if stats is None:
        return f"本机{_metric_label(primary)}没有取得有效样本"
    count, mean, _, maximum = stats
    unit = primary.unit or ""
    return (
        f"本机{_metric_label(primary)} {count} 个有效样本，"
        f"均值 {_fmt_number(mean)}{unit}，最高 {_fmt_number(maximum)}{unit}"
    )


def _peer_chat_phrase(bundle: EvidenceBundle) -> str | None:
    entries = []
    for fact in bundle.facts:
        if fact.kind != "comparison_timeseries":
            continue
        stats = _fact_stats(fact)
        if stats is None:
            continue
        entries.append(
            (
                _device_code(fact.statement or "", fact.asset_id),
                stats[1],
                stats[3] - stats[2],
                fact.unit or "",
            )
        )
    if not entries:
        return None
    codes = "/".join(code for code, *_ in entries)
    means = [mean for _, mean, _, _ in entries]
    spans = [span for _, _, span, _ in entries]
    spread = max(means) - min(means)
    unit = entries[0][3]
    if spread <= 0.5 and all(span <= 0.5 for span in spans):
        return f"同机房 {codes} 稳定在约 {_fmt_number(means[0])}{unit}"
    return f"同机房 {codes} 均值相差 {_fmt_number(spread)}{unit}，需一并核查"


def _related_chat_phrase(bundle: EvidenceBundle) -> str | None:
    entries = []
    for fact in bundle.facts:
        if fact.kind != "related_timeseries":
            continue
        stats = _fact_stats(fact)
        if stats is None:
            continue
        entries.append(
            (
                _device_code(fact.statement or "", fact.asset_id),
                _metric_label(fact),
                stats[1],
                stats[3] - stats[2],
                fact.unit or "",
            )
        )
    if not entries:
        return None
    code, metric, mean, span, unit = entries[0]
    text = f"上游 {code} {metric}约 {_fmt_number(mean)}{unit}"
    if span <= 0.5:
        text += "，窗口内稳定"
    return text


def _chat_basis(bundle: EvidenceBundle, label: str) -> str | None:
    parts: list[str] = []
    alarm_events = [fact for fact in bundle.facts if fact.kind == "alarm_event"]
    if alarm_events:
        title = _alarm_title(alarm_events)
        phrases = _alarm_phrases(bundle)
        parts.append(f"平台记录 {len(alarm_events)} 次「{title}」告警（{'；'.join(phrases)}）")
    elif label != "分析未完成":
        parts.append("平台没有相关告警记录")
    if label == "发现异常":
        peer = _peer_chat_phrase(bundle)
        if peer:
            parts.append(peer)
        related = _related_chat_phrase(bundle)
        if related:
            parts.append(related)
    elif label in {"未发现异常", "无法判定"}:
        local = _local_chat_phrase(bundle)
        if local:
            parts.append(local)
    return "；".join(parts) + "。" if parts else None


def _suggestion(bundle: EvidenceBundle, label: str) -> str:
    if label == "分析未完成":
        return "确认数据源与配置后重试；本轮结果不能作为设备状态依据。"
    if label == "发现异常":
        if bundle.hypotheses:
            return f"{bundle.hypotheses[0].label}；结合告警记录安排现场确认。"
        return "按现场核查单检查本机运行状态、回风侧与滤网，并核对告警期间是否有维护操作。"
    if label == "未发现异常":
        return "按常规巡检继续观察；如再次出现告警，再复核本窗口数据。"
    return "先补采缺失测点或延长窗口后重试，不能按正常处理。"


def _gap_sentence(bundle: EvidenceBundle) -> str | None:
    if not bundle.gaps:
        return None
    first = bundle.gaps[0].item
    if bundle.status == "partial":
        return (
            f"本轮为部分完成，存在 {len(bundle.gaps)} 项数据缺口（如{first}）；"
            "详细证据见「查看核查依据」。"
        )
    return f"本轮记录 {len(bundle.gaps)} 项数据缺口（如{first}）。"


def _local_observation(bundle: EvidenceBundle) -> str | None:
    primary = next((fact for fact in bundle.facts if fact.kind == "timeseries"), None)
    if primary is None:
        return None
    stats = _fact_stats(primary)
    if stats is None:
        return f"本机{_metric_label(primary)}没有取得有效样本。"
    count, mean, _, maximum = stats
    unit = primary.unit or ""
    coverage = "数据完整" if (primary.coverage or "") == "complete" else "数据覆盖不完整"
    return (
        f"本机{_metric_label(primary)}：{count} 个有效样本，均值 {_fmt_number(mean)}{unit}，"
        f"最高 {_fmt_number(maximum)}{unit}；{coverage}。"
    )


def _peer_observation(bundle: EvidenceBundle) -> str | None:
    parts = []
    for fact in bundle.facts:
        if fact.kind != "comparison_timeseries":
            continue
        stats = _fact_stats(fact)
        if stats is None:
            continue
        _, mean, _, maximum = stats
        code = _device_code(fact.statement or "", fact.asset_id)
        parts.append(
            f"{code} 均值 {_fmt_number(mean)}{fact.unit or ''}、最高 {_fmt_number(maximum)}{fact.unit or ''}"
        )
    return f"同机房对照：{'；'.join(parts)}。" if parts else None


def _related_observation(bundle: EvidenceBundle) -> str | None:
    parts = []
    for fact in bundle.facts:
        if fact.kind != "related_timeseries":
            continue
        stats = _fact_stats(fact)
        if stats is None:
            continue
        _, mean, _, maximum = stats
        code = _device_code(fact.statement or "", fact.asset_id)
        parts.append(
            f"{code} {_metric_label(fact)}均值 {_fmt_number(mean)}{fact.unit or ''}、"
            f"最高 {_fmt_number(maximum)}{fact.unit or ''}"
        )
    return f"上游关联：{'；'.join(parts)}。" if parts else None


def _observation_lines(bundle: EvidenceBundle) -> list[str]:
    lines: list[str] = []
    local = _local_observation(bundle)
    if local:
        lines.append(f"- {local}")
    for fact in bundle.facts:
        if fact.kind != "optional_timeseries":
            continue
        stats = _fact_stats(fact)
        if stats is None:
            continue
        _, mean, _, maximum = stats
        lines.append(
            f"- 本机{_metric_label(fact)}：均值 {_fmt_number(mean)}{fact.unit or ''}，"
            f"最高 {_fmt_number(maximum)}{fact.unit or ''}。"
        )
    alarm_events = [fact for fact in bundle.facts if fact.kind == "alarm_event"]
    for fact in alarm_events:
        text = _humanize_times(fact.statement or "")
        title = _alarm_title([fact])
        times = re.findall(r"\d{2}:\d{2}", text)
        if "恢复" in text and len(times) >= 2:
            lines.append(f"- 平台告警「{title}」：{times[0]} 触发，{times[1]} 恢复。")
        elif times:
            lines.append(f"- 平台告警「{title}」：{times[0]} 触发，截至查询时点未恢复。")
    if not alarm_events:
        lines.append("- 平台告警：本轮窗口内没有相关告警记录（无告警不代表设备正常）。")
    peer = _peer_observation(bundle)
    if peer:
        lines.append(f"- {peer}")
    related = _related_observation(bundle)
    if related:
        lines.append(f"- {related}")
    return lines


def _check_basis_lines(bundle: EvidenceBundle) -> list[str]:
    if not bundle.sop_snippets:
        return ["- 本轮未取得适用的 SOP 或手册；建议按现场规程核查。"]
    lines = []
    for snippet in bundle.sop_snippets:
        section = f" / {snippet.section}" if snippet.section else ""
        text = snippet.text.strip()
        if len(text) > 180:
            text = text[:179].rstrip() + "…"
        lines.append(f"- 《{snippet.title}》{section}：{text}")
    return lines


def build_evidence_brief(
    bundle: EvidenceBundle,
    *,
    analysis_context: dict[str, Any],
    expanded_plan: dict[str, Any] | None = None,
) -> str:
    """面向工程师的核查摘要：结论、关键观测、数据缺口与检查依据。"""
    context = analysis_context or {}
    subject = context.get("analysis_subject") or bundle.analysis_subject
    window_text = _window_text(bundle.evidence_window)
    label, detail = _conclusion(bundle)
    lines = ["## 核查结论", f"**结论：{label}。** {detail}"]
    if bundle.status == "partial":
        lines.append(f"完成度：部分完成，存在 {len(bundle.gaps)} 项数据缺口。")
    elif bundle.status == "completed":
        lines.append("完成度：本轮取证完整。")
    lines.append(f"对象：{subject}；窗口：{window_text}。")
    if bundle.anomaly_claim:
        lines.append(f"用户描述（未核实）：{_humanize_times(bundle.anomaly_claim.statement)}")
    lines.append("")
    lines.append("## 关键观测")
    observations = _observation_lines(bundle)
    lines.extend(observations or ["- 本轮没有取得可复核的关键观测。"])
    lines.append("")
    lines.append("## 数据缺口")
    if bundle.gaps:
        for gap in bundle.gaps:
            impact = gap.impact.strip()
            if not impact.endswith("。"):
                impact += "。"
            lines.append(f"- {gap.item}：{impact}")
    else:
        lines.append("- 本轮未记录数据缺口。")
    if bundle.topology_is_current_snapshot and not any(
        gap.type == "topology_current_snapshot_only" for gap in bundle.gaps
    ):
        lines.append("- 拓扑关系仅为当前登记快照，不能证明异常时段的历史连接状态。")
    lines.append("")
    lines.append("## 检查依据")
    lines.extend(_check_basis_lines(bundle))
    lines.append("")
    lines.append("检查依据仅用于核对排查方向，不作为本次故障的根因证据。")
    return "\n".join(lines)


def build_chat_summary(
    bundle: EvidenceBundle,
    *,
    analysis_context: dict[str, Any],
    expanded_plan: dict[str, Any] | None = None,
) -> str:
    """面向对话的结论摘要：先给结论，再给依据、建议与数据缺口。"""
    label, detail = _conclusion(bundle)
    lines = [f"结论：{label}。{detail}"]
    basis = _chat_basis(bundle, label)
    if basis:
        lines.append(f"依据：{basis}")
    lines.append(f"建议：{_suggestion(bundle, label)}")
    gap_text = _gap_sentence(bundle)
    if gap_text:
        lines.append(f"说明：{gap_text}")
    return "\n".join(lines)


def _allowed_ids(bundle: EvidenceBundle) -> set[str]:
    ids = {"claim_01"} if bundle.anomaly_claim else set()
    ids |= {item.id for item in bundle.anomaly_assertions}
    ids |= {item.id for item in bundle.facts}
    ids |= {item.id for item in bundle.features}
    ids |= {item.id for item in bundle.hypotheses}
    ids |= {item.id for item in bundle.gaps}
    ids |= {item.chunk_id for item in bundle.sop_snippets}
    ids |= {gap.id for gap in bundle.gaps}
    return ids


def _allowed_decimals(bundle: EvidenceBundle) -> set[str]:
    text = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False)
    return set(_DECIMAL.findall(text))


def validate_report(text: str, bundle: EvidenceBundle) -> list[str]:
    errors: list[str] = []
    positions = [text.find(section) for section in _SECTIONS]
    if any(position < 0 for position in positions):
        errors.append("missing_required_section")
    elif positions != sorted(positions):
        errors.append("section_order_invalid")
    if bundle.fallback_used and "未匹配专项分析场景，使用本机兜底取证。" not in text:
        errors.append("fallback_phrase_missing")
    if any(phrase in text for phrase in _PROHIBITED):
        errors.append("prohibited_root_cause_phrase")
    allowed = _allowed_ids(bundle)
    citations = _CITATION.findall(text)
    unknown = [citation for citation in citations if citation not in allowed]
    if unknown:
        errors.append(f"unknown_citation:{unknown[0]}")
    if not any(citation.startswith("ev_") for citation in citations):
        errors.append("fact_citation_missing")
    allowed_decimals = _allowed_decimals(bundle)
    unknown_decimals = [value for value in _DECIMAL.findall(text) if value not in allowed_decimals]
    if unknown_decimals:
        errors.append(f"number_not_in_bundle:{unknown_decimals[0]}")
    return errors


async def compose(
    *,
    bundle: EvidenceBundle,
    analysis_context: dict[str, Any],
    expanded_plan: dict[str, Any] | None,
    llm: LLMClient | None,
) -> tuple[str, str, list[str]]:
    template = build_template_report(
        bundle, analysis_context=analysis_context, expanded_plan=expanded_plan
    )
    warnings: list[str] = []
    if llm is None or not llm.enabled:
        return template, "template", warnings
    try:
        text = await llm.chat(
            system="你是运维助手的告警分析报告生成器，只能依据证据包写作。",
            user=build_report_prompt(bundle.model_dump(mode="json")),
        )
        errors = validate_report(text, bundle)
        if errors:
            warnings.append("report_validation_failed:" + ";".join(errors))
            return template, "template", warnings
        return text.strip(), "llm", warnings
    except (LLMError, ValueError) as exc:
        warnings.append(f"report_llm_failed:{type(exc).__name__}")
        return template, "template", warnings
