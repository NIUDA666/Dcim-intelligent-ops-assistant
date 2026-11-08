"""查询编排服务（§9.2、§10.4、§12）。

约束：
- 解析与编译分开，模型只产出语义计划，后端编译为 ExecutionTask；
- 每个子任务独立执行、独立状态，部分失败不清空其他结果；
- 告警分析只走固定的 AnalysisRunner（模式 A）；
- 统计口径由 count_policy 决定，活动告警与窗口内新发生不混用。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.adapters.fixtures import FixtureStore
from app.adapters.mock_monitoring import MockMonitoringAdapter
from app.evidence.report import (
    build_chat_summary,
    build_conclusion,
    build_evidence_brief,
)
from app.graphs.analysis_graph import AnalysisRunner
from app.rag.llm_client import LLMClient
from app.schemas.api import (
    QuerySnapshot,
    TaskCard,
)
from app.schemas.analysis import EvidenceBundle
from app.schemas.execution import (
    ConfirmationCandidate,
    CountPolicy,
    ExecutionPlan,
    ExecutionTask,
    PendingConfirmation,
    SchedulingStatus,
    TaskResult,
    TaskResultStatus,
    TimeWindow,
)
from app.schemas.intent import Intent, IntentPlan, SemanticTask
from app.schemas.tool import ToolResult, ToolStatus
from app.scenarios.registry import ConfigStore
from app.services.access import AccessContext
from app.services.alarm_count import AlarmEpisode, count_active, count_occurrences, count_recovered
from app.services.clock import clock
from app.services.entity_resolution import format_device_display
from app.services.knowledge_service import answer_question
from app.services.plan_compiler import CompileDependencies, ConfirmationSelection, PlanCompiler
from app.services.semantic_parser import SemanticParseError, parse_query
from app.services.time_windows import window_dict
from app.settings import settings
from app.tools.gateway import ToolGateway


class QueryServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class QueryEvent:
    seq: int
    event: str
    data: dict[str, Any]


@dataclass
class SessionRecord:
    session_id: str
    name: str | None
    user_id: str
    revision: int = 1
    created_at: datetime = field(default_factory=lambda: clock.now())
    idempotency: dict[str, str] = field(default_factory=dict)
    focus: dict[str, Any] | None = None


@dataclass
class QueryRecord:
    request_id: str
    session_id: str
    original_query: str
    client_request_id: str | None
    fixed_query_time: datetime
    intent_plan: IntentPlan
    plan: ExecutionPlan
    parser: str = "rules"
    status: str = "accepted"
    revision: int = 1
    cards: dict[str, TaskCard] = field(default_factory=dict)
    results: dict[str, TaskResult] = field(default_factory=dict)
    bundles: dict[str, EvidenceBundle] = field(default_factory=dict)
    reports: dict[str, str] = field(default_factory=dict)
    confirmations: list[PendingConfirmation] = field(default_factory=list)
    selections: list[ConfirmationSelection] = field(default_factory=list)
    confirm_idempotency: dict[str, str] = field(default_factory=dict)
    answer: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    events: list[QueryEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_requested: bool = False
    runner_task: asyncio.Task[Any] | None = None
    created_at: datetime = field(default_factory=lambda: clock.now())
    updated_at: datetime = field(default_factory=lambda: clock.now())
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FeedbackRecord:
    feedback_id: str
    request_id: str
    task_id: str | None
    label: str
    comment: str | None
    created_at: datetime


_DONE_STATUSES = {"success", "partial", "failed", "cancelled", "waiting_user"}


class QueryService:
    def __init__(
        self,
        *,
        store: FixtureStore,
        config_store: ConfigStore,
        monitoring: MockMonitoringAdapter,
        gateway: ToolGateway,
        compiler: PlanCompiler,
        analysis_runner: AnalysisRunner,
        access: AccessContext,
        metric_dictionary: dict[str, dict[str, Any]],
        alarm_dictionary: dict[str, Any],
        llm: LLMClient | None = None,
    ):
        self.store = store
        self.config_store = config_store
        self.monitoring = monitoring
        self.gateway = gateway
        self.compiler = compiler
        self.analysis_runner = analysis_runner
        self.access = access
        self.metric_dictionary = metric_dictionary
        self.alarm_dictionary = alarm_dictionary
        self.llm = llm
        self.sessions: dict[str, SessionRecord] = {}
        self.queries: dict[str, QueryRecord] = {}
        self.confirmation_index: dict[str, tuple[str, str]] = {}
        self.feedback: list[FeedbackRecord] = []
        self._lock = asyncio.Lock()

    # ---------- 会话与查询 ----------

    async def create_session(self, name: str | None) -> SessionRecord:
        session = SessionRecord(
            session_id=f"sess_{uuid.uuid4().hex[:12]}",
            name=name,
            user_id=self.access.user_id,
        )
        self.sessions[session.session_id] = session
        return session

    def require_session(self, session_id: str) -> SessionRecord:
        session = self.sessions.get(session_id)
        if session is None:
            raise QueryServiceError("SESSION_NOT_FOUND", "会话不存在或已失效", 404)
        return session

    def require_query(self, request_id: str) -> QueryRecord:
        record = self.queries.get(request_id)
        if record is None:
            raise QueryServiceError("QUERY_NOT_FOUND", "查询不存在或无权访问", 404)
        return record

    async def submit_query(
        self,
        *,
        session_id: str,
        query: str,
        client_request_id: str | None,
    ) -> QueryRecord:
        async with self._lock:
            session = self.require_session(session_id)
            if client_request_id:
                existing_id = session.idempotency.get(client_request_id)
                if existing_id and existing_id in self.queries:
                    return self.queries[existing_id]
            request_id = f"req_{uuid.uuid4().hex[:12]}"
            fixed_query_time = clock.now()
            session.revision += 1
            intent_plan, parser, warnings = await self._parse(
                query=query, session=session
            )
            self.compiler.deps.session_revision = session.revision
            compiled = self.compiler.compile(
                intent_plan=intent_plan,
                request_id=request_id,
                session_id=session_id,
                fixed_query_time=fixed_query_time,
            )
            record = QueryRecord(
                request_id=request_id,
                session_id=session_id,
                original_query=query,
                client_request_id=client_request_id,
                fixed_query_time=fixed_query_time,
                intent_plan=intent_plan,
                plan=compiled.plan,
                parser=parser,
                revision=session.revision,
                warnings=list(warnings),
            )
            self._apply_plan_to_record(record)
            self.queries[request_id] = record
            self._index_confirmations(record)
            if client_request_id:
                session.idempotency[client_request_id] = request_id
            await self._emit(
                record,
                "accepted",
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "revision": record.revision,
                    "fixed_query_time": fixed_query_time.isoformat(),
                    "parser": parser,
                    "tasks": [card.model_dump(mode="json") for card in record.cards.values()],
                },
            )
            if record.confirmations:
                record.status = "waiting_user"
                await self._emit_confirmations(record)
            elif not record.plan.tasks:
                record.status = "waiting_user" if self._requires_user_input(record) else "failed"
                await self._finalize_without_tasks(record)
            else:
                self._start_runner(record)
            return record

    async def confirm(
        self,
        *,
        confirmation_id: str,
        selected_id: str,
        session_revision: int,
        client_request_id: str | None,
    ) -> tuple[QueryRecord, PendingConfirmation | None]:
        async with self._lock:
            entry = self.confirmation_index.get(confirmation_id)
            if entry is None:
                raise QueryServiceError("CONFIRMATION_NOT_FOUND", "确认项不存在或已失效", 404)
            request_id, _ = entry
            record = self.require_query(request_id)
            if record.status in {"cancelled", "failed", "success", "partial"}:
                raise QueryServiceError("CONFIRMATION_CLOSED", "该查询已结束，不能再确认", 409)
            if client_request_id and client_request_id in record.confirm_idempotency:
                return record, self._find_confirmation(record, confirmation_id)
            confirmation = self._find_confirmation(record, confirmation_id)
            if confirmation is None:
                raise QueryServiceError("CONFIRMATION_NOT_FOUND", "确认项已被替换", 404)
            if session_revision != record.revision or confirmation.session_revision != record.revision:
                raise QueryServiceError("STALE_REVISION", "会话版本已变化，请刷新后重试", 409)
            if clock.now() > confirmation.expires_at:
                raise QueryServiceError("CONFIRMATION_EXPIRED", "确认项已过期，请重新提交问题", 410)
            candidate = next(
                (item for item in confirmation.candidates if item.id == selected_id), None
            )
            if candidate is None:
                raise QueryServiceError("CANDIDATE_INVALID", "所选候选项不属于当前确认项", 422)
            session = self.require_session(record.session_id)
            session.revision += 1
            record.revision = session.revision
            record.selections = [
                item for item in record.selections if item.semantic_id != self._semantic_id(confirmation.task_id)
            ]
            record.selections.append(self._selection_from_candidate(confirmation, candidate))
            if client_request_id:
                record.confirm_idempotency[client_request_id] = confirmation_id
            self.compiler.deps.session_revision = session.revision
            compiled = self.compiler.compile(
                intent_plan=record.intent_plan,
                request_id=record.request_id,
                session_id=record.session_id,
                fixed_query_time=record.fixed_query_time,
                selections=record.selections,
            )
            record.plan = compiled.plan
            record.cards = {}
            record.errors = list(compiled.plan.errors)
            record.status = "accepted"
            self._apply_plan_to_record(record)
            self._reindex_confirmations(record)
            await self._emit(
                record,
                "progress",
                {
                    "stage": "confirmation_resolved",
                    "confirmation_id": confirmation_id,
                    "selected_id": selected_id,
                    "revision": record.revision,
                },
            )
            if record.confirmations:
                record.status = "waiting_user"
                await self._emit_confirmations(record)
            elif not record.plan.tasks:
                record.status = "waiting_user" if self._requires_user_input(record) else "failed"
                await self._finalize_without_tasks(record)
            else:
                self._start_runner(record)
            return record, candidate

    async def cancel(self, request_id: str, reason: str | None) -> QueryRecord:
        async with self._lock:
            record = self.require_query(request_id)
            if record.status in _DONE_STATUSES:
                return record
            record.cancel_requested = True
            if record.runner_task is not None and not record.runner_task.done():
                record.runner_task.cancel()
                await asyncio.gather(record.runner_task, return_exceptions=True)
            else:
                await self._mark_cancelled(record, reason)
                await self._finalize(record)
            return record

    def snapshot(self, record: QueryRecord) -> QuerySnapshot:
        return QuerySnapshot(
            request_id=record.request_id,
            session_id=record.session_id,
            original_query=record.original_query,
            overall_status=record.status,  # type: ignore[arg-type]
            revision=record.revision,
            fixed_query_time=record.fixed_query_time,
            tasks=list(record.cards.values()),
            pending_confirmations=list(record.confirmations),
            answer=record.answer,
            errors=list(record.errors),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def record_feedback(
        self,
        *,
        request_id: str,
        task_id: str | None,
        label: str,
        comment: str | None,
    ) -> FeedbackRecord:
        self.require_query(request_id)
        cleaned = label.strip()
        if not cleaned or len(cleaned) > 40:
            raise QueryServiceError("FEEDBACK_LABEL_INVALID", "反馈标签长度不合法", 422)
        feedback = FeedbackRecord(
            feedback_id=f"fb_{uuid.uuid4().hex[:10]}",
            request_id=request_id,
            task_id=task_id,
            label=cleaned,
            comment=comment.strip() if comment else None,
            created_at=clock.now(),
        )
        self.feedback.append(feedback)
        return feedback

    async def stream_events(self, request_id: str, last_event_id: str | None):
        record = self.require_query(request_id)
        start_seq = _parse_last_event_id(last_event_id, request_id)
        sent = 0
        while True:
            heartbeat = False
            async with record.condition:
                if len(record.events) <= sent and not record.done.is_set():
                    try:
                        await asyncio.wait_for(
                            record.condition.wait(), timeout=settings.sse_heartbeat_seconds
                        )
                    except TimeoutError:
                        heartbeat = True
                batch = record.events[sent:]
                finished = record.done.is_set()
            if heartbeat and not batch:
                yield ": keep-alive\n\n"
                continue
            for event in batch:
                sent += 1
                if event.seq <= start_seq:
                    continue
                yield _format_sse(request_id, event)
            if finished and sent >= len(record.events):
                break

    # ---------- 解析 / 编译辅助 ----------

    async def _parse(self, *, query: str, session: SessionRecord) -> tuple[IntentPlan, str, list[str]]:
        context = {"focus": session.focus} if session.focus else None
        try:
            parsed = await parse_query(
                query,
                store=self.store,
                access=self.access,
                metric_dictionary=self.metric_dictionary,
                alarm_dictionary=self.alarm_dictionary,
                session_context=context,
                llm=self.llm,
            )
            return parsed.plan, parsed.parser, parsed.warnings
        except SemanticParseError as exc:
            plan = IntentPlan(
                tasks=[
                    SemanticTask(
                        semantic_id="s1",
                        intent=Intent.unsupported,
                        room=None,
                        device=None,
                        metric=None,
                        time=None,
                    )
                ],
                relations=[],
                ask="未能理解该问题，请补充设备、指标、时间范围或要查询的内容。",
            )
            return plan, "rules", [f"semantic_parse_failed:{exc}"]

    def _apply_plan_to_record(self, record: QueryRecord) -> None:
        record.confirmations = list(record.plan.pending_confirmations)
        record.cards = {}
        for task in record.plan.tasks:
            record.cards[task.task_id] = self._initial_card(task)
        record.errors = list(record.plan.errors)
        record.updated_at = clock.now()

    def _initial_card(self, task: ExecutionTask) -> TaskCard:
        card_status = task.status
        if task.scheduling_status is SchedulingStatus.ready:
            card_status = "pending"
        return TaskCard(
            task_id=task.task_id,
            semantic_id=task.semantic_id,
            intent=task.intent.value,
            title=self._task_title(task),
            status=card_status,
            scheduling_status=task.scheduling_status.value,
            device_refs=list(task.device_refs),
            time_window=task.time_window,
            source_mode=self.gateway.source_mode,
            error_code=(task.input_bindings or {}).get("error_code"),
            clarification=(task.input_bindings or {}).get("clarification"),
        )

    def _task_title(self, task: ExecutionTask) -> str:
        device_names = [
            format_device_display(ref.get("name"), ref.get("room_name"))
            for ref in task.device_refs
            if ref.get("name")
        ]
        raw = task.input_bindings or {}
        scope = "、".join(device_names) or str(raw.get("device") or "").strip()
        binding = task.metric_binding or {}
        metric_label = (
            binding.get("metric_name")
            or binding.get("metric_code")
            or str(raw.get("metric") or "").strip()
            or "指标"
        )
        alarm = task.alarm_binding or {}
        if task.intent is Intent.metric_latest:
            title = f"{metric_label}最新值"
            return f"{scope} {title}" if scope else f"{title} · 待补充设备"
        if task.intent is Intent.metric_trend:
            title = f"{metric_label}趋势"
            return f"{scope} {title}" if scope else f"{title} · 待补充设备"
        if task.intent is Intent.alarm_list:
            suffix = f" {alarm.get('label')}" if alarm.get("label") else " 告警"
            base = f"{suffix}列表（当前活动）" if task.time_window is None else f"{suffix}记录"
            return f"{scope}{base}" if scope else base.strip()
        if task.intent is Intent.alarm_count:
            if task.count_policy is CountPolicy.active_at_query_time:
                window_label = "当前活动数量"
            elif task.count_policy is CountPolicy.recovered_in_window:
                window_label = "窗口内恢复数量"
            else:
                window_label = "窗口内新发生次数"
            base = f"{alarm.get('label') or '告警'} {window_label}"
            return f"{scope} {base}" if scope else base
        if task.intent is Intent.knowledge_qa:
            return "知识问答"
        if task.intent is Intent.alarm_analysis:
            context = task.analysis_context or {}
            subject = str(context.get("analysis_subject") or "").strip()
            if subject:
                return subject
            return f"{scope} 告警辅助分析" if scope else "告警辅助分析"
        return "未支持任务"

    # ---------- 执行 ----------

    def _start_runner(self, record: QueryRecord) -> None:
        record.status = "running"
        record.updated_at = clock.now()
        record.runner_task = asyncio.create_task(self._run(record))

    async def _run(self, record: QueryRecord) -> None:
        try:
            for task in record.plan.tasks:
                if record.cancel_requested:
                    break
                card = record.cards[task.task_id]
                if task.scheduling_status in {
                    SchedulingStatus.blocked,
                    SchedulingStatus.waiting_user,
                }:
                    continue
                skip_reason = self._dependency_skip(record, task)
                if skip_reason is not None:
                    reason, condition = skip_reason
                    result = self._build_result(
                        record,
                        task,
                        status=TaskResultStatus.skipped_condition,
                        result_type="skipped_condition",
                        data={"reason": reason, "condition": condition},
                        warnings=[reason],
                    )
                    record.results[task.task_id] = result
                    card.status = result.status.value
                    card.scheduling_status = SchedulingStatus.skipped_condition.value
                    card.result = result
                    card.complete = False
                    card.warnings = [reason]
                    card.error_code = "CONDITION_NOT_MET" if condition == "if_abnormal" else "UPSTREAM_UNAVAILABLE"
                    record.updated_at = clock.now()
                    await self._emit(record, "task_result", card.model_dump(mode="json"))
                    await self._emit(
                        record,
                        "progress",
                        {"stage": "task_skipped", "task_id": task.task_id, "reason": reason},
                    )
                    continue
                await self._execute_task(record, task, card)
                if record.cancel_requested:
                    break
        except asyncio.CancelledError:
            record.cancel_requested = True
        except Exception as exc:  # 编排异常必须形成受控失败
            record.errors.append(
                {"code": "QUERY_EXECUTION_FAILED", "message": f"查询执行失败：{type(exc).__name__}"}
            )
            if record.status not in _DONE_STATUSES:
                record.status = "failed"
        finally:
            if record.cancel_requested:
                await self._mark_cancelled(record, "user_cancelled")
            else:
                record.answer = self._compose_answer(record)
                record.status = self._overall_status(record)
                self._store_focus(record)
                if record.answer:
                    await self._emit_answer(record.answer, record)
            await self._finalize(record)

    async def _execute_task(self, record: QueryRecord, task: ExecutionTask, card: TaskCard) -> None:
        card.scheduling_status = SchedulingStatus.running.value
        card.status = "running"
        record.updated_at = clock.now()
        await self._emit(record, "progress", {"stage": "task_started", "task_id": task.task_id})
        try:
            async with asyncio.timeout(settings.task_timeout_seconds):
                result, bundle, report = await self._invoke(record, task)
        except TimeoutError:
            result = self._error_result(record, task, "TASK_TIMEOUT", "子任务执行超过时限")
            bundle, report = None, None
        except QueryServiceError as exc:
            result = self._error_result(record, task, exc.code, str(exc))
            bundle, report = None, None
        except Exception as exc:
            result = self._error_result(
                record, task, "TASK_EXECUTION_FAILED", f"子任务执行失败：{type(exc).__name__}"
            )
            bundle, report = None, None
        record.results[task.task_id] = result
        if bundle is not None:
            record.bundles[task.task_id] = bundle
            card.analysis = bundle
        if report is not None:
            record.reports[task.task_id] = report
        card.status = result.status.value
        card.scheduling_status = (
            SchedulingStatus.done.value
            if result.status is not TaskResultStatus.error
            else SchedulingStatus.blocked.value
        )
        card.complete = result.complete
        card.source_mode = result.source_mode
        card.warnings = list(result.warnings)
        card.data = result.data
        card.unit = result.unit
        card.aggregation = result.aggregation
        card.error_code = result.error_code
        card.result_type = result.result_type
        card.result = result
        record.updated_at = clock.now()
        if result.status is TaskResultStatus.error:
            record.errors.append(
                {
                    "code": result.error_code or "TASK_FAILED",
                    "task_id": task.task_id,
                    "message": "；".join(result.warnings) or "子任务执行失败",
                }
            )
        await self._emit(record, "task_result", card.model_dump(mode="json"))
        await self._emit(
            record,
            "progress",
            {"stage": "task_finished", "task_id": task.task_id, "status": result.status.value},
        )

    async def _invoke(
        self, record: QueryRecord, task: ExecutionTask
    ) -> tuple[TaskResult, EvidenceBundle | None, str | None]:
        if task.intent is Intent.metric_latest:
            return await self._invoke_latest(record, task), None, None
        if task.intent is Intent.metric_trend:
            return await self._invoke_trend(record, task), None, None
        if task.intent is Intent.alarm_list:
            return await self._invoke_alarm_list(record, task), None, None
        if task.intent is Intent.alarm_count:
            return await self._invoke_alarm_count(record, task), None, None
        if task.intent is Intent.knowledge_qa:
            return await self._invoke_knowledge(record, task), None, None
        if task.intent is Intent.alarm_analysis:
            outcome = await self.analysis_runner.run(task=task, access=self.access)
            status = (
                TaskResultStatus.success
                if outcome.bundle.status == "completed"
                else TaskResultStatus.partial
                if outcome.bundle.status == "partial"
                else TaskResultStatus.error
            )
            result = self._build_result(
                record,
                task,
                status=status,
                result_type="alarm_analysis",
                data={
                    "analysis_id": outcome.bundle.analysis_id,
                    "bundle_status": outcome.bundle.status,
                    "report_mode": outcome.report_mode,
                    "conclusion": build_conclusion(outcome.bundle),
                    "brief": build_evidence_brief(
                        outcome.bundle,
                        analysis_context=task.analysis_context or {},
                        expanded_plan=task.expanded_plan,
                    ),
                    "report": outcome.report,
                    "hypothesis_count": len(outcome.bundle.hypotheses),
                    "gap_count": len(outcome.bundle.gaps),
                },
                complete=outcome.bundle.status == "completed",
                warnings=list(outcome.bundle.warnings),
                error_code="ANALYSIS_FAILED" if outcome.bundle.status == "failed" else None,
            )
            return result, outcome.bundle, outcome.report
        raise QueryServiceError("INTENT_NOT_EXECUTABLE", "该任务类型不可执行", 422)

    async def _invoke_latest(self, record: QueryRecord, task: ExecutionTask) -> TaskResult:
        device = task.device_refs[0]
        binding = task.metric_binding or {}
        if not binding.get("point_id"):
            return self._error_result(record, task, "POINT_BINDING_MISSING", "设备缺少该指标的测点绑定")
        tool = self.gateway.latest(
            request_id=record.request_id,
            task_id=task.task_id,
            asset_id=device["asset_id"],
            metric_code=binding["metric_code"],
            point_id=binding["point_id"],
            query_time=record.fixed_query_time,
            access=self.access,
        )
        return self._from_tool_result(record, task, tool, requested_window=None)

    async def _invoke_trend(self, record: QueryRecord, task: ExecutionTask) -> TaskResult:
        device = task.device_refs[0]
        binding = task.metric_binding or {}
        if not binding.get("point_id"):
            return self._error_result(record, task, "POINT_BINDING_MISSING", "设备缺少该指标的测点绑定")
        if task.time_window is None:
            return self._error_result(record, task, "TIME_WINDOW_MISSING", "趋势查询缺少时间窗口")
        tool = self.gateway.history(
            request_id=record.request_id,
            task_id=task.task_id,
            asset_id=device["asset_id"],
            metric_code=binding["metric_code"],
            point_id=binding["point_id"],
            window=task.time_window,
            access=self.access,
        )
        return self._from_tool_result(record, task, tool, requested_window=task.time_window)

    def _query_alarm_records(
        self, record: QueryRecord, task: ExecutionTask, window: TimeWindow | None
    ) -> list[dict[str, Any]]:
        asset_ids = {ref["asset_id"] for ref in task.device_refs}
        tool = self.gateway.alarms(
            request_id=record.request_id,
            task_id=task.task_id,
            asset_ids=asset_ids,
            access=self.access,
            window=window,
        )
        if tool.status is ToolStatus.error:
            raise QueryServiceError(tool.error_code or "ALARM_QUERY_FAILED", "告警查询失败", 502)
        records = (tool.data or {}).get("records") or []
        allowed = set((task.alarm_binding or {}).get("alarm_type_ids") or [])
        if allowed:
            records = [item for item in records if item.get("alarm_type_id") in allowed]
        return records

    async def _invoke_alarm_list(self, record: QueryRecord, task: ExecutionTask) -> TaskResult:
        records = self._query_alarm_records(record, task, task.time_window)
        active_mode = task.time_window is None
        if active_mode:
            records = [
                item
                for item in records
                if _parse_dt(item["occurred_at"]) < record.fixed_query_time
                and (
                    item.get("recovered_at") is None
                    or _parse_dt(item["recovered_at"]) > record.fixed_query_time
                )
            ]
        episodes = [_episode_view(item) for item in records]
        data = {
            "episodes": episodes,
            "count": len(episodes),
            "policy": "active_at_query_time" if active_mode else "records_in_window",
            "query_time": record.fixed_query_time.isoformat(),
        }
        return self._build_result(
            record,
            task,
            status=TaskResultStatus.success if episodes else TaskResultStatus.empty,
            result_type="alarm_list",
            data=data,
            requested_window=task.time_window,
            complete=True,
            warnings=[] if episodes else ["no_alarm_records"],
        )

    async def _invoke_alarm_count(self, record: QueryRecord, task: ExecutionTask) -> TaskResult:
        policy = task.count_policy or CountPolicy.new_occurrences_in_window
        if task.time_window is None and policy is not CountPolicy.active_at_query_time:
            return self._error_result(record, task, "TIME_WINDOW_MISSING", "告警统计缺少时间窗口")
        window = task.time_window if policy is not CountPolicy.active_at_query_time else None
        records = self._query_alarm_records(record, task, window)
        episodes = [
            AlarmEpisode(
                source_system=item.get("source_system", "mock-alarm"),
                episode_id=item["episode_id"],
                device_id=item["device_id"],
                alarm_type_id=item["alarm_type_id"],
                occurred_at=_parse_dt(item["occurred_at"]),
                recovered_at=_parse_dt(item["recovered_at"]) if item.get("recovered_at") else None,
            )
            for item in records
        ]
        alarm_type_ids = list((task.alarm_binding or {}).get("alarm_type_ids") or [])
        if not alarm_type_ids:
            return self._error_result(record, task, "ALARM_TYPE_MISSING", "统计告警次数缺少告警类型")
        source_system = episodes[0].source_system if episodes else "mock-alarm"
        by_type: list[dict[str, Any]] = []
        complete = True
        observed_total = 0
        for alarm_type_id in alarm_type_ids:
            if policy is CountPolicy.active_at_query_time:
                outcome = count_active(
                    episodes,
                    source_system=source_system,
                    device_id=None,
                    alarm_type_id=alarm_type_id,
                    at=record.fixed_query_time,
                )
            elif policy is CountPolicy.recovered_in_window:
                outcome = count_recovered(
                    episodes,
                    source_system=source_system,
                    device_id=None,
                    alarm_type_id=alarm_type_id,
                    start=task.time_window.start,
                    end=task.time_window.end,
                    complete=True,
                )
            else:
                outcome = count_occurrences(
                    episodes,
                    source_system=source_system,
                    device_id=None,
                    alarm_type_id=alarm_type_id,
                    start=task.time_window.start,
                    end=task.time_window.end,
                    complete=True,
                )
            complete = complete and bool(outcome["complete"])
            observed_total += int(outcome["observed_distinct"])
            by_type.append(
                {
                    "alarm_type_id": alarm_type_id,
                    "count": outcome["count"],
                    "observed_distinct": outcome["observed_distinct"],
                }
            )
        data = {
            "count": observed_total if complete else None,
            "observed_distinct": observed_total,
            "complete": complete,
            "policy": policy.value,
            "by_alarm_type": by_type,
            "window": window_dict(task.time_window),
            "query_time": record.fixed_query_time.isoformat(),
        }
        return self._build_result(
            record,
            task,
            status=TaskResultStatus.success if complete else TaskResultStatus.partial,
            result_type="alarm_count",
            data=data,
            requested_window=task.time_window,
            complete=complete,
            warnings=[] if complete else ["count_incomplete"],
        )

    async def _invoke_knowledge(self, record: QueryRecord, task: ExecutionTask) -> TaskResult:
        answer = answer_question(
            gateway=self.gateway,
            task=task,
            query=record.original_query,
            query_time=record.fixed_query_time,
            single_task=len(record.plan.tasks) == 1,
        )
        return self._build_result(
            record,
            task,
            status=TaskResultStatus.success if answer.complete else TaskResultStatus.empty,
            result_type="knowledge_answer",
            data={
                "answer": answer.answer,
                "sources": answer.sources,
                "faq_used": answer.faq_used,
                "route": answer.route,
            },
            complete=answer.complete,
            warnings=list(answer.warnings),
        )

    # ---------- 依赖条件 ----------

    def _dependency_skip(
        self, record: QueryRecord, task: ExecutionTask
    ) -> tuple[str, str] | None:
        if not task.dependencies:
            return None
        condition = (task.run_condition or {}).get("condition", "always")
        for dependency in task.dependencies:
            source_task = next(
                (item for item in record.plan.tasks if item.task_id == dependency), None
            )
            source_result = record.results.get(dependency)
            if source_task is None or source_result is None:
                return ("上游任务没有可用结果，依赖任务未执行", "dependency")
            if source_result.status in {
                TaskResultStatus.error,
                TaskResultStatus.blocked,
                TaskResultStatus.needs_confirmation,
            }:
                return ("上游任务失败或被阻断，依赖任务未执行", "dependency")
            if condition == "if_abnormal":
                abnormal, reason = self._is_abnormal(record, source_task, source_result, task)
                if abnormal is None:
                    return (f"未判定为异常：{reason}；按条件跳过，不代表设备正常", condition)
                if not abnormal:
                    return (f"上游结果未满足异常条件：{reason}；按条件跳过，不代表设备正常", condition)
        return None

    def _is_abnormal(
        self,
        record: QueryRecord,
        source_task: ExecutionTask,
        source_result: TaskResult,
        target_task: ExecutionTask,
    ) -> tuple[bool | None, str]:
        data = source_result.data or {}
        threshold = (
            (target_task.expanded_plan or {})
            .get("parameters", {})
            .get("sustained_threshold_c")
        )
        threshold = float(threshold) if isinstance(threshold, (int, float)) else 27.5
        if source_task.intent is Intent.metric_latest:
            value = data.get("value") if isinstance(data, dict) else None
            if not isinstance(value, (int, float)):
                return None, "上游最新值没有有效数值"
            return value >= threshold, f"最新值 {_fmt(value)} 与阈值 {_fmt(threshold)} 比较"
        if source_task.intent is Intent.metric_trend:
            values = [
                point.get("value")
                for point in (data.get("points") or [])
                if isinstance(point.get("value"), (int, float))
            ]
            if not values:
                return None, "上游趋势窗口没有有效样本"
            peak = max(values)
            return peak >= threshold, f"窗口峰值 {_fmt(peak)} 与阈值 {_fmt(threshold)} 比较"
        if source_task.intent in {Intent.alarm_count, Intent.alarm_list}:
            count = data.get("count")
            if not isinstance(count, int):
                return None, "上游告警统计不完整"
            return count > 0, f"上游告警数量为 {count}"
        if source_task.intent is Intent.alarm_analysis:
            bundle = record.bundles.get(source_task.task_id)
            statuses = {item.status for item in (bundle.anomaly_assertions if bundle else [])}
            if "confirmed_abnormal" in statuses:
                return True, "上游分析结论为确认异常"
            if "unknown" in statuses or not statuses:
                return None, "上游分析结论未知"
            return False, "上游分析未确认异常"
        return None, "上游任务类型没有异常判定规则"

    # ---------- 结果与回答 ----------

    def _build_result(
        self,
        record: QueryRecord,
        task: ExecutionTask,
        *,
        status: TaskResultStatus,
        result_type: str,
        data: Any,
        requested_window: TimeWindow | None = None,
        complete: bool = False,
        warnings: list[str] | None = None,
        error_code: str | None = None,
    ) -> TaskResult:
        aggregation = None
        if task.intent is Intent.metric_latest:
            aggregation = "latest"
        elif task.intent is Intent.metric_trend:
            aggregation = "raw"
        return TaskResult(
            request_id=record.request_id,
            task_id=task.task_id,
            execution_id=f"exe_{record.request_id}_{task.task_id}",
            result_ref=f"result:{record.request_id}:{task.task_id}",
            status=status,
            result_type=result_type,
            source_mode=self.gateway.source_mode,
            warnings=list(warnings or []),
            data=data,
            unit=(task.metric_binding or {}).get("unit"),
            aggregation=aggregation,
            requested_window=requested_window,
            actual_window=requested_window,
            complete=complete,
            quality_summary=_quality_summary(result_type, data),
            fetched_at=clock.now(),
            source_refs=[],
            error_code=error_code,
        )

    def _from_tool_result(
        self,
        record: QueryRecord,
        task: ExecutionTask,
        tool: ToolResult,
        *,
        requested_window: TimeWindow | None,
    ) -> TaskResult:
        status = {
            ToolStatus.success: TaskResultStatus.success,
            ToolStatus.empty: TaskResultStatus.empty,
            ToolStatus.partial: TaskResultStatus.partial,
            ToolStatus.error: TaskResultStatus.error,
            ToolStatus.needs_confirmation: TaskResultStatus.needs_confirmation,
        }[tool.status]
        window = None
        if tool.requested_window:
            window = TimeWindow(
                start=_parse_dt(tool.requested_window["start"]),
                end=_parse_dt(tool.requested_window["end"]),
            )
        return TaskResult(
            request_id=record.request_id,
            task_id=task.task_id,
            execution_id=f"exe_{record.request_id}_{task.task_id}",
            result_ref=f"result:{record.request_id}:{task.task_id}",
            status=status,
            result_type=tool.result_type,
            source_mode=tool.source_mode,
            warnings=list(tool.warnings),
            data=tool.data,
            unit=tool.unit,
            aggregation=tool.aggregation,
            requested_window=requested_window,
            actual_window=window,
            complete=tool.complete,
            quality_summary=dict(tool.quality_summary),
            fetched_at=tool.fetched_at,
            source_refs=[item.model_dump() for item in tool.source_refs],
            error_code=tool.error_code,
        )

    def _error_result(
        self, record: QueryRecord, task: ExecutionTask, code: str, message: str
    ) -> TaskResult:
        return self._build_result(
            record,
            task,
            status=TaskResultStatus.error,
            result_type="error",
            data=None,
            complete=False,
            warnings=[message],
            error_code=code,
        )

    def _compose_answer(self, record: QueryRecord) -> str:
        lines: list[str] = []
        clarifications: list[str] = []
        successful = 0
        skipped = False
        analysis_ready = any(
            task.intent is Intent.alarm_analysis
            and record.results.get(task.task_id) is not None
            and record.results[task.task_id].status
            in {TaskResultStatus.success, TaskResultStatus.partial}
            and record.bundles.get(task.task_id) is not None
            for task in record.plan.tasks
        )
        for task in record.plan.tasks:
            card = record.cards.get(task.task_id)
            result = record.results.get(task.task_id)
            if result is None:
                if card and card.status in {"waiting_user", "needs_confirmation"}:
                    text = (card.clarification or "").strip() or "请补充查询所需的信息。"
                    if text not in clarifications:
                        clarifications.append(text)
                elif card and card.status == "blocked":
                    text = (card.clarification or "").strip()
                    title = f"【{card.title}】" if card.title else ""
                    lines.append(f"{title}未执行：{text}。" if text else f"{title}未执行。")
                continue
            if result.status is TaskResultStatus.error:
                detail = "；".join(result.warnings) or "子任务执行失败"
                lines.append(
                    f"【{card.title if card else task.task_id}】查询失败：{detail}。"
                    "该项没有可用结果，不能改写成正常、零值或无异常。"
                )
                continue
            if result.status is TaskResultStatus.skipped_condition:
                skipped = True
                reason = (result.data or {}).get("reason", "未满足执行条件")
                lines.append(f"【{card.title if card else task.task_id}】未执行分析：{reason}。")
                continue
            successful += 1
            if task.intent is Intent.metric_trend and analysis_ready:
                continue
            lines.append(self._task_answer_line(task, result, record))
        if clarifications:
            if len(clarifications) == 1:
                lines.append(clarifications[0])
            else:
                lines.append("还需要补充以下信息：")
                lines.extend(f"- {item}" for item in clarifications)
        for error in record.errors:
            if error.get("code") == "NEED_CLARIFICATION" and error.get("message"):
                lines.append(f"需要补充信息：{error['message']}")
        if not lines:
            return "没有可展示的查询结果。"
        if record.plan.tasks and successful == 0 and not clarifications and not skipped:
            lines.insert(0, "本轮没有取得可用的查询结果：")
        return "\n".join(lines)

    def _task_answer_line(
        self, task: ExecutionTask, result: TaskResult, record: QueryRecord
    ) -> str:
        data = result.data or {}
        device_names = [
            format_device_display(ref.get("name"), ref.get("room_name"))
            for ref in task.device_refs
            if ref.get("name")
        ]
        scope = "、".join(device_names) if device_names else "授权范围"
        binding = task.metric_binding or {}
        unit = result.unit or ""
        if task.intent is Intent.metric_latest:
            if result.status is TaskResultStatus.empty:
                return f"【{scope} {binding.get('metric_name')}】没有取得有效最新读数，不能据此判断正常。"
            value = data.get("value")
            return (
                f"【{scope} {binding.get('metric_name')}】最新值 {_fmt(value)} {unit}，"
                f"采集时间 {data.get('timestamp') or '未知'}（来源：{result.source_mode}）。"
            )
        if task.intent is Intent.metric_trend:
            series = data if isinstance(data, dict) else {}
            samples = [
                point
                for point in (series.get("points") or [])
                if isinstance(point.get("value"), (int, float))
            ]
            if not samples:
                return f"【{scope} {binding.get('metric_name')}】核查窗口内没有有效样本，不能据此判断正常。"
            values = [float(point["value"]) for point in samples]
            last = values[-1]
            low, high = min(values), max(values)
            mean = _mean(values)
            span = high - low
            window = result.requested_window
            if window and window.start.date() == window.end.date():
                window_text = f"{window.start:%H:%M}–{window.end:%H:%M}"
            elif window:
                window_text = f"{window.start:%m-%d %H:%M}–{window.end:%m-%d %H:%M}"
            else:
                window_text = "本次窗口"
            if span <= 0.5:
                trend = f"整体平稳（波动约 {_fmt(span)} {unit}）"
            elif last >= high - max(span * 0.02, 0.01) and last - mean >= max(span * 0.5, 1.0):
                trend = (
                    f"末段升至 {_fmt(last)} {unit}（窗口最高），"
                    f"明显高于均值 {_fmt(mean)} {unit}"
                )
            elif last <= low + max(span * 0.02, 0.01) and mean - last >= max(span * 0.5, 1.0):
                trend = (
                    f"末段降至 {_fmt(last)} {unit}（窗口最低），"
                    f"明显低于均值 {_fmt(mean)} {unit}"
                )
            else:
                trend = (
                    f"在 {_fmt(low)}–{_fmt(high)} {unit} 之间波动，"
                    f"均值 {_fmt(mean)} {unit}"
                )
            incomplete = "；数据不完整，趋势仅供参考" if not result.complete else ""
            return f"【{scope} {binding.get('metric_name')}】{window_text} {trend}{incomplete}。"
        if task.intent is Intent.alarm_list:
            count = data.get("count", 0)
            if count == 0:
                return (
                    f"【{scope}】查询成功，当前没有未恢复告警。"
                    "该结论仅表示平台记录范围内没有活动告警，不等于设备正常。"
                )
            episodes = data.get("episodes") or []
            details = "；".join(
                f"{item.get('alarm_name')}（{item.get('asset_name')}，发生于 {item.get('occurred_at')}）"
                for item in episodes[:5]
            )
            return f"【{scope}】当前有 {count} 条未恢复告警：{details}。"
        if task.intent is Intent.alarm_count:
            label = (task.alarm_binding or {}).get("label") or "告警"
            policy = data.get("policy")
            if policy == CountPolicy.active_at_query_time.value:
                title = "当前活动数量"
                counter = "条"
            elif policy == CountPolicy.recovered_in_window.value:
                title = "窗口内恢复数量"
                counter = "次"
            else:
                title = "窗口内新发生次数"
                counter = "次"
            if data.get("count") is None:
                return f"【{scope}】{label}{title}统计不完整，不能给出完整总数。"
            return (
                f"【{scope}】{label}{title}为 {data.get('count')} {counter}"
                f"（独立 episode 口径；观察到的独立事件 {data.get('observed_distinct')} 个）。"
            )
        if task.intent is Intent.knowledge_qa:
            return str(data.get("answer") or "知识库没有形成可用回答。")
        if task.intent is Intent.alarm_analysis:
            bundle = record.bundles.get(task.task_id)
            if bundle is not None:
                return build_chat_summary(
                    bundle,
                    analysis_context=task.analysis_context or {},
                    expanded_plan=task.expanded_plan,
                )
            report = record.reports.get(task.task_id)
            if report:
                return report
            return f"【{scope}】告警分析未形成报告，证据包状态：{data.get('bundle_status') or 'unknown'}。"
        return f"【{scope}】{result.result_type} 查询完成。"

    def _overall_status(self, record: QueryRecord) -> str:
        if record.cancel_requested:
            return "cancelled"
        if any(
            task.scheduling_status is SchedulingStatus.waiting_user
            for task in record.plan.tasks
        ):
            return "waiting_user"
        statuses = [result.status for result in record.results.values()]
        blocked = [
            task
            for task in record.plan.tasks
            if task.scheduling_status in {SchedulingStatus.blocked, SchedulingStatus.waiting_user}
        ]
        if not statuses and not blocked:
            return "failed"
        has_usable = any(
            status
            in {
                TaskResultStatus.success,
                TaskResultStatus.partial,
                TaskResultStatus.empty,
                TaskResultStatus.skipped_condition,
            }
            for status in statuses
        )
        has_error = any(
            status in {TaskResultStatus.error, TaskResultStatus.blocked} for status in statuses
        ) or bool(blocked)
        has_partial = any(
            status in {TaskResultStatus.partial, TaskResultStatus.empty} for status in statuses
        )
        orchestration_error = any(
            not error.get("task_id") and error.get("code") != "NEED_CLARIFICATION"
            for error in record.errors
        )
        if orchestration_error:
            return "partial" if has_usable else "failed"
        if has_error and not has_usable:
            return "failed"
        if has_error or has_partial:
            return "partial"
        return "success"

    def _store_focus(self, record: QueryRecord) -> None:
        executed = [
            task
            for task in record.plan.tasks
            if task.task_id in record.results
            and record.results[task.task_id].status
            in {TaskResultStatus.success, TaskResultStatus.partial}
        ]
        if len(executed) != 1:
            return
        task = executed[0]
        if task.intent not in {
            Intent.metric_latest,
            Intent.metric_trend,
            Intent.alarm_list,
            Intent.alarm_count,
            Intent.alarm_analysis,
        }:
            return
        broadcast = task.input_bindings or {}
        session = self.sessions.get(record.session_id)
        if session is not None:
            session.focus = {
                "intent": task.intent.value,
                "room": broadcast.get("room"),
                "device": broadcast.get("device"),
                "metric": broadcast.get("metric"),
                "count_kind": broadcast.get("count_kind"),
            }

    # ---------- 事件与收尾 ----------

    async def _emit(self, record: QueryRecord, event: str, data: dict[str, Any]) -> None:
        async with record.condition:
            record.events.append(QueryEvent(seq=len(record.events) + 1, event=event, data=data))
            record.updated_at = clock.now()
            record.condition.notify_all()

    async def _emit_confirmations(self, record: QueryRecord) -> None:
        await self._emit(
            record,
            "confirmation_required",
            {
                "request_id": record.request_id,
                "revision": record.revision,
                "confirmations": [
                    item.model_dump(mode="json") for item in record.confirmations
                ],
            },
        )

    async def _emit_answer(self, answer: str, record: QueryRecord) -> None:
        for start in range(0, len(answer), 120):
            await self._emit(record, "answer_delta", {"delta": answer[start : start + 120]})

    async def _finalize(self, record: QueryRecord) -> None:
        if record.status in {"success", "partial", "failed", "cancelled", "waiting_user"}:
            await self._emit(
                record,
                "done",
                {"overall_status": record.status, "request_id": record.request_id},
            )
        record.updated_at = clock.now()
        record.done.set()
        async with record.condition:
            record.condition.notify_all()

    async def _finalize_without_tasks(self, record: QueryRecord) -> None:
        message = "；".join(
            str(item.get("message") or "") for item in record.errors if item.get("message")
        )
        if message:
            record.answer = message
            await self._emit_answer(message, record)
        if record.status == "waiting_user":
            if record.confirmations:
                await self._emit_confirmations(record)
        else:
            await self._emit(
                record,
                "error",
                {"code": "QUERY_FAILED", "message": message or "查询无法执行"},
            )
        await self._finalize(record)

    async def _mark_cancelled(self, record: QueryRecord, reason: str | None) -> None:
        record.status = "cancelled"
        for card in record.cards.values():
            if card.status in {"pending", "running"}:
                card.status = "cancelled"
        record.answer = "查询已取消；已经完成的任务结果仍然保留。"
        record.updated_at = clock.now()
        await self._emit(
            record,
            "progress",
            {"stage": "cancelled", "reason": reason or "user_cancelled"},
        )

    def _requires_user_input(self, record: QueryRecord) -> bool:
        return any(item.get("requires_user_input") for item in record.errors)

    # ---------- 确认索引 ----------

    def _index_confirmations(self, record: QueryRecord) -> None:
        for item in record.confirmations:
            self.confirmation_index[item.confirmation_id] = (record.request_id, item.task_id)

    def _reindex_confirmations(self, record: QueryRecord) -> None:
        for key, (request_id, _) in list(self.confirmation_index.items()):
            if request_id == record.request_id:
                del self.confirmation_index[key]
        self._index_confirmations(record)

    @staticmethod
    def _find_confirmation(record: QueryRecord, confirmation_id: str) -> PendingConfirmation | None:
        return next(
            (item for item in record.confirmations if item.confirmation_id == confirmation_id),
            None,
        )

    @staticmethod
    def _semantic_id(task_id: str) -> str:
        return task_id[2:] if task_id.startswith("t_") else task_id

    def _selection_from_candidate(
        self, confirmation: PendingConfirmation, candidate: ConfirmationCandidate
    ) -> ConfirmationSelection:
        semantic_id = self._semantic_id(confirmation.task_id)
        if confirmation.field == "device":
            if not candidate.asset_id:
                raise QueryServiceError("CANDIDATE_INVALID", "设备候选缺少资产标识", 422)
            return ConfirmationSelection(
                semantic_id=semantic_id, field="device", asset_id=candidate.asset_id
            )
        if confirmation.field == "metric":
            metric_code = candidate.metadata.get("metric_code")
            if not metric_code:
                raise QueryServiceError("CANDIDATE_INVALID", "指标候选缺少指标编码", 422)
            return ConfirmationSelection(
                semantic_id=semantic_id, field="metric", metric_code=metric_code
            )
        if not candidate.alarm_type_id:
            raise QueryServiceError("CANDIDATE_INVALID", "告警候选缺少告警类型", 422)
        return ConfirmationSelection(
            semantic_id=semantic_id,
            field="alarm",
            alarm_type_ids=[candidate.alarm_type_id],
        )


def _format_sse(request_id: str, event: QueryEvent) -> str:
    import json

    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {request_id}:{event.seq}\nevent: {event.event}\ndata: {payload}\n\n"


def _parse_last_event_id(value: str | None, request_id: str) -> int:
    if not value:
        return 0
    prefix = f"{request_id}:"
    if value.startswith(prefix):
        value = value[len(prefix) :]
    try:
        return int(value)
    except ValueError:
        return 0


def _parse_dt(value: str) -> datetime:
    from app.adapters.fixtures import parse_ts

    return parse_ts(value)


def _fmt(value: Any, digits: int = 2) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _quality_summary(result_type: str, data: Any) -> dict[str, Any]:
    if result_type in {"metric_latest", "metric_series"} and isinstance(data, dict):
        keys = ("quality", "actual_points", "expected_points", "missing_ratio", "quality_counts")
        return {key: data.get(key) for key in keys if data.get(key) is not None}
    if result_type == "alarm_list" and isinstance(data, dict):
        return {"count": data.get("count"), "policy": data.get("policy")}
    if result_type == "alarm_count" and isinstance(data, dict):
        return {
            "count": data.get("count"),
            "observed_distinct": data.get("observed_distinct"),
            "complete": data.get("complete"),
        }
    return {}


def _episode_view(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "episode_id": item.get("episode_id"),
        "device_id": item.get("device_id"),
        "asset_name": item.get("asset_name"),
        "alarm_type_id": item.get("alarm_type_id"),
        "alarm_name": item.get("alarm_name"),
        "occurred_at": item.get("occurred_at"),
        "recovered_at": item.get("recovered_at"),
        "status": "active" if item.get("recovered_at") is None else "recovered",
        "notification_count": item.get("notification_count", 1),
    }
