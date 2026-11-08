from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.adapters.fixtures import FixtureStore
from app.adapters.mock_monitoring import MockDataError, MockMonitoringAdapter
from app.schemas.analysis import AnalysisContext, AnomalyClaim
from app.schemas.execution import (
    ConfirmationCandidate,
    CountPolicy,
    ExecutionPlan,
    ExecutionTask,
    PendingConfirmation,
    SchedulingStatus,
    TimeWindow,
)
from app.schemas.intent import Intent, IntentPlan, SemanticRelation, SemanticTask
from app.scenarios.registry import ConfigStore, PlanResolutionError, PlanSelection, select_plan
from app.services.access import AccessContext
from app.services.entity_resolution import (
    ResolutionContext,
    ResolutionIssue,
    ResolvedAlarmFilter,
    ResolvedDevice,
    ResolvedMetric,
    ResolvedPoint,
    ResolvedRoom,
    format_device_display,
    normalize_text,
    render_time_issue,
    resolve_alarm_filter,
    resolve_device,
    resolve_metric,
    resolve_point,
    resolve_room,
    resolve_task_window,
)
from app.services.semantic_parser import validate_semantic_structure
from app.services.time_windows import UnsupportedTimeExpression, window_dict
from app.settings import settings

_CLARIFICATION_CODES = {
    "time_required",
    "device_required",
    "device_metric_required",
    "device_not_found",
    "device_ambiguous",
    "device_out_of_scope",
    "metric_required",
    "metric_not_found",
    "metric_ambiguous",
    "metric_out_of_scope",
    "analysis_target_missing",
    "room_not_found",
    "room_ambiguous",
    "time_unsupported",
    "latest_with_historical_time",
    "analysis_window_too_wide",
    "alarm_type_not_found",
    "alarm_ambiguous",
    "binding_ambiguous",
    "point_out_of_scope",
}


@dataclass(frozen=True)
class ConfirmationSelection:
    semantic_id: str
    field: str
    asset_id: str | None = None
    point_id: str | None = None
    metric_code: str | None = None
    alarm_type_ids: list[str] | None = None


@dataclass
class CompileDependencies:
    store: FixtureStore
    config_store: ConfigStore
    monitoring: MockMonitoringAdapter
    access: AccessContext
    metric_dictionary: dict[str, Any]
    alarm_dictionary: dict[str, Any]
    session_revision: int = 1


@dataclass
class ResolvedSlots:
    room: ResolvedRoom | None = None
    device: ResolvedDevice | None = None
    metric: ResolvedMetric | None = None
    time_window: TimeWindow | None = None
    default_rule: str | None = None


@dataclass
class CompileResult:
    plan: ExecutionPlan
    resolutions: dict[str, ResolvedSlots] = field(default_factory=dict)


