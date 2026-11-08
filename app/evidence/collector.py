"""按已固定计划采集证据（§8.3 通用节点、§8.9.5 预算）。

采集器只执行 ExecutionTask.expanded_plan 中已展开的步骤，不接受模型传入的
新工具、新关系或新指标。任何缺失都形成缺口，不用 0、正常或推测补齐。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.adapters.fixtures import FixtureStore
from app.schemas.analysis import EvidenceFact, EvidenceGap, EvidenceSample
from app.schemas.execution import ExecutionTask, TimeWindow
from app.schemas.tool import ToolResult, ToolStatus
from app.services.access import AccessContext
from app.services.entity_resolution import format_device_display
from app.tools.gateway import ExecutionBudget, ToolGateway


@dataclass
class SeriesEvidence:
    fact_id: str
    asset_id: str
    asset_name: str
    device_code: str | None
    metric_code: str
    metric_name: str
    point_id: str
    unit: str | None
    window: TimeWindow
    samples: list[EvidenceSample]
    complete: bool
    missing_ratio: float
    quality: str
    source_system: str


@dataclass
class PeerEvidence:
    fact_id: str
    asset_id: str
    device_code: str | None
    name: str | None
    metric_code: str
    metric_name: str
    point_id: str
    unit: str | None
    window: TimeWindow
    samples: list[EvidenceSample]
    complete: bool


@dataclass
class CollectedEvidence:
    facts: list[EvidenceFact] = field(default_factory=list)
    gaps: list[EvidenceGap] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    self_series: dict[str, list[SeriesEvidence]] = field(default_factory=dict)
    related_series: list[SeriesEvidence] = field(default_factory=list)
    peers: list[PeerEvidence] = field(default_factory=list)
    alarm_records: list[dict[str, Any]] = field(default_factory=list)
    alarm_fact_ids: dict[str, str] = field(default_factory=dict)
    topology_fact_ids: list[str] = field(default_factory=list)
    unknown_topology: list[dict[str, Any]] = field(default_factory=list)
    executed_steps: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    core_available: bool = False

    def facts_by_id(self) -> dict[str, EvidenceFact]:
        return {fact.id: fact for fact in self.facts}


class EvidenceCollector:
    def __init__(
        self,
        *,
        gateway: ToolGateway,
        store: FixtureStore,
        access: AccessContext,
        metric_dictionary: dict[str, dict[str, Any]],
        budget: ExecutionBudget,
    ):
        self.gateway = gateway
        self.store = store
        self.access = access
        self.metric_dictionary = metric_dictionary
        self.budget = budget
        self.collected = CollectedEvidence()
        self._fact_seq = 0
        self._gap_seq = 0
        self._alarm_query_cache: set[tuple[str, str | None, str | None]] = set()

    # ---------- 公共入口 ----------

    def collect_self(self, task: ExecutionTask) -> CollectedEvidence:
        asset = task.device_refs[0]
        window = task.time_window
        if window is None:
            self._gap(
                "missing_time_window",
                "分析时间窗口",
                "没有明确核查窗口，无法读取时序证据",
            )
            return self.collected
        for step in self._steps(task):
            action = step.get("action")
            if action == "metric_history":
                self._collect_self_metrics(task, asset["asset_id"], window, step)
            elif action == "alarm_history":
                self._collect_alarms(
                    asset_id=asset["asset_id"],
                    asset_name=self._asset_name(asset),
                    window=window,
                    plan_step_id=step.get("id"),
                )
        return self.collected

    def collect_related(self, task: ExecutionTask) -> CollectedEvidence:
        asset = task.device_refs[0]
        window = task.time_window
        if window is None:
            return self.collected
        for step in self._steps(task):
            action = step.get("action")
            if action == "related_evidence":
                self._collect_related_step(task, asset, window, step)
            elif action == "peer_evidence":
                self._collect_peer_step(task, asset, window, step)
        return self.collected

    def validate(self, task: ExecutionTask) -> CollectedEvidence:
        trigger_metric = (task.metric_binding or {}).get("metric_code")
        trigger_series = self.collected.self_series.get(trigger_metric or "", [])
        if not trigger_series:
            self._gap(
                "core_evidence_unavailable",
                f"触发指标 {trigger_metric or '未知'} 的时序证据",
                "核心趋势缺失，不能计算持续超限或本机变化特征",
                related_step_id=None,
            )
        else:
            max_missing = float(
                (task.expanded_plan or {}).get("parameters", {}).get("max_missing_ratio", 0.2)
            )
            for series in trigger_series:
                if series.missing_ratio > max_missing:
                    self._gap(
                        "data_quality_insufficient",
                        f"{series.metric_name}时序完整性",
                        f"缺失比例 {series.missing_ratio:.0%} 超过参数上限 {max_missing:.0%}，相关特征只能标记未知",
                        related_step_id=None,
                    )
        if self.collected.unknown_topology:
            self._gap(
                "unknown_relation",
                "未识别的拓扑关系",
                "存在未知关系，不能解释为“没有上游”或“上游正常”",
            )
        if self.collected.topology_fact_ids:
            self._gap(
                "topology_current_snapshot_only",
                "拓扑时点",
                "关系数据来自当前登记快照，不能证明异常时段的历史连接状态",
            )
        if not self.collected.alarm_records:
            self.collected.warnings.append("no_platform_alarm_in_analysis_window")
        plan = task.expanded_plan or {}
        if plan.get("fallback_used"):
            self._gap(
                "fallback_scope",
                "专项场景覆盖范围",
                "未匹配专项分析场景，仅执行本机兜底；未检查上下游，不能据此认定异常只在本机",
            )
        if plan.get("feature_profile") == "temperature_demo_v1":
            self._gap(
                "work_order_not_in_plan",
                "工单与变更记录",
                "本次已发布计划未包含工单/变更取证，无法判断异常前后的现场操作时间线",
                severity="optional",
            )
        self.collected.core_available = bool(trigger_series)
        return self.collected

    # ---------- 步骤：本机 ----------

    def _collect_self_metrics(
        self,
        task: ExecutionTask,
        asset_id: str,
        window: TimeWindow,
        step: dict[str, Any],
    ) -> None:
        metrics = step.get("metrics") or {}
        required = list(metrics.get("required") or [])
        optional = list(metrics.get("optional") or [])
        binding = task.metric_binding or {}
        required = [
            binding.get("metric_code") if code == "$trigger_metric" else code for code in required
        ]
        optional = [
            binding.get("metric_code") if code == "$trigger_metric" else code for code in optional
        ]
        for metric_code in required:
            if not metric_code:
                continue
            self._collect_self_metric(
                asset_id, metric_code, window, step.get("id"), required=True, task_binding=binding
            )
        for metric_code in optional:
            if not metric_code or metric_code in required:
                continue
            self._collect_self_metric(
                asset_id, metric_code, window, step.get("id"), required=False, task_binding=binding
            )

    def _collect_self_metric(
        self,
        asset_id: str,
        metric_code: str,
        window: TimeWindow,
        plan_step_id: str | None,
        *,
        required: bool,
        task_binding: dict[str, Any],
    ) -> None:
        device = self.store.device_by_asset(asset_id, self.access)
        if device is None:
            self._gap("asset_out_of_scope", metric_code, "设备不在授权范围内", plan_step_id)
            return
        metric_name = self._metric_name(metric_code)
        binding = self._binding(asset_id, metric_code)
        if isinstance(binding, str):
            self._gap(
                "point_binding_missing" if binding == "missing" else "binding_ambiguous",
                metric_name,
                (
                    "设备没有该指标的测点绑定，无法读取数据"
                    if binding == "missing"
                    else "该指标存在多个测点通道，计划未明确选择，不能任选一个"
                ),
                plan_step_id,
            )
            return
        if metric_code == (task_binding or {}).get("metric_code") and task_binding.get("point_id"):
            binding = {"point_id": task_binding["point_id"], "unit": task_binding.get("unit"), "channel": task_binding.get("channel")}
        result = self._history(asset_id, metric_code, binding["point_id"], window)
        series = self._series_from_result(
            result=result,
            asset=device,
            metric_code=metric_code,
            metric_name=metric_name,
            point_id=binding["point_id"],
            unit=binding.get("unit") or result.unit,
            window=window,
            plan_step_id=plan_step_id,
            kind="timeseries" if required else "optional_timeseries",
            required=required,
        )
        if series is not None:
            self.collected.self_series.setdefault(metric_code, []).append(series)

    # ---------- 步骤：关联与对照 ----------

    def _collect_related_step(
        self,
        task: ExecutionTask,
        asset: dict[str, Any],
        window: TimeWindow,
        step: dict[str, Any],
    ) -> None:
        rel_type = step.get("rel_type") or ""
        direction = step.get("direction") or "upstream"
        asset_types = list(step.get("asset_types") or [])
        result = self.gateway.related(
            request_id=task.input_bindings.get("request_id", "analysis"),
            task_id=task.task_id,
            asset_id=asset["asset_id"],
            rel_type=rel_type,
            direction=direction,
            asset_types=asset_types,
            access=self.access,
            budget=self.budget,
            limit=self.budget.limits.max_related_assets,
        )
        self.collected.tool_calls.append("topology.upstream_related")
        if result.status is ToolStatus.error:
            self._gap(
                "related_evidence_failed",
                f"{rel_type} 上游证据",
                "拓扑查询失败，无法检查该方向",
                step.get("id"),
            )
            return
        data = result.data or {}
        relations = data.get("relations") or []
        unknown = data.get("unknown_relations") or []
        self.collected.unknown_topology.extend(unknown)
        if not relations:
            self._gap(
                "no_related_asset",
                f"{rel_type} 已确认对象",
                "未取得已确认的上游对象，不能据此认定没有上游或上游正常",
                step.get("id"),
            )
        metric_names = (step.get("metrics") or {}).get("required") or []
        for relation in relations:
            related_id = relation["asset_id"]
            fact_id = self._fact(
                kind="topology_relation",
                source_system=relation.get("source_system", "mock-topology"),
                source_record_id=relation.get("relation_id", related_id),
                observed_at=None,
                statement=(
                    f"当前登记拓扑快照显示 {relation.get('name')}（{related_id}）"
                    f"通过 {rel_type} 关系与 {asset.get('name')} 关联；"
                    "该快照不代表历史时点的连接状态。"
                ),
                asset_id=related_id,
                plan_step_id=step.get("id"),
                quality="good",
                coverage="current_snapshot",
            )
            self.collected.topology_fact_ids.append(fact_id)
            for metric_code in metric_names:
                self._collect_related_metric(
                    related_id, metric_code, window, step.get("id"), fact_id
                )
            if step.get("include_alarms"):
                self._collect_alarms(
                    asset_id=related_id,
                    asset_name=self.store.device_name(related_id),
                    window=window,
                    plan_step_id=step.get("id"),
                )
        warnings = result.warnings or []
        self.collected.warnings.extend(warnings)
        if data.get("snapshot"):
            self.collected.warnings.append("topology_snapshot_is_current_only")

    def _collect_related_metric(
        self,
        asset_id: str,
        metric_code: str,
        window: TimeWindow,
        plan_step_id: str | None,
        topology_fact_id: str,
    ) -> None:
        device = self.store.device_by_asset(asset_id, self.access)
        if device is None:
            self._gap("related_asset_out_of_scope", metric_code, "关联设备不在授权范围内", plan_step_id)
            return
        binding = self._binding(asset_id, metric_code)
        if isinstance(binding, str):
            self._gap(
                "related_point_missing",
                f"{device.get('name')} {self._metric_name(metric_code)}",
                "关联设备缺少该指标测点，无法检查上游方向",
                plan_step_id,
            )
            return
        result = self._history(asset_id, metric_code, binding["point_id"], window)
        series = self._series_from_result(
            result=result,
            asset=device,
            metric_code=metric_code,
            metric_name=self._metric_name(metric_code),
            point_id=binding["point_id"],
            unit=binding.get("unit") or result.unit,
            window=window,
            plan_step_id=plan_step_id,
            kind="related_timeseries",
            required=True,
            extra_statement=f"关联依据 {topology_fact_id}。",
        )
        if series is not None:
            self.collected.related_series.append(series)

    def _collect_peer_step(
        self,
        task: ExecutionTask,
        asset: dict[str, Any],
        window: TimeWindow,
        step: dict[str, Any],
    ) -> None:
        metric_codes = (step.get("metrics") or {}).get("required") or []
        for metric_code in metric_codes:
            result = self.gateway.peer_evidence(
                request_id=task.input_bindings.get("request_id", "analysis"),
                task_id=task.task_id,
                subject_asset_id=asset["asset_id"],
                room_id=asset.get("room_id") or "",
                device_type=asset.get("type_code") or "",
                metric_code=metric_code,
                window=window,
                access=self.access,
                budget=self.budget,
            )
            self.collected.tool_calls.append("monitoring.peer_evidence")
            data = result.data or {}
            peers = data.get("peers") or []
            if not peers:
                self._gap(
                    "peer_evidence_unavailable",
                    "同机房同类型对照设备",
                    "没有形成可比较的对照证据，无法判断是否为局部变化",
                    step.get("id"),
                )
            for warning in result.warnings or []:
                self.collected.warnings.append(warning)
                if warning.startswith("peer_"):
                    self._gap(
                        "peer_evidence_incomplete",
                        warning.split(":", 1)[-1],
                        "部分对照设备数据不可用，比较对象数量可能不足",
                        step.get("id"),
                    )
            for peer in peers:
                samples = [
                    EvidenceSample(
                        timestamp=datetime.fromisoformat(point["timestamp"]),
                        value=point["value"] if isinstance(point["value"], (int, float)) else None,
                        quality=point.get("quality", "good"),
                    )
                    for point in peer.get("samples", [])
                ]
                fact_id = self._fact(
                    kind="comparison_timeseries",
                    source_system="mock-monitoring",
                    source_record_id=peer.get("point_id") or peer["asset_id"],
                    observed_at=None,
                    statement=(
                        f"同机房同类型设备 {peer.get('name')}（{peer.get('asset_id')}）的"
                        f"{self._metric_name(metric_code)}在核查窗口内均值 "
                        f"{self._format(peer.get('avg'))}{peer.get('unit') or ''}，"
                        f"最大 {self._format(peer.get('max'))}{peer.get('unit') or ''}，"
                        f"样本 {peer.get('sample_count')} 个。"
                    ),
                    asset_id=peer["asset_id"],
                    point_id=peer.get("point_id"),
                    metric_code=metric_code,
                    window=window,
                    unit=peer.get("unit"),
                    aggregation="raw",
                    quality="good" if peer.get("complete") else "partial",
                    coverage="complete" if peer.get("complete") else "partial",
                    plan_step_id=step.get("id"),
                    data=samples,
                )
                self.collected.peers.append(
                    PeerEvidence(
                        fact_id=fact_id,
                        asset_id=peer["asset_id"],
                        device_code=peer.get("device_code"),
                        name=peer.get("name"),
                        metric_code=metric_code,
                        metric_name=self._metric_name(metric_code),
                        point_id=peer.get("point_id") or "",
                        unit=peer.get("unit"),
                        window=window,
                        samples=samples,
                        complete=bool(peer.get("complete")),
                    )
                )

    # ---------- 步骤：告警 ----------

    def _collect_alarms(
        self,
        *,
        asset_id: str,
        asset_name: str,
        window: TimeWindow,
        plan_step_id: str | None,
    ) -> None:
        cache_key = (asset_id, window.start.isoformat(), window.end.isoformat())
        if cache_key in self._alarm_query_cache:
            return
        self._alarm_query_cache.add(cache_key)
        result = self.gateway.alarms(
            request_id="analysis",
            task_id=plan_step_id or "alarm_history",
            asset_ids={asset_id},
            access=self.access,
            window=window,
            budget=self.budget,
        )
        self.collected.tool_calls.append("monitoring.get_alarm_records")
        records = (result.data or {}).get("records") or []
        if not records:
            self._fact(
                kind="alarm_query",
                source_system="mock-alarm",
                source_record_id=f"alarm_query:{asset_id}:{window.start.isoformat()}",
                observed_at=None,
                statement=(
                    f"平台告警记录查询显示 {asset_name}（{asset_id}）在核查窗口内"
                    "返回 0 条独立告警记录；无告警不等于设备正常。"
                ),
                asset_id=asset_id,
                window=window,
                plan_step_id=plan_step_id,
                quality="good",
                coverage="complete",
            )
            return
        self._fact(
            kind="alarm_query",
            source_system="mock-alarm",
            source_record_id=f"alarm_query:{asset_id}:{window.start.isoformat()}",
            observed_at=None,
            statement=(
                f"平台告警记录查询显示 {asset_name}（{asset_id}）在核查窗口内"
                f"返回 {len(records)} 条独立告警记录。"
            ),
            asset_id=asset_id,
            window=window,
            plan_step_id=plan_step_id,
            quality="good",
            coverage="complete",
        )
        for record in records:
            self.collected.alarm_records.append(record)
            definition = self.store.alarm_definition(record["alarm_type_id"]) or {}
            alarm_name = definition.get("name") or record.get("alarm_name") or record["alarm_type_id"]
            occurred_at = datetime.fromisoformat(record["occurred_at"])
            recovered_at = (
                datetime.fromisoformat(record["recovered_at"]) if record.get("recovered_at") else None
            )
            if recovered_at is None:
                lifecycle = "截至查询时点未恢复"
            else:
                lifecycle = f"已于 {record['recovered_at']} 恢复"
            fact_id = self._fact(
                kind="alarm_event",
                source_system=record.get("source_system", "mock-alarm"),
                source_record_id=record.get("episode_id", record["alarm_type_id"]),
                observed_at=occurred_at,
                statement=(
                    f"平台告警记录显示 {asset_name}（{asset_id}）的“{alarm_name}”告警"
                    f"于 {record['occurred_at']} 发生，{lifecycle}。"
                ),
                asset_id=asset_id,
                metric_code=definition.get("metric_code"),
                window=window,
                plan_step_id=plan_step_id,
                quality="good",
                coverage="complete",
            )
            self.collected.alarm_fact_ids[record.get("episode_id", fact_id)] = fact_id

    # ---------- 公共工具 ----------

    def _history(
        self,
        asset_id: str,
        metric_code: str,
        point_id: str,
        window: TimeWindow,
    ) -> ToolResult:
        result = self.gateway.history(
            request_id="analysis",
            task_id="metric_history",
            asset_id=asset_id,
            metric_code=metric_code,
            point_id=point_id,
            window=window,
            access=self.access,
            budget=self.budget,
        )
        self.collected.tool_calls.append("monitoring.get_series")
        return result

    def _series_from_result(
        self,
        *,
        result: ToolResult,
        asset: dict[str, Any],
        metric_code: str,
        metric_name: str,
        point_id: str,
        unit: str | None,
        window: TimeWindow,
        plan_step_id: str | None,
        kind: str,
        required: bool,
        extra_statement: str = "",
    ) -> SeriesEvidence | None:
        if result.status is ToolStatus.error:
            self._gap(
                "data_request_failed",
                f"{asset.get('name')} {metric_name}",
                f"读取失败：{result.error_code or '上游错误'}；不能按 0 或正常处理",
                plan_step_id,
            )
            return None
        data = result.data or {}
        points = data.get("points") or []
        if not points:
            self._gap(
                "no_series_data",
                f"{asset.get('name')} {metric_name}",
                "核查窗口内没有有效样本，相关特征只能标记未知",
                plan_step_id,
                severity="required" if required else "optional",
            )
            return None
        samples = [
            EvidenceSample(
                timestamp=datetime.fromisoformat(point["timestamp"]),
                value=point["value"] if isinstance(point["value"], (int, float)) else None,
                quality=point.get("quality", "good"),
            )
            for point in points
        ]
        numeric = [sample.value for sample in samples if isinstance(sample.value, (int, float))]
        complete = bool(result.complete)
        missing_ratio = float((result.quality_summary or {}).get("missing_ratio") or 0.0)
        quality = "good" if complete and missing_ratio <= 0.05 else "partial"
        if numeric:
            summary = (
                f"样本 {len(samples)} 个，均值 {self._format(sum(numeric) / len(numeric))}{unit or ''}，"
                f"最大 {self._format(max(numeric))}{unit or ''}，"
                f"末值 {self._format(numeric[-1])}{unit or ''}，"
                f"缺失比例 {missing_ratio:.0%}"
            )
        else:
            summary = f"样本 {len(samples)} 个，末值 {samples[-1].value}，缺失比例 {missing_ratio:.0%}"
        statement = (
            f"平台监控记录显示 {format_device_display(asset.get('name'), asset.get('room_name'))}{metric_name}"
            f"在 {window.start.isoformat()} 至 {window.end.isoformat()} 的核查窗口内{summary}。"
        )
        if extra_statement:
            statement += extra_statement
        fact_id = self._fact(
            kind=kind,
            source_system=data.get("source_system", "mock-monitoring"),
            source_record_id=point_id,
            observed_at=None,
            statement=statement,
            asset_id=asset["asset_id"],
            point_id=point_id,
            metric_code=metric_code,
            window=window,
            unit=unit,
            aggregation="raw",
            quality=quality,
            coverage="complete" if complete else "partial",
            plan_step_id=plan_step_id,
            data=samples,
        )
        return SeriesEvidence(
            fact_id=fact_id,
            asset_id=asset["asset_id"],
            asset_name=asset.get("name") or asset["asset_id"],
            device_code=asset.get("device_code"),
            metric_code=metric_code,
            metric_name=metric_name,
            point_id=point_id,
            unit=unit,
            window=window,
            samples=samples,
            complete=complete,
            missing_ratio=missing_ratio,
            quality=quality,
            source_system=data.get("source_system", "mock-monitoring"),
        )

    def _binding(self, asset_id: str, metric_code: str) -> dict[str, Any] | str:
        bindings = self.store.points_for(asset_id, metric_code)
        if not bindings:
            return "missing"
        if len(bindings) > 1:
            return "ambiguous"
        return bindings[0]

    def _metric_name(self, metric_code: str) -> str:
        spec = self.metric_dictionary.get(metric_code) or {}
        return spec.get("name") or metric_code

    def _asset_name(self, asset: dict[str, Any]) -> str:
        return format_device_display(asset.get("name"), asset.get("room_name"))

    def _steps(self, task: ExecutionTask) -> list[dict[str, Any]]:
        return list((task.expanded_plan or {}).get("steps") or [])

    def _fact(
        self,
        *,
        kind: str,
        source_system: str,
        source_record_id: str,
        observed_at: datetime | None,
        statement: str,
        asset_id: str | None = None,
        point_id: str | None = None,
        metric_code: str | None = None,
        window: TimeWindow | None = None,
        unit: str | None = None,
        aggregation: str | None = None,
        quality: str | None = None,
        coverage: str | None = None,
        plan_step_id: str | None = None,
        data: list[EvidenceSample] | None = None,
    ) -> str:
        self._fact_seq += 1
        fact_id = f"ev_{self._fact_seq:02d}"
        self.collected.facts.append(
            EvidenceFact(
                id=fact_id,
                kind=kind,
                source_system=source_system,
                source_record_id=source_record_id,
                observed_at=observed_at,
                statement=statement,
                quality=quality,
                coverage=coverage,
                asset_id=asset_id,
                point_id=point_id,
                metric_code=metric_code,
                window_start=window.start if window else None,
                window_end=window.end if window else None,
                unit=unit,
                aggregation=aggregation,
                data=data or [],
                plan_step_id=plan_step_id,
            )
        )
        return fact_id

    def _gap(
        self,
        gap_type: str,
        item: str,
        impact: str,
        related_step_id: str | None = None,
        *,
        severity: str = "required",
    ) -> str:
        self._gap_seq += 1
        gap_id = f"gap_{self._gap_seq:02d}"
        if severity == "optional":
            item = f"{item}（可选证据）"
        self.collected.gaps.append(
            EvidenceGap(
                id=gap_id,
                type=gap_type,
                item=item,
                impact=impact,
                related_step_id=related_step_id,
            )
        )
        return gap_id

    @staticmethod
    def _format(value: Any) -> str:
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, (int, float)):
            return f"{value:.2f}".rstrip("0").rstrip(".")
        return str(value)
