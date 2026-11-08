from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.adapters.fixtures import FixtureStore
from app.adapters.mock_knowledge import MockKnowledgeAdapter
from app.adapters.mock_monitoring import MockDataError, MockMonitoringAdapter
from app.adapters.mock_topology import MockTopologyAdapter
from app.schemas.execution import TimeWindow
from app.schemas.tool import SourceRef, ToolResult, ToolStatus
from app.scenarios.models import ExecutionLimits
from app.services.access import AccessContext
from app.services.clock import clock


class BudgetExceeded(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _point_time(point: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(point["timestamp"])


@dataclass
class ExecutionBudget:
    limits: ExecutionLimits
    started_at: float = field(default_factory=time.monotonic)
    tool_calls: int = 0
    related_assets: int = 0

    def charge_tool(self, count: int = 1) -> None:
        self.tool_calls += count
        if self.tool_calls > self.limits.max_tool_calls:
            raise BudgetExceeded("tool_call_budget_exceeded", "工具调用次数超过配置上限")

    def charge_related_assets(self, count: int) -> None:
        self.related_assets += count
        if self.related_assets > self.limits.max_related_assets:
            raise BudgetExceeded("related_asset_budget_exceeded", "关联资产数量超过配置上限")

    def check_deadline(self) -> None:
        elapsed_ms = (time.monotonic() - self.started_at) * 1000
        if elapsed_ms > self.limits.deadline_ms:
            raise BudgetExceeded("evidence_deadline_exceeded", "取证超过配置时限")


class ToolGateway:
    source_mode = "mock"

    def __init__(
        self,
        monitoring: MockMonitoringAdapter,
        topology: MockTopologyAdapter,
        knowledge: MockKnowledgeAdapter,
        store: FixtureStore,
    ):
        self.monitoring = monitoring
        self.topology = topology
        self.knowledge = knowledge
        self.store = store

    def latest(
        self,
        *,
        request_id: str,
        task_id: str,
        asset_id: str,
        metric_code: str,
        point_id: str,
        query_time: datetime,
        access: AccessContext,
        budget: ExecutionBudget | None = None,
    ) -> ToolResult:
        if budget:
            budget.check_deadline()
            budget.charge_tool()
        fetched_at = clock.now()
        try:
            reading = self.monitoring.get_latest(
                asset_id=asset_id,
                metric_code=metric_code,
                point_id=point_id,
                query_time=query_time,
                access=access,
            )
        except MockDataError as exc:
            return self._error(request_id, task_id, exc.code, str(exc), fetched_at)
        return ToolResult(
            request_id=request_id,
            task_id=task_id,
            source_mode=self.source_mode,
            status=ToolStatus.success,
            result_type="metric_latest",
            data=reading,
            unit=reading.get("unit"),
            aggregation="latest",
            complete=True,
            quality_summary={"quality": reading.get("quality")},
            source_refs=[
                SourceRef(
                    source_system=reading.get("source_system", "mock-monitoring"),
                    device_id=asset_id,
                    point_id=point_id,
                )
            ],
            fetched_at=fetched_at,
            warnings=reading.get("warnings", []),
        )

    def history(
        self,
        *,
        request_id: str,
        task_id: str,
        asset_id: str,
        metric_code: str,
        point_id: str,
        window: TimeWindow,
        access: AccessContext,
        budget: ExecutionBudget | None = None,
    ) -> ToolResult:
        if budget:
            budget.check_deadline()
            budget.charge_tool()
        fetched_at = clock.now()
        try:
            series = self.monitoring.get_series(
                asset_id=asset_id,
                metric_code=metric_code,
                point_id=point_id,
                window_start=window.start,
                window_end=window.end,
                access=access,
            )
        except MockDataError as exc:
            return self._error(request_id, task_id, exc.code, str(exc), fetched_at)
        status = ToolStatus.success if series["points"] else ToolStatus.empty
        return ToolResult(
            request_id=request_id,
            task_id=task_id,
            source_mode=self.source_mode,
            status=status,
            result_type="metric_series",
            data=series,
            unit=series.get("unit"),
            aggregation="raw",
            complete=series.get("complete", False),
            requested_window={"start": window.start.isoformat(), "end": window.end.isoformat()},
            quality_summary={
                "actual_points": series.get("actual_points"),
                "expected_points": series.get("expected_points"),
                "missing_ratio": series.get("missing_ratio"),
                "quality_counts": series.get("quality_counts"),
            },
            source_refs=[
                SourceRef(
                    source_system=series.get("source_system", "mock-monitoring"),
                    device_id=asset_id,
                    point_id=point_id,
                )
            ],
            fetched_at=fetched_at,
            warnings=series.get("warnings", []),
        )

    def alarms(
        self,
        *,
        request_id: str,
        task_id: str,
        asset_ids: set[str],
        access: AccessContext,
        window: TimeWindow | None = None,
        budget: ExecutionBudget | None = None,
    ) -> ToolResult:
        if budget:
            budget.check_deadline()
            budget.charge_tool()
        fetched_at = clock.now()
        records = self.monitoring.get_alarm_records(
            asset_ids=asset_ids,
            access=access,
            start=window.start if window else None,
            end=window.end if window else None,
        )
        status = ToolStatus.success if records else ToolStatus.empty
        return ToolResult(
            request_id=request_id,
            task_id=task_id,
            source_mode=self.source_mode,
            status=status,
            result_type="alarm_records",
            data={"records": records},
            complete=True,
            requested_window=(
                {"start": window.start.isoformat(), "end": window.end.isoformat()} if window else None
            ),
            quality_summary={"record_count": len(records)},
            source_refs=[
                SourceRef(source_system="mock-alarm", device_id=asset_id)
                for asset_id in sorted(asset_ids)
            ],
            fetched_at=fetched_at,
        )

    def related(
        self,
        *,
        request_id: str,
        task_id: str,
        asset_id: str,
        rel_type: str,
        direction: str,
        asset_types: list[str],
        access: AccessContext,
        budget: ExecutionBudget | None = None,
        limit: int = 4,
    ) -> ToolResult:
        if budget:
            budget.check_deadline()
            budget.charge_tool()
        result = self.topology.upstream_related(
            asset_id=asset_id,
            rel_type=rel_type,
            direction=direction,
            asset_types=asset_types,
            access=access,
            limit=limit,
        )
        if budget:
            budget.charge_related_assets(len(result.get("relations", [])))
        fetched_at = clock.now()
        if result.get("status") == "error":
            return self._error(request_id, task_id, result.get("error_code", "topology_failed"), "拓扑查询失败", fetched_at)
        relations = result.get("relations", [])
        status = ToolStatus.success if relations else ToolStatus.empty
        return ToolResult(
            request_id=request_id,
            task_id=task_id,
            source_mode=self.source_mode,
            status=status,
            result_type="related_assets",
            data=result,
            complete=True,
            quality_summary={
                "relation_count": len(relations),
                "unknown_relation_count": len(result.get("unknown_relations", [])),
            },
            source_refs=[
                SourceRef(
                    source_system=item.get("source_system", "mock-topology"),
                    device_id=item.get("asset_id"),
                )
                for item in relations
            ],
            fetched_at=fetched_at,
            warnings=result.get("warnings", []),
        )

    def peer_evidence(
        self,
        *,
        request_id: str,
        task_id: str,
        subject_asset_id: str,
        room_id: str,
        device_type: str,
        metric_code: str,
        window: TimeWindow,
        access: AccessContext,
        budget: ExecutionBudget | None = None,
    ) -> ToolResult:
        if budget:
            budget.check_deadline()
        peers = [
            device
            for device in self.store.devices_by_room(room_id, access)
            if device.get("type_code") == device_type and device["asset_id"] != subject_asset_id
        ]
        evidence: list[dict[str, Any]] = []
        warnings: list[str] = []
        for peer in peers:
            if budget:
                if budget.related_assets >= budget.limits.max_related_assets:
                    warnings.append("related_asset_budget_reached")
                    break
            bindings = self.store.points_for(peer["asset_id"], metric_code)
            if len(bindings) != 1:
                warnings.append(f"peer_point_unavailable:{peer['asset_id']}")
                continue
            if budget:
                budget.charge_tool()
                budget.charge_related_assets(1)
            try:
                series = self.monitoring.get_series(
                    asset_id=peer["asset_id"],
                    metric_code=metric_code,
                    point_id=bindings[0]["point_id"],
                    window_start=window.start,
                    window_end=window.end,
                    access=access,
                )
            except MockDataError:
                warnings.append(f"peer_data_unavailable:{peer['asset_id']}")
                continue
            values = [point["value"] for point in series["points"] if isinstance(point["value"], (int, float))]
            if not values:
                warnings.append(f"peer_no_numeric_data:{peer['asset_id']}")
                continue
            midpoint = window.start + (window.end - window.start) / 2
            first_values = [
                point["value"]
                for point in series["points"]
                if isinstance(point["value"], (int, float))
                and _point_time(point) < midpoint
            ]
            second_values = [
                point["value"]
                for point in series["points"]
                if isinstance(point["value"], (int, float))
                and _point_time(point) >= midpoint
            ]
            evidence.append(
                {
                    "asset_id": peer["asset_id"],
                    "device_code": peer.get("device_code"),
                    "name": peer.get("name"),
                    "room_id": peer.get("room_id"),
                    "point_id": bindings[0]["point_id"],
                    "sample_count": len(values),
                    "latest": values[-1],
                    "avg": round(sum(values) / len(values), 4),
                    "max": max(values),
                    "min": min(values),
                    "first_half_avg": (
                        round(sum(first_values) / len(first_values), 4) if first_values else None
                    ),
                    "second_half_avg": (
                        round(sum(second_values) / len(second_values), 4) if second_values else None
                    ),
                    "samples": [
                        {
                            "timestamp": point["timestamp"],
                            "value": point["value"],
                            "quality": point.get("quality", "good"),
                        }
                        for point in series["points"]
                    ],
                    "unit": series.get("unit"),
                    "complete": series.get("complete", False),
                }
            )
        status = ToolStatus.success if evidence else ToolStatus.empty
        return ToolResult(
            request_id=request_id,
            task_id=task_id,
            source_mode=self.source_mode,
            status=status,
            result_type="peer_metric_summary",
            data={"subject_asset_id": subject_asset_id, "peers": evidence},
            unit=evidence[0].get("unit") if evidence else None,
            complete=len(warnings) == 0,
            requested_window={"start": window.start.isoformat(), "end": window.end.isoformat()},
            quality_summary={"peer_count": len(evidence)},
            source_refs=[
                SourceRef(source_system="mock-monitoring", device_id=item["asset_id"], point_id=item["point_id"])
                for item in evidence
            ],
            fetched_at=clock.now(),
            warnings=warnings,
        )

    def knowledge_search(
        self,
        *,
        query: str,
        query_time: datetime,
        device_type: str | None = None,
        site_id: str | None = None,
        top_k: int = 3,
        doc_types: tuple[str, ...] = ("sop", "manual"),
    ) -> list[dict[str, Any]]:
        return self.knowledge.search(
            query=query,
            query_time=query_time,
            device_type=device_type,
            site_id=site_id,
            top_k=top_k,
            doc_types=doc_types,
        )

    def faq_lookup(self, *, question: str, query_time: datetime) -> dict[str, Any] | None:
        return self.knowledge.try_faq(question, query_time)

    @staticmethod
    def _error(request_id: str, task_id: str, code: str, message: str, fetched_at: datetime) -> ToolResult:
        return ToolResult(
            request_id=request_id,
            task_id=task_id,
            source_mode="mock",
            status=ToolStatus.error,
            result_type="error",
            complete=False,
            fetched_at=fetched_at,
            error_code=code,
            warnings=[message],
        )