class PlanCompiler:
    def __init__(self, deps: CompileDependencies):
        self.deps = deps
        self.resolution_context = ResolutionContext(
            store=deps.store,
            access=deps.access,
            metric_dictionary=deps.metric_dictionary,
            alarm_dictionary=deps.alarm_dictionary,
        )

    def compile(
        self,
        *,
        intent_plan: IntentPlan,
        request_id: str,
        session_id: str,
        fixed_query_time,
        selections: list[ConfirmationSelection] | None = None,
    ) -> CompileResult:
        validate_semantic_structure(intent_plan)
        fixed_query_time = fixed_query_time
        if intent_plan.ask:
            return CompileResult(
                plan=ExecutionPlan(
                    request_id=request_id,
                    session_id=session_id,
                    semantic_plan=intent_plan,
                    fixed_query_time=fixed_query_time,
                    tasks=[],
                    errors=[
                        {
                            "code": "NEED_CLARIFICATION",
                            "message": intent_plan.ask,
                            "requires_user_input": True,
                        }
                    ],
                    pending_confirmations=[],
                )
            )

        selection_map = {item.semantic_id: item for item in (selections or [])}
        relation_by_target = {relation.target: relation for relation in intent_plan.relations}
        tasks: list[ExecutionTask] = []
        errors: list[dict[str, Any]] = []
        confirmations: list[PendingConfirmation] = []
        resolutions: dict[str, ResolvedSlots] = {}
        task_states: dict[str, ResolvedSlots] = {}

        for semantic_task in intent_plan.tasks:
            task_id = f"t_{semantic_task.semantic_id}"
            selection = selection_map.get(semantic_task.semantic_id)
            relation = relation_by_target.get(semantic_task.semantic_id)
            inherited = self._inherited_slots(relation, task_states)

            if semantic_task.intent is Intent.unsupported:
                tasks.append(
                    ExecutionTask(
                        task_id=task_id,
                        semantic_id=semantic_task.semantic_id,
                        intent=semantic_task.intent,
                        scheduling_status=SchedulingStatus.blocked,
                        status="blocked",
                        input_bindings={
                            "room": semantic_task.room,
                            "device": semantic_task.device,
                            "metric": semantic_task.metric,
                            "time": semantic_task.time,
                            "reason": "unsupported_operation",
                            "clarification": "暂不支持该操作类请求，运维助手只提供查询、分析和知识问答。",
                        },
                    )
                )
                errors.append(
                    {
                        "code": "UNSUPPORTED_OPERATION",
                        "task_id": task_id,
                        "message": "暂不支持该操作类请求，运维助手只提供查询、分析和知识问答。",
                        "requires_user_input": False,
                    }
                )
                continue

            if semantic_task.intent is Intent.knowledge_qa:
                task = ExecutionTask(
                    task_id=task_id,
                    semantic_id=semantic_task.semantic_id,
                    intent=semantic_task.intent,
                    scheduling_status=SchedulingStatus.ready,
                    status="pending",
                    validated_tool_calls=["knowledge.search"],
                    input_bindings={
                        "room": semantic_task.room,
                        "device": semantic_task.device,
                        "metric": semantic_task.metric,
                        "time": semantic_task.time,
                    },
                )
                tasks.append(task)
                resolutions[semantic_task.semantic_id] = ResolvedSlots()
                continue

            try:
                if semantic_task.intent in {Intent.metric_latest, Intent.metric_trend}:
                    task, slots = self._compile_metric_task(
                        semantic_task, task_id, fixed_query_time, selection
                    )
                elif semantic_task.intent in {Intent.alarm_list, Intent.alarm_count}:
                    task, slots = self._compile_alarm_task(
                        semantic_task, task_id, fixed_query_time, selection
                    )
                elif semantic_task.intent is Intent.alarm_analysis:
                    task, slots = self._compile_analysis_task(
                        semantic_task,
                        task_id,
                        fixed_query_time,
                        selection,
                        inherited,
                        request_id,
                        intent_plan.ask or "",
                    )
                else:
                    raise ResolutionIssue("intent_unsupported", "不支持的意图")
            except ResolutionIssue as exc:
                supports_confirmation = bool(exc.candidates) and exc.field_name in {"device", "metric", "alarm"}
                asks_user = supports_confirmation or exc.code in _CLARIFICATION_CODES
                waiting_task = ExecutionTask(
                    task_id=task_id,
                    semantic_id=semantic_task.semantic_id,
                    intent=semantic_task.intent,
                    scheduling_status=(
                        SchedulingStatus.waiting_user if asks_user else SchedulingStatus.blocked
                    ),
                    status=(
                        "needs_confirmation"
                        if supports_confirmation
                        else "waiting_user"
                        if asks_user
                        else "blocked"
                    ),
                    input_bindings={
                        "room": semantic_task.room,
                        "device": semantic_task.device,
                        "metric": semantic_task.metric,
                        "time": semantic_task.time,
                        "error_code": exc.code,
                        "clarification": str(exc),
                    },
                )
                tasks.append(waiting_task)
                errors.append(
                    {
                        "code": exc.code,
                        "task_id": task_id,
                        "message": str(exc),
                        "requires_user_input": asks_user,
                        "field_name": exc.field_name,
                    }
                )
                if supports_confirmation:
                    confirmations.append(
                        PendingConfirmation(
                            confirmation_id=f"cf_{request_id}_{len(confirmations) + 1}",
                            request_id=request_id,
                            session_id=session_id,
                            task_id=task_id,
                            field=exc.field_name,  # type: ignore[arg-type]
                            prompt=str(exc),
                            candidates=exc.candidates,
                            created_at=fixed_query_time,
                            expires_at=fixed_query_time + timedelta(minutes=15),
                            session_revision=self.deps.session_revision,
                        )
                    )
                continue
            except PlanResolutionError as exc:
                tasks.append(
                    ExecutionTask(
                        task_id=task_id,
                        semantic_id=semantic_task.semantic_id,
                        intent=semantic_task.intent,
                        scheduling_status=SchedulingStatus.blocked,
                        status="blocked",
                        input_bindings={"error_code": exc.code, "clarification": str(exc)},
                    )
                )
                errors.append(
                    {
                        "code": exc.code.upper(),
                        "task_id": task_id,
                        "message": str(exc),
                        "requires_user_input": False,
                    }
                )
                continue
            except UnsupportedTimeExpression:
                issue = render_time_issue(UnsupportedTimeExpression("unsupported"))
                tasks.append(
                    ExecutionTask(
                        task_id=task_id,
                        semantic_id=semantic_task.semantic_id,
                        intent=semantic_task.intent,
                        scheduling_status=SchedulingStatus.waiting_user,
                        status="waiting_user",
                        input_bindings={
                            "error_code": issue.code,
                            "clarification": str(issue),
                        },
                    )
                )
                errors.append(
                    {
                        "code": issue.code,
                        "task_id": task_id,
                        "message": str(issue),
                        "requires_user_input": True,
                        "field_name": issue.field_name,
                    }
                )
                continue

            if relation is not None:
                source_task_id = f"t_{relation.source}"
                task.dependencies = [source_task_id]
                task.scheduling_status = SchedulingStatus.waiting_dependency
                task.input_bindings["relation"] = {
                    "type": relation.type,
                    "source_semantic_id": relation.source,
                    "condition": relation.condition,
                }
                task.run_condition = {"type": "analyze_result", "condition": relation.condition}
            tasks.append(task)
            resolutions[semantic_task.semantic_id] = slots
            task_states[semantic_task.semantic_id] = slots

        return CompileResult(
            plan=ExecutionPlan(
                request_id=request_id,
                session_id=session_id,
                semantic_plan=intent_plan,
                fixed_query_time=fixed_query_time,
                tasks=tasks,
                errors=errors,
                pending_confirmations=confirmations,
            ),
            resolutions=resolutions,
        )

    def _inherited_slots(
        self, relation: SemanticRelation | None, task_states: dict[str, ResolvedSlots]
    ) -> ResolvedSlots | None:
        if relation is None:
            return None
        return task_states.get(relation.source)

    def _resolve_room_and_device(
        self,
        semantic_task: SemanticTask,
        selection: ConfirmationSelection | None,
        inherited: ResolvedSlots | None,
    ) -> tuple[ResolvedRoom | None, ResolvedDevice | None]:
        if selection and selection.field == "device" and selection.asset_id:
            device_data = self.deps.store.device_by_asset(selection.asset_id, self.deps.access)
            if device_data is None:
                raise ResolutionIssue("device_out_of_scope", "确认的设备不在授权范围内", field_name="device")
            device = ResolvedDevice(
                asset_id=device_data["asset_id"],
                device=device_data,
                display_name=format_device_display(device_data.get("name"), device_data.get("room_name")),
            )
            room = ResolvedRoom(device_data["room_id"], device_data["room_name"])
            return room, device

        room = resolve_room(semantic_task.room, self.resolution_context)
        if semantic_task.device:
            device = resolve_device(semantic_task.device, room, self.resolution_context)
        elif inherited and inherited.device:
            device = inherited.device
            room = inherited.room
        else:
            device = None
        return room, device

    def _resolve_metric(
        self,
        semantic_task: SemanticTask,
        selection: ConfirmationSelection | None,
    ) -> ResolvedMetric | None:
        if selection and selection.field == "metric" and selection.metric_code:
            spec = self.deps.metric_dictionary.get(selection.metric_code)
            if spec is None:
                raise ResolutionIssue("metric_not_found", "确认的指标未注册", field_name="metric")
            return ResolvedMetric(
                metric_code=selection.metric_code,
                name=spec.get("name", selection.metric_code),
                unit=spec.get("unit"),
            )
        return resolve_metric(semantic_task.metric, self.resolution_context)

    def _resolve_point_with_selection(
        self,
        asset_id: str,
        metric_code: str,
        selection: ConfirmationSelection | None,
    ) -> ResolvedPoint:
        if selection and selection.point_id:
            for binding in self.deps.store.points_for(asset_id, metric_code):
                if binding["point_id"] == selection.point_id:
                    return ResolvedPoint(
                        point_id=binding["point_id"],
                        unit=binding.get("unit"),
                        channel=binding["channel"],
                    )
            raise ResolutionIssue("point_out_of_scope", "确认的测点不属于该设备指标", field_name="metric")
        return resolve_point(asset_id, metric_code, self.resolution_context)

    def _compile_metric_task(
        self,
        semantic_task: SemanticTask,
        task_id: str,
        fixed_query_time,
        selection: ConfirmationSelection | None,
    ) -> tuple[ExecutionTask, ResolvedSlots]:
        room: ResolvedRoom | None = None
        device: ResolvedDevice | None = None
        device_issue: ResolutionIssue | None = None
        try:
            room, device = self._resolve_room_and_device(semantic_task, selection, None)
        except ResolutionIssue as exc:
            device_issue = exc

        metric: ResolvedMetric | None = None
        metric_issue: ResolutionIssue | None = None
        try:
            metric = self._resolve_metric(semantic_task, selection)
        except ResolutionIssue as exc:
            metric_issue = exc

        if device_issue is not None and (
            device_issue.candidates or device_issue.code != "device_required"
        ):
            raise device_issue
        if device is not None and metric_issue is not None:
            if metric_issue.candidates:
                raise metric_issue
            raise self._metric_clarification(semantic_task, device, metric_issue)
        if device is None or metric is None:
            raise self._missing_slots_clarification(
                semantic_task, device=device, metric=metric
            )
        point = self._resolve_point_with_selection(device.asset_id, metric.metric_code, selection)
        try:
            window, default_rule = resolve_task_window(
                semantic_task.intent, semantic_task.time, fixed_query_time
            )
        except UnsupportedTimeExpression:
            issue = render_time_issue(UnsupportedTimeExpression("unsupported"))
            raise issue
        task = ExecutionTask(
            task_id=task_id,
            semantic_id=semantic_task.semantic_id,
            intent=semantic_task.intent,
            scheduling_status=SchedulingStatus.ready,
            status="pending",
            device_refs=[self._device_ref(device)],
            metric_binding={
                "metric_code": metric.metric_code,
                "metric_name": metric.name,
                "point_id": point.point_id,
                "unit": point.unit,
                "channel": point.channel,
            },
            time_window=window,
            validated_tool_calls=[
                "monitoring.get_latest" if semantic_task.intent is Intent.metric_latest else "monitoring.get_series"
            ],
            input_bindings={
                "room": semantic_task.room,
                "device": semantic_task.device,
                "metric": semantic_task.metric,
                "time": semantic_task.time,
                "resolved_room_id": room.room_id if room else None,
                "resolved_asset_id": device.asset_id,
                "select_metric": metric.metric_code,
                "default_rule": default_rule,
            },
        )
        return task, ResolvedSlots(
            room=room,
            device=device,
            metric=metric,
            time_window=window,
            default_rule=default_rule,
        )

    def _available_metric_names(
        self, semantic_task: SemanticTask, device: ResolvedDevice | None, limit: int = 3
    ) -> list[str]:
        hint = normalize_text(semantic_task.metric or "")
        type_code = (device.device.get("type_code") if device is not None else None)
        names: list[str] = []
        for code, spec in self.deps.metric_dictionary.items():
            device_types = spec.get("device_types") or []
            if type_code and device_types and type_code not in device_types:
                continue
            name = spec.get("name") or code
            if hint and hint not in normalize_text(name):
                continue
            names.append(name)
        if not names:
            for code, spec in self.deps.metric_dictionary.items():
                device_types = spec.get("device_types") or []
                if type_code and device_types and type_code not in device_types:
                    continue
                names.append(spec.get("name") or code)
        return sorted(
            dict.fromkeys(names),
            key=lambda name: (
                len(name),
                0 if "送风" in name else 1 if "回风" in name else 2,
                name,
            ),
        )[:limit]

    @staticmethod
    def _device_example(device: ResolvedDevice | None) -> str | None:
        if device is None:
            return None
        room = device.device.get("room_name")
        code = device.device.get("device_code") or device.display_name
        return f"{room} {code}".strip()

    def _example_device_data(self) -> dict[str, Any] | None:
        devices = self.deps.store.visible_devices(self.deps.access)
        if not devices:
            return None
        return next(
            (item for item in devices if item.get("type_code") == "precision_air_conditioner"),
            devices[0],
        )

    def _example_device(self) -> str | None:
        device = self._example_device_data()
        if device is None:
            return None
        room = device.get("room_name")
        code = device.get("device_code") or device.get("name")
        return f"{room} {code}".strip()

    def _missing_slots_clarification(
        self,
        semantic_task: SemanticTask,
        *,
        device: ResolvedDevice | None,
        metric: ResolvedMetric | None,
    ) -> ResolutionIssue:
        device_missing = device is None
        metric_missing = metric is None
        sample_device = device
        if sample_device is None:
            sample = self._example_device_data()
            if sample is not None:
                sample_device = ResolvedDevice(
                    sample["asset_id"],
                    sample,
                    format_device_display(sample.get("name"), sample.get("room_name")),
                )
        parts: list[str] = []
        if device_missing and metric_missing:
            parts.append("请补充要查询的设备和具体指标")
        elif device_missing:
            parts.append("请补充要查询哪台设备")
        else:
            parts.append("请补充要查询的具体指标")
        if metric_missing and sample_device is not None:
            names = self._available_metric_names(semantic_task, sample_device)
            if names:
                label = "可选指标" if not device_missing else "指标示例"
                parts.append(f"{label}：{'、'.join(names)}")

        example_device = self._device_example(device) or self._example_device()
        example_metric = metric.name if metric is not None else None
        if example_metric is None and sample_device is not None:
            names = self._available_metric_names(semantic_task, sample_device)
            example_metric = names[0] if names else None
        if example_device and example_metric:
            parts.append(f"例如：“{example_device} 的{example_metric}”")
        elif example_device:
            parts.append(f"例如：“{example_device}”")

        if device_missing and metric_missing:
            code = "device_metric_required"
        elif device_missing:
            code = "device_required"
        else:
            code = "metric_required"
        return ResolutionIssue(
            code,
            "；".join(parts) + "。",
            field_name="device" if device_missing else "metric",
        )

    def _metric_clarification(
        self,
        semantic_task: SemanticTask,
        device: ResolvedDevice,
        issue: ResolutionIssue,
    ) -> ResolutionIssue:
        names = self._available_metric_names(semantic_task, device)
        if names:
            message = f"请补充要查询的具体指标（可选：{'、'.join(names)}）"
        else:
            message = "请补充要查询的具体指标"
        example_device = self._device_example(device)
        if example_device and names:
            message += f"；例如：“{example_device} 的{names[0]}”"
        if issue.code == "metric_not_found" and semantic_task.metric:
            message = f"“{semantic_task.metric}”未对应到已登记指标。" + message
        return ResolutionIssue("metric_required", message + "。", field_name="metric")

    def _compile_alarm_task(
        self,
        semantic_task: SemanticTask,
        task_id: str,
        fixed_query_time,
        selection: ConfirmationSelection | None,
    ) -> tuple[ExecutionTask, ResolvedSlots]:
        room, device = self._resolve_room_and_device(semantic_task, selection, None)
        if device is not None:
            devices = [device.device]
        elif room is not None:
            devices = self.deps.store.devices_by_room(room.room_id, self.deps.access)
        else:
            devices = self.deps.store.visible_devices(self.deps.access)
        if semantic_task.intent is Intent.alarm_count and not semantic_task.metric and not (selection and selection.alarm_type_ids):
            raise ResolutionIssue(
                "metric_required",
                "统计告警次数需要明确告警类型，例如“高温告警”。",
                field_name="alarm",
            )
        try:
            alarm_filter = self._resolve_alarm_with_selection(semantic_task, devices, selection)
        except UnsupportedTimeExpression:
            raise render_time_issue(UnsupportedTimeExpression("unsupported"))
        count_kind = semantic_task.count_kind
        try:
            window, default_rule = resolve_task_window(
                semantic_task.intent,
                None if count_kind == "active" else semantic_task.time,
                fixed_query_time,
            )
        except UnsupportedTimeExpression:
            raise render_time_issue(UnsupportedTimeExpression("unsupported"))

        if semantic_task.intent is Intent.alarm_count:
            if count_kind == "active":
                policy = CountPolicy.active_at_query_time
                window = None
            elif count_kind == "recovered":
                policy = CountPolicy.recovered_in_window
            else:
                policy = CountPolicy.new_occurrences_in_window
        else:
            policy = None

        task = ExecutionTask(
            task_id=task_id,
            semantic_id=semantic_task.semantic_id,
            intent=semantic_task.intent,
            scheduling_status=SchedulingStatus.ready,
            status="pending",
            device_refs=[
                self._device_ref(
                    ResolvedDevice(
                        d["asset_id"],
                        d,
                        format_device_display(d.get("name"), d.get("room_name")),
                    )
                )
                for d in devices
            ],
            alarm_binding=(
                {
                    "alarm_type_ids": alarm_filter.alarm_type_ids,
                    "metric_code": alarm_filter.metric_code,
                    "anomaly_kind": alarm_filter.anomaly_kind,
                    "label": alarm_filter.label,
                }
                if alarm_filter
                else {"alarm_type_ids": [], "metric_code": None, "anomaly_kind": None, "label": None}
            ),
            time_window=window,
            count_policy=policy,
            validated_tool_calls=["monitoring.get_alarm_records"],
            input_bindings={
                "room": semantic_task.room,
                "device": semantic_task.device,
                "metric": semantic_task.metric,
                "time": semantic_task.time,
                "count_kind": count_kind,
                "mode": "active" if window is None and semantic_task.intent is Intent.alarm_list else "window",
                "resolved_room_id": room.room_id if room else None,
                "resolved_asset_id": device.asset_id if device else None,
                "default_rule": default_rule,
            },
        )
        slots = ResolvedSlots(
            room=room,
            device=device,
            metric=(
                ResolvedMetric(
                    metric_code=alarm_filter.metric_code,
                    name=alarm_filter.label,
                    unit=None,
                )
                if alarm_filter and alarm_filter.metric_code
                else None
            ),
            time_window=window,
            default_rule=default_rule,
        )
        return task, slots

    def _resolve_alarm_with_selection(
        self,
        semantic_task: SemanticTask,
        devices: list[dict[str, Any]],
        selection: ConfirmationSelection | None,
    ) -> ResolvedAlarmFilter | None:
        if selection and selection.alarm_type_ids:
            definitions = [
                definition
                for alarm_type_id in selection.alarm_type_ids
                if (definition := self.deps.store.alarm_definition(alarm_type_id)) is not None
            ]
            if not definitions:
                raise ResolutionIssue("alarm_type_not_found", "确认的告警类型不存在", field_name="alarm")
            first = definitions[0]
            return ResolvedAlarmFilter(
                alarm_type_ids=[definition["alarm_type_id"] for definition in definitions],
                metric_code=first.get("metric_code"),
                anomaly_kind=first.get("anomaly_kind"),
                label=first.get("name") or first["alarm_type_id"],
            )
        if not semantic_task.metric:
            return None
        try:
            return resolve_alarm_filter(semantic_task.metric, devices=devices, context=self.resolution_context)
        except ResolutionIssue as exc:
            if exc.code == "alarm_type_not_found" and semantic_task.intent is Intent.alarm_analysis:
                return None
            raise

    def _compile_analysis_task(
        self,
        semantic_task: SemanticTask,
        task_id: str,
        fixed_query_time,
        selection: ConfirmationSelection | None,
        inherited: ResolvedSlots | None,
        request_id: str,
        raw_query: str,
    ) -> tuple[ExecutionTask, ResolvedSlots]:
        room, device = self._resolve_room_and_device(semantic_task, selection, inherited)
        if device is None:
            raise ResolutionIssue(
                "analysis_target_missing",
                "请明确要分析的设备；如果分析上一轮结果，请说明“分析一下结果”。",
                field_name="device",
            )
        try:
            window, default_rule = resolve_task_window(
                semantic_task.intent, semantic_task.time, fixed_query_time
            )
        except UnsupportedTimeExpression:
            raise render_time_issue(UnsupportedTimeExpression("unsupported"))
        if window is None:
            raise ResolutionIssue("time_required", "请补充分析时间范围。", field_name="time")
        if semantic_task.time is None and inherited and inherited.time_window is not None:
            inherited_window = inherited.time_window
            max_seconds = settings.hard_max_window_seconds
            if (inherited_window.end - inherited_window.start).total_seconds() > max_seconds:
                window = TimeWindow(
                    start=inherited_window.end - timedelta(seconds=max_seconds),
                    end=inherited_window.end,
                )
                default_rule = "inherited_window_capped_to_analysis_limit"
            else:
                window = inherited_window
                default_rule = "inherited_from_related_task"

        metric: ResolvedMetric | None = None
        alarm_filter: ResolvedAlarmFilter | None = None
        try:
            alarm_filter = self._resolve_alarm_with_selection(semantic_task, [device.device], selection)
        except ResolutionIssue as exc:
            if exc.candidates:
                raise
            if semantic_task.metric:
                raise
        if alarm_filter is None and semantic_task.metric:
            metric = self._resolve_metric(semantic_task, selection)
        elif alarm_filter and alarm_filter.metric_code:
            spec = self.deps.metric_dictionary.get(alarm_filter.metric_code)
            metric = ResolvedMetric(
                metric_code=alarm_filter.metric_code,
                name=spec.get("name", alarm_filter.metric_code) if spec else alarm_filter.metric_code,
                unit=spec.get("unit") if spec else None,
            )

        if metric is None and inherited and inherited.metric:
            metric = inherited.metric
        if metric is None:
            raise ResolutionIssue(
                "analysis_target_missing",
                "请明确要分析的异常指标或告警类型，例如“送风温度高”或“高温告警”。",
                field_name="metric",
            )

        window_start = window.start
        window_end = window.end
        alarm_records: list[dict[str, Any]] = []
        if alarm_filter and alarm_filter.alarm_type_ids:
            all_records = self._alarm_records_for_analysis(
                device.asset_id, window_start, window_end
            )
            allowed = set(alarm_filter.alarm_type_ids)
            alarm_records = [record for record in all_records if record["alarm_type_id"] in allowed]
        else:
            alarm_records = self._alarm_records_for_analysis(
                device.asset_id, window_start, window_end
            )

        anomaly_kind = alarm_filter.anomaly_kind if alarm_filter else None
        match_basis = "no_match"
        if alarm_records:
            match_basis = "platform_alarm"
            if anomaly_kind is None:
                latest_record = max(alarm_records, key=lambda item: item["occurred_at"])
                definition = self.deps.store.alarm_definition(latest_record["alarm_type_id"])
                anomaly_kind = definition.get("anomaly_kind") if definition else None
        if anomaly_kind is None:
            if "高" in (semantic_task.metric or ""):
                anomaly_kind = "high"
                match_basis = "user_statement"
            elif "低" in (semantic_task.metric or ""):
                anomaly_kind = "low"
                match_basis = "user_statement"
        if anomaly_kind is not None and match_basis == "no_match":
            explicit_claim = any(
                word in (semantic_task.metric or "") for word in ("高", "低", "偏高", "偏低")
            )
            match_basis = "user_statement" if explicit_claim else "alarm_type_registry"
        if anomaly_kind is None:
            anomaly_kind = "unknown"

        claim: AnomalyClaim | None = None
        if match_basis == "user_statement":
            claim = AnomalyClaim(
                message_ref=f"user:{request_id}",
                statement=raw_query or f"{device.display_name} {semantic_task.metric or ''}",
            )

        try:
            bundle = self.deps.config_store.load()
        except Exception as exc:
            code = getattr(exc, "code", "config_unavailable")
            raise PlanResolutionError(code, str(exc)) from exc

        plan_selection: PlanSelection = select_plan(
            bundle,
            device=device.device,
            metric_code=metric.metric_code,
            match_anomaly_kind=anomaly_kind,
            match_basis=match_basis,
        )

        point: ResolvedPoint | None = None
        point_warning: str | None = None
        try:
            point = self._resolve_point_with_selection(device.asset_id, metric.metric_code, selection)
        except ResolutionIssue as exc:
            if exc.candidates:
                raise
            point_warning = f"POINT_BINDING_MISSING:{metric.metric_code}"

        analysis_context = AnalysisContext(
            tenant_id=device.device["tenant_id"],
            platform_instance_id=device.device["platform_instance_id"],
            asset_id=device.asset_id,
            device_type=device.device.get("type_code", ""),
            model_code=device.device.get("model_code"),
            cooling_mode=device.device.get("cooling_mode"),
            site_id=device.device.get("site_id"),
            room_id=device.device.get("room_id"),
            room_name=device.device.get("room_name"),
            capabilities=device.device.get("capabilities", []),
            metric_code=metric.metric_code,
            analysis_subject=f"{device.display_name} {metric.name}",
            anomaly_claim=claim,
            requested_anomaly_kind=anomaly_kind if match_basis == "user_statement" else None,
            match_anomaly_kind=anomaly_kind,
            match_basis=match_basis,
            fixed_query_time=fixed_query_time,
            evidence_window=window_dict(window),
            config_bundle_version=bundle.bundle_version,
        )

        expanded_plan = {
            "scenario_id": plan_selection.scenario_id,
            "scenario_version": plan_selection.scenario_version,
            "bundle_version": bundle.bundle_version,
            "limits": bundle.limits.model_dump(),
            "steps": [step.model_dump() for step in plan_selection.steps],
            "parameters": plan_selection.parameters.values,
            "parameter_set_ids": plan_selection.parameters.parameter_set_ids,
            "parameter_sources": plan_selection.parameters.sources,
            "override_basis": plan_selection.parameters.override_basis,
            "feature_profile": plan_selection.feature_profile,
            "feature_profile_version": plan_selection.profile_version,
            "feature_algorithm_versions": bundle.feature_profiles.profiles[
                plan_selection.feature_profile
            ].algorithm_versions,
            "fallback_used": plan_selection.fallback_used,
            "match_basis": match_basis,
            "match_anomaly_kind": anomaly_kind,
        }
        task = ExecutionTask(
            task_id=task_id,
            semantic_id=semantic_task.semantic_id,
            intent=semantic_task.intent,
            scheduling_status=SchedulingStatus.ready,
            status="pending",
            device_refs=[self._device_ref(device)],
            metric_binding={
                "metric_code": metric.metric_code,
                "metric_name": metric.name,
                "point_id": point.point_id if point else None,
                "unit": point.unit if point else metric.unit,
                "channel": point.channel if point else None,
                "gap": point_warning,
            },
            alarm_binding=(
                {
                    "alarm_type_ids": alarm_filter.alarm_type_ids,
                    "metric_code": alarm_filter.metric_code,
                    "anomaly_kind": alarm_filter.anomaly_kind,
                    "label": alarm_filter.label,
                    "platform_record_count": len(alarm_records),
                }
                if alarm_filter
                else {
                    "alarm_type_ids": [],
                    "metric_code": None,
                    "anomaly_kind": None,
                    "label": None,
                    "platform_record_count": len(alarm_records),
                }
            ),
            time_window=window,
            validated_tool_calls=self._analysis_tool_calls(plan_selection),
            config_bundle_version=bundle.bundle_version,
            scenario_version=plan_selection.scenario_version,
            snippet_versions=plan_selection.snippet_versions,
            expanded_plan=expanded_plan,
            plan_hash=plan_selection.plan_hash,
            feature_profile_version=plan_selection.profile_version,
            fallback_used=plan_selection.fallback_used,
            analysis_context=analysis_context.model_dump(mode="json"),
            input_bindings={
                "request_id": request_id,
                "room": semantic_task.room,
                "device": semantic_task.device,
                "metric": semantic_task.metric,
                "time": semantic_task.time,
                "resolved_room_id": room.room_id if room else None,
                "resolved_asset_id": device.asset_id,
                "select_metric": metric.metric_code,
                "default_rule": default_rule,
                "scenario_id": plan_selection.scenario_id,
                "fallback_used": plan_selection.fallback_used,
            },
        )
        return task, ResolvedSlots(
            room=room,
            device=device,
            metric=metric,
            time_window=window,
            default_rule=default_rule,
        )

    def _alarm_records_for_analysis(self, asset_id: str, start, end) -> list[dict[str, Any]]:
        try:
            return self.deps.monitoring.get_alarm_records(
                asset_ids={asset_id},
                access=self.deps.access,
                start=start,
                end=end,
            )
        except MockDataError:
            return []

    def _analysis_tool_calls(self, selection: PlanSelection) -> list[str]:
        mapping = {
            "metric_history": "monitoring.get_series",
            "alarm_history": "monitoring.get_alarm_records",
            "related_evidence": "topology.upstream_related",
            "peer_evidence": "monitoring.peer_evidence",
        }
        calls: list[str] = []
        for step in selection.steps:
            call = mapping[step.action]
            if call not in calls:
                calls.append(call)
        return calls

    @staticmethod
    def _device_ref(device: ResolvedDevice) -> dict[str, Any]:
        return {
            "asset_id": device.asset_id,
            "device_code": device.device.get("device_code"),
            "name": device.device.get("name"),
            "room_id": device.device.get("room_id"),
            "room_name": device.device.get("room_name"),
            "type_code": device.device.get("type_code"),
        }
