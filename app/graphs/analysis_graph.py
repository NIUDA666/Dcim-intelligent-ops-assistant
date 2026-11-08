"""固定 LangGraph 取证骨架（§8.3、§8.9.5）。

所有告警分析场景共用这一套节点；场景差异只来自 ExecutionTask 中已经固定的
YAML 展开步骤、参数和 profile。节点不读取模型临时指令，也不新增取证动作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.adapters.fixtures import FixtureStore
from app.evidence.collector import CollectedEvidence, EvidenceCollector
from app.evidence.features import FeatureComputer
from app.evidence.hypotheses import evaluate
from app.evidence.knowledge import (
    KnowledgeQuery,
    build_queries,
    retrieve,
)
from app.evidence.report import build_template_report, compose
from app.rag.llm_client import LLMClient
from app.schemas.analysis import (
    AnalysisContext,
    AnomalyAssertion,
    EvidenceBundle,
    EvidenceFeature,
    EvidenceGap,
    Hypothesis,
    SopSnippet,
)
from app.schemas.execution import ExecutionTask
from app.scenarios.models import ExecutionLimits
from app.services.access import AccessContext
from app.settings import BUSINESS_TZ
from app.tools.gateway import BudgetExceeded, ExecutionBudget, ToolGateway


class AnalysisState(TypedDict, total=False):
    task: ExecutionTask
    access: AccessContext
    budget: ExecutionBudget
    collector: EvidenceCollector
    collected: CollectedEvidence
    features: list[EvidenceFeature]
    assertions: list[AnomalyAssertion]
    hypotheses: list[Hypothesis]
    gaps: list[EvidenceGap]
    queries: list[KnowledgeQuery]
    snippets: list[SopSnippet]
    bundle: EvidenceBundle
    report: str
    report_mode: str
    warnings: list[str]
    status: str
    error: str | None


@dataclass
class AnalysisOutcome:
    bundle: EvidenceBundle
    report: str
    report_mode: str = "template"
    warnings: list[str] = field(default_factory=list)


class AnalysisRunner:
    def __init__(
        self,
        *,
        gateway: ToolGateway,
        store: FixtureStore,
        metric_dictionary: dict[str, dict[str, Any]],
        llm: LLMClient | None = None,
    ):
        self.gateway = gateway
        self.store = store
        self.metric_dictionary = metric_dictionary
        self.llm = llm
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AnalysisState)
        builder.add_node("resolve_context", self._resolve_context)
        builder.add_node("match_and_pin_plan", self._match_and_pin_plan)
        builder.add_node("collect_self_evidence", self._collect_self_evidence)
        builder.add_node("collect_related_evidence", self._collect_related_evidence)
        builder.add_node("validate_evidence", self._validate_evidence)
        builder.add_node("compute_features", self._compute_features)
        builder.add_node("evaluate_hypotheses", self._evaluate_hypotheses)
        builder.add_node("build_knowledge_queries", self._build_knowledge_queries)
        builder.add_node("retrieve_sop", self._retrieve_sop)
        builder.add_node("compose_report", self._compose_report)

        builder.add_edge(START, "resolve_context")
        builder.add_conditional_edges(
            "resolve_context",
            self._route_after_context,
            {"continue": "match_and_pin_plan", "fail": END},
        )
        builder.add_conditional_edges(
            "match_and_pin_plan",
            self._route_after_context,
            {"continue": "collect_self_evidence", "fail": END},
        )
        builder.add_edge("collect_self_evidence", "collect_related_evidence")
        builder.add_edge("collect_related_evidence", "validate_evidence")
        builder.add_edge("validate_evidence", "compute_features")
        builder.add_edge("compute_features", "evaluate_hypotheses")
        builder.add_edge("evaluate_hypotheses", "build_knowledge_queries")
        builder.add_edge("build_knowledge_queries", "retrieve_sop")
        builder.add_edge("retrieve_sop", "compose_report")
        builder.add_edge("compose_report", END)
        return builder.compile()

    async def run(self, *, task: ExecutionTask, access: AccessContext) -> AnalysisOutcome:
        state: AnalysisState = {"task": task, "access": access, "warnings": []}
        try:
            final = await self.graph.ainvoke(state)
        except Exception as exc:  # 图执行异常也必须形成受控失败结果
            return self._failure_outcome(task, f"analysis_graph_failed:{type(exc).__name__}")
        if final.get("status") == "failed" or final.get("bundle") is None:
            return self._failure_outcome(task, final.get("error") or "analysis_failed")
        return AnalysisOutcome(
            bundle=final["bundle"],
            report=final.get("report") or "",
            report_mode=final.get("report_mode") or "template",
            warnings=final.get("warnings") or [],
        )

    # ---------- 节点 ----------

    def _resolve_context(self, state: AnalysisState) -> AnalysisState:
        task = state["task"]
        try:
            context = AnalysisContext.model_validate(task.analysis_context)
        except ValidationError:
            state["status"] = "failed"
            state["error"] = "analysis_context_invalid"
            return state
        if task.time_window is None:
            state["status"] = "failed"
            state["error"] = "analysis_time_window_missing"
            return state
        if self.store.device_by_asset(context.asset_id, state["access"]) is None:
            state["status"] = "failed"
            state["error"] = "analysis_asset_out_of_scope"
            return state
        state["status"] = "running"
        return state

    def _match_and_pin_plan(self, state: AnalysisState) -> AnalysisState:
        task = state["task"]
        plan = task.expanded_plan or {}
        required = ("scenario_id", "scenario_version", "steps", "feature_profile", "limits")
        if any(not plan.get(key) for key in required) or not task.plan_hash:
            state["status"] = "failed"
            state["error"] = "pinned_plan_incomplete"
            return state
        try:
            limits = ExecutionLimits.model_validate(plan["limits"])
        except ValidationError:
            state["status"] = "failed"
            state["error"] = "pinned_plan_limits_invalid"
            return state
        state["budget"] = ExecutionBudget(limits=limits)
        state["collector"] = EvidenceCollector(
            gateway=self.gateway,
            store=self.store,
            access=state["access"],
            metric_dictionary=self.metric_dictionary,
            budget=state["budget"],
        )
        return state

    def _collect_self_evidence(self, state: AnalysisState) -> AnalysisState:
        task = state["task"]
        collector = state["collector"]
        try:
            collector.collect_self(task)
        except BudgetExceeded as exc:
            collector.collected.warnings.append(exc.code)
            collector.collected.gaps.append(
                EvidenceGap(
                    id=f"gap_budget_{len(collector.collected.gaps) + 1:02d}",
                    type="budget_exceeded",
                    item="本机取证预算",
                    impact="到达计划预算后保留已取得证据，不声称取证完整",
                )
            )
        state["collected"] = collector.collected
        return state

    def _collect_related_evidence(self, state: AnalysisState) -> AnalysisState:
        task = state["task"]
        collector = state["collector"]
        try:
            collector.collect_related(task)
        except BudgetExceeded as exc:
            collector.collected.warnings.append(exc.code)
            collector.collected.gaps.append(
                EvidenceGap(
                    id=f"gap_budget_{len(collector.collected.gaps) + 1:02d}",
                    type="budget_exceeded",
                    item="关联取证预算",
                    impact="到达计划预算后保留已取得证据，不声称取证完整",
                )
            )
        state["collected"] = collector.collected
        return state

    def _validate_evidence(self, state: AnalysisState) -> AnalysisState:
        task = state["task"]
        collector = state["collector"]
        collector.validate(task)
        state["collected"] = collector.collected
        return state

    def _compute_features(self, state: AnalysisState) -> AnalysisState:
        task = state["task"]
        state["features"] = FeatureComputer(
            task=task, collected=state["collected"]
        ).compute()
        return state

    def _evaluate_hypotheses(self, state: AnalysisState) -> AnalysisState:
        task = state["task"]
        assertions, hypotheses, gaps, warnings = evaluate(
            task=task,
            collected=state["collected"],
            features=state["features"],
        )
        state["assertions"] = assertions
        state["hypotheses"] = hypotheses
        state["collected"].gaps.extend(gaps)
        state["collected"].warnings.extend(warnings)
        return state

    async def _build_knowledge_queries(self, state: AnalysisState) -> AnalysisState:
        task = state["task"]
        context = AnalysisContext.model_validate(task.analysis_context)
        queries, warnings = await build_queries(
            llm=self.llm,
            device_name=context.analysis_subject,
            device_type=context.device_type,
            claim=context.anomaly_claim,
            assertions=state.get("assertions") or [],
            facts=state["collected"].facts,
            features=state.get("features") or [],
            hypotheses=state.get("hypotheses") or [],
            gaps=state["collected"].gaps,
        )
        state["queries"] = queries
        state["warnings"] = list(state.get("warnings") or []) + warnings
        return state

    def _retrieve_sop(self, state: AnalysisState) -> AnalysisState:
        task = state["task"]
        context = AnalysisContext.model_validate(task.analysis_context)
        state["snippets"] = retrieve(
            gateway=self.gateway,
            queries=state.get("queries") or [],
            query_time=context.fixed_query_time,
            device_type=context.device_type,
            site_id=context.site_id,
            limit=3,
        )
        return state

    async def _compose_report(self, state: AnalysisState) -> AnalysisState:
        task = state["task"]
        context = AnalysisContext.model_validate(task.analysis_context)
        collected = state["collected"]
        snippets = state.get("snippets") or []
        gaps = list(collected.gaps)
        if not snippets:
            gaps.append(
                EvidenceGap(
                    id=f"gap_sop_{len(gaps) + 1:02d}",
                    type="no_valid_sop",
                    item="本轮检索没有取得适用的有效 SOP 或手册片段",
                    impact="报告只能说明缺口并建议现场按权限核查，不生成未经审核的操作步骤",
                )
            )
        if not collected.facts:
            status = "failed"
        elif gaps:
            status = "partial"
        else:
            status = "completed"
        request_id = str(task.input_bindings.get("request_id") or task.task_id)
        bundle = EvidenceBundle(
            analysis_id=f"ana_{request_id}_{task.task_id}",
            status=status,
            analysis_subject=context.analysis_subject,
            anomaly_claim=context.anomaly_claim,
            anomaly_assertions=state.get("assertions") or [],
            facts=collected.facts,
            features=state.get("features") or [],
            hypotheses=state.get("hypotheses") or [],
            gaps=gaps,
            sop_snippets=snippets,
            config_bundle_version=task.config_bundle_version,
            scenario_id=(task.expanded_plan or {}).get("scenario_id"),
            scenario_version=task.scenario_version,
            snippet_versions=dict(task.snippet_versions),
            feature_profile_version=task.feature_profile_version,
            plan_hash=task.plan_hash,
            fallback_used=task.fallback_used,
            fixed_query_time=context.fixed_query_time,
            evidence_window=context.evidence_window,
            source_mode=self.gateway.source_mode,
            warnings=list(collected.warnings) + list(state.get("warnings") or []),
            topology_is_current_snapshot=True,
        )
        text, mode, warnings = await compose(
            bundle=bundle,
            analysis_context=context.model_dump(mode="json"),
            expanded_plan=task.expanded_plan,
            llm=self.llm,
        )
        bundle.warnings.extend(warnings)
        state["bundle"] = bundle
        state["report"] = text
        state["report_mode"] = mode
        state["warnings"] = list(state.get("warnings") or []) + warnings
        return state

    # ---------- 路由与失败 ----------

    @staticmethod
    def _route_after_context(state: AnalysisState) -> str:
        return "fail" if state.get("status") == "failed" else "continue"

    def _failure_outcome(self, task: ExecutionTask, error: str) -> AnalysisOutcome:
        context: AnalysisContext | None = None
        try:
            context = AnalysisContext.model_validate(task.analysis_context)
        except (ValidationError, TypeError):
            context = None
        subject = context.analysis_subject if context else task.task_id
        request_id = str(task.input_bindings.get("request_id") or task.task_id)
        bundle = EvidenceBundle(
            analysis_id=f"ana_{request_id}_{task.task_id}",
            status="failed",
            analysis_subject=subject,
            anomaly_claim=context.anomaly_claim if context else None,
            facts=[],
            features=[],
            hypotheses=[],
            gaps=[
                EvidenceGap(
                    id="gap_failed_01",
                    type="analysis_execution_failed",
                    item="分析执行",
                    impact=f"分析任务失败：{error}；未形成可复核的异常结论",
                )
            ],
            sop_snippets=[],
            config_bundle_version=task.config_bundle_version,
            scenario_id=(task.expanded_plan or {}).get("scenario_id"),
            scenario_version=task.scenario_version,
            snippet_versions=dict(task.snippet_versions),
            feature_profile_version=task.feature_profile_version,
            plan_hash=task.plan_hash,
            fallback_used=task.fallback_used,
            fixed_query_time=(
                context.fixed_query_time if context else datetime.now(tz=BUSINESS_TZ)
            ),
            evidence_window=context.evidence_window if context else None,
            source_mode=self.gateway.source_mode,
            warnings=[error],
        )
        report = build_template_report(
            bundle,
            analysis_context=context.model_dump(mode="json") if context else {},
            expanded_plan=task.expanded_plan,
        )
        return AnalysisOutcome(bundle=bundle, report=report, report_mode="template", warnings=[error])
