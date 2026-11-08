from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.adapters.fixtures import FixtureStore
from app.schemas.execution import ConfirmationCandidate, TimeWindow
from app.schemas.intent import Intent
from app.services.access import AccessContext
from app.services.time_windows import (
    UnsupportedTimeExpression,
    latest_analysis_window,
    resolve_window,
)
from app.settings import settings


def normalize_text(value: str) -> str:
    return re.sub(r"[\s\-_]+", "", value).lower()


def format_device_display(name: str | None, room_name: str | None) -> str:
    """设备展示名；名称已含机房时不再重复追加。"""
    clean_name = (name or "").strip()
    clean_room = (room_name or "").strip()
    if not clean_name:
        return clean_room or "未知设备"
    if not clean_room or clean_room in re.sub(r"\s+", "", clean_name):
        return clean_name
    return f"{clean_name}（{clean_room}）"


_LIVE_TIME_WORDS = {"现在", "当前", "实时", "此刻"}


class ResolutionIssue(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        candidates: list[ConfirmationCandidate] | None = None,
        field_name: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.candidates = candidates or []
        self.field_name = field_name
        self.details = details or {}


@dataclass(frozen=True)
class ResolvedRoom:
    room_id: str
    room_name: str


@dataclass(frozen=True)
class ResolvedDevice:
    asset_id: str
    device: dict[str, Any]
    display_name: str


@dataclass(frozen=True)
class ResolvedMetric:
    metric_code: str
    name: str
    unit: str | None


@dataclass(frozen=True)
class ResolvedAlarmFilter:
    alarm_type_ids: list[str]
    metric_code: str | None
    anomaly_kind: str | None
    label: str


@dataclass(frozen=True)
class ResolvedPoint:
    point_id: str
    unit: str | None
    channel: str


@dataclass
class ResolutionContext:
    store: FixtureStore
    access: AccessContext
    metric_dictionary: dict[str, Any]
    alarm_dictionary: dict[str, Any]
    matched_rooms: list[ResolvedRoom] = field(default_factory=list)

    def visible_devices(self) -> list[dict[str, Any]]:
        return self.store.visible_devices(self.access)


def resolve_room(text: str | None, context: ResolutionContext) -> ResolvedRoom | None:
    if not text:
        return None
    target = normalize_text(text)
    rooms: dict[str, ResolvedRoom] = {}
    for device in context.visible_devices():
        aliases = [device.get("room_name", ""), *device.get("room_aliases", [])]
        if any(normalize_text(alias) == target for alias in aliases if alias):
            rooms[device["room_id"]] = ResolvedRoom(device["room_id"], device["room_name"])
    if not rooms:
        raise ResolutionIssue(
            "room_not_found",
            f"未找到机房“{text}”，请确认机房名称。",
            field_name="room",
        )
    if len(rooms) > 1:
        candidates = [
            ConfirmationCandidate(id=f"room:{room.room_id}", label=room.room_name, metadata={"room_id": room.room_id})
            for room in rooms.values()
        ]
        raise ResolutionIssue("room_ambiguous", f"“{text}”对应多个机房，请确认。", candidates=candidates, field_name="room")
    return next(iter(rooms.values()))


def _device_candidates(text: str, room: ResolvedRoom | None, context: ResolutionContext) -> list[dict[str, Any]]:
    target = normalize_text(text)
    devices = context.visible_devices()
    if room is not None:
        devices = [device for device in devices if device.get("room_id") == room.room_id]
    matched: list[dict[str, Any]] = []
    for device in devices:
        aliases = [device.get("device_code", ""), device.get("name", ""), *device.get("aliases", [])]
        normalized_aliases = [normalize_text(alias) for alias in aliases if alias]
        if target in normalized_aliases:
            matched.append(device)
    if matched:
        return matched
    # 只在精确别名未命中时尝试“输入包含设备编码”，避免把相近设备当唯一实例。
    for device in devices:
        code = normalize_text(device.get("device_code", ""))
        if code and len(code) >= 3 and code in target:
            matched.append(device)
    return matched


def resolve_device(text: str | None, room: ResolvedRoom | None, context: ResolutionContext) -> ResolvedDevice | None:
    if not text:
        return None
    matched = _device_candidates(text, room, context)
    if not matched:
        scope = f"（{room.room_name}）" if room else ""
        raise ResolutionIssue(
            "device_not_found",
            f"未在授权范围{scope}内找到设备“{text}”，请确认设备编码或名称。",
            field_name="device",
        )
    if len(matched) > 1:
        candidates = [
            ConfirmationCandidate(
                id=f"asset:{device['asset_id']}",
                label=f"{format_device_display(device.get('name'), device.get('room_name'))} / {device.get('device_code')}",
                asset_id=device["asset_id"],
                metadata={
                    "room_id": device.get("room_id"),
                    "room_name": device.get("room_name"),
                    "device_code": device.get("device_code"),
                },
            )
            for device in sorted(matched, key=lambda item: item["asset_id"])
        ]
        raise ResolutionIssue(
            "device_ambiguous",
            f"“{text}”对应多个设备，请选择具体机房和设备。",
            candidates=candidates,
            field_name="device",
        )
    device = matched[0]
    return ResolvedDevice(
        asset_id=device["asset_id"],
        device=device,
        display_name=format_device_display(device.get("name"), device.get("room_name")),
    )


def resolve_metric(text: str | None, context: ResolutionContext) -> ResolvedMetric | None:
    if not text:
        return None
    raw = text.strip()
    target = normalize_text(raw)
    exact: list[tuple[str, dict[str, Any]]] = []
    for code, spec in context.metric_dictionary.items():
        aliases = [code, spec.get("name", ""), *spec.get("aliases", [])]
        if any(normalize_text(alias) == target for alias in aliases if alias):
            exact.append((code, spec))
    if not exact:
        # 允许“送风温度值”这类带修饰的表达，取最长别名匹配。
        for code, spec in context.metric_dictionary.items():
            aliases = [spec.get("name", ""), *spec.get("aliases", [])]
            if any(alias and normalize_text(alias) in target for alias in aliases):
                exact.append((code, spec))
    unique = {code: spec for code, spec in exact}
    if not unique:
        raise ResolutionIssue("metric_not_found", f"未注册指标“{text}”，请确认指标名称。", field_name="metric")
    if len(unique) > 1:
        candidates = [
            ConfirmationCandidate(
                id=f"metric:{code}",
                label=spec.get("name", code),
                metadata={"metric_code": code, "unit": spec.get("unit")},
            )
            for code, spec in sorted(unique.items())
        ]
        raise ResolutionIssue("metric_ambiguous", f"“{text}”对应多个指标，请确认。", candidates=candidates, field_name="metric")
    code, spec = next(iter(unique.items()))
    return ResolvedMetric(metric_code=code, name=spec.get("name", code), unit=spec.get("unit"))


def resolve_alarm_filter(
    text: str | None,
    *,
    devices: list[dict[str, Any]],
    context: ResolutionContext,
) -> ResolvedAlarmFilter | None:
    if not text:
        return None
    anomaly_kind: str | None = None
    metric: ResolvedMetric | None = None
    alias_entry = context.alarm_dictionary.get("alias_index", {}).get(text.strip())
    if alias_entry:
        metric_code = alias_entry.get("metric_code")
        anomaly_kind = alias_entry.get("anomaly_kind")
        spec = context.metric_dictionary.get(metric_code)
        if spec:
            metric = ResolvedMetric(metric_code=metric_code, name=spec.get("name", metric_code), unit=spec.get("unit"))
    if metric is None:
        metric = resolve_metric(text, context)
    assert metric is not None

    device_ids = {device["asset_id"] for device in devices}
    definitions: list[dict[str, Any]] = []
    for device in context.visible_devices():
        if device_ids and device["asset_id"] not in device_ids:
            continue
        for definition in context.store.alarm_definitions_for(device["asset_id"]):
            if definition.get("metric_code") != metric.metric_code:
                continue
            if anomaly_kind and definition.get("anomaly_kind") != anomaly_kind:
                continue
            definitions.append(definition)
    if not definitions:
        raise ResolutionIssue(
            "alarm_type_not_found",
            f"未找到与“{text}”匹配的已登记告警类型。",
            field_name="alarm",
        )
    unique = {definition["alarm_type_id"]: definition for definition in definitions}
    if len(unique) > 1 and anomaly_kind is None:
        candidates = [
            ConfirmationCandidate(
                id=f"alarm:{alarm_type_id}",
                label=f"{definition.get('name')}（{definition.get('anomaly_kind')}）",
                alarm_type_id=alarm_type_id,
                metadata={"metric_code": definition.get("metric_code"), "anomaly_kind": definition.get("anomaly_kind")},
            )
            for alarm_type_id, definition in sorted(unique.items())
        ]
        raise ResolutionIssue("alarm_ambiguous", f"“{text}”对应多个告警类型，请确认。", candidates=candidates, field_name="alarm")
    first = next(iter(unique.values()))
    return ResolvedAlarmFilter(
        alarm_type_ids=sorted(unique),
        metric_code=first.get("metric_code"),
        anomaly_kind=anomaly_kind or first.get("anomaly_kind"),
        label=first.get("name") or text,
    )


def resolve_point(asset_id: str, metric_code: str, context: ResolutionContext) -> ResolvedPoint:
    bindings = context.store.points_for(asset_id, metric_code)
    if not bindings:
        raise ResolutionIssue(
            "point_binding_missing",
            f"设备缺少指标 {metric_code} 的测点绑定。",
            field_name="metric",
            details={"asset_id": asset_id, "metric_code": metric_code},
        )
    channels = {binding["channel"] for binding in bindings}
    if len(bindings) > 1:
        candidates = [
            ConfirmationCandidate(
                id=f"point:{binding['point_id']}",
                label=f"通道 {binding['channel']} / {binding['point_id']}",
                asset_id=asset_id,
                point_id=binding["point_id"],
                metadata={"channel": binding["channel"], "unit": binding.get("unit")},
            )
            for binding in bindings
        ]
        raise ResolutionIssue(
            "binding_ambiguous",
            f"指标 {metric_code} 存在多个通道，请选择测点。",
            candidates=candidates,
            field_name="metric",
        )
    binding = bindings[0]
    return ResolvedPoint(point_id=binding["point_id"], unit=binding.get("unit"), channel=binding["channel"])


def resolve_task_window(
    intent: Intent,
    time_text: str | None,
    query_time,
) -> tuple[TimeWindow | None, str | None]:
    """返回 (窗口, 默认规则说明)。窗口为 None 表示即时查询/活动告警。"""
    if intent is Intent.metric_latest:
        if time_text and time_text.strip() not in _LIVE_TIME_WORDS:
            # 实时读数只使用查询锚点；明确历史时间应改走趋势，不能混用。
            raise ResolutionIssue("latest_with_historical_time", "实时读数不接受历史时间，请改用趋势查询。", field_name="time")
        return None, None
    if intent is Intent.metric_trend:
        if not time_text:
            raise ResolutionIssue("time_required", "请补充趋势查询的时间范围，例如“最近两小时”或“今天”。", field_name="time")
        return resolve_window(time_text, query_time), None
    if intent is Intent.alarm_list:
        if not time_text or time_text.strip() in _LIVE_TIME_WORDS:
            return None, "no_time_means_active_alarms"
        return resolve_window(time_text, query_time), None
    if intent is Intent.alarm_count:
        if not time_text:
            return resolve_window("今天", query_time), "default_today_for_alarm_count"
        return resolve_window(time_text, query_time), None
    if intent is Intent.alarm_analysis:
        if not time_text:
            return latest_analysis_window(query_time), "configured_analysis_window"
        window = resolve_window(time_text, query_time)
        if (window.end - window.start).total_seconds() > settings.hard_max_window_seconds:
            raise ResolutionIssue(
                "analysis_window_too_wide",
                "分析窗口超过配置上限，请缩小时间范围（例如“最近两小时”）。",
                field_name="time",
            )
        return window, None
    return None, None


def render_time_issue(exc: UnsupportedTimeExpression) -> ResolutionIssue:
    return ResolutionIssue("time_unsupported", "暂不支持该时间表达，请使用“今天”“昨天”“最近两小时”等明确范围。", field_name="time")
