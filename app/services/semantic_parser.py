from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.adapters.fixtures import FixtureStore
from app.rag.llm_client import LLMClient, LLMError
from app.rag.prompts import build_intent_prompt
from app.schemas.intent import Intent, IntentPlan, SemanticRelation, SemanticTask
from app.services.access import AccessContext
from app.services.entity_resolution import normalize_text


class SemanticParseError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ParseOutcome:
    plan: IntentPlan
    parser: str
    warnings: list[str] = field(default_factory=list)


_TIME_PATTERN = re.compile(
    r"(最近\s*[0-9一二三四五六七八九十两半]+\s*(?:小时|分钟|天)|近\s*[0-9一二三四五六七八九十两半]+\s*(?:小时|分钟|天)|过去\s*[0-9一二三四五六七八九十两半]+\s*(?:小时|分钟|天)|今天|今日|昨天|昨日|本周|这周|现在|当前|实时)"
)
_COUNT_PATTERN = re.compile(r"(几次|多少次|次数|几条|多少个|统计)")
_CONTROL_WORDS = ("关闭", "关掉", "停机", "启动", "重启", "停止", "删除", "下发", "派单", "调节", "设定值", "拉闸", "断电")
_ANALYSIS_WORDS = ("为什么", "原因", "分析", "排查", "核查", "怎么回事", "怎么导致", "异常吗", "正常吗", "偏高", "有点高", "告警分析")
_TREND_WORDS = ("趋势", "曲线", "历史", "变化", "走势")
_KNOWLEDGE_WORDS = ("巡检", "规程", "手册", "作业指导", "注意事项", "如何", "怎么处理", "如何处理", "是什么", "有哪些", "知识库", "faq")


def _clauses(query: str) -> list[str]:
    # “如果有异常再分析”必须和条件一起进入分析子句，不能拆出一个孤立条件任务。
    normalized = query.replace("如果有异常再分析", "如果有异常分析").replace("有异常再分析", "有异常分析")
    normalized = normalized.replace("再分析", "，分析")
    parts = re.split(
        r"[，,；;。\n]+|然后|另外|顺便(?:看看|问一下|查一下)?|再看|再查|再统计",
        normalized,
    )
    return [part.strip() for part in parts if part and part.strip()]


def _extract_time(text: str) -> str | None:
    match = _TIME_PATTERN.search(text)
    return match.group(1).strip() if match else None


def _extract_room(text: str, store: FixtureStore, access: AccessContext) -> str | None:
    normalized = normalize_text(text)
    matches: list[str] = []
    for device in store.visible_devices(access):
        for alias in [device.get("room_name", ""), *device.get("room_aliases", [])]:
            if alias and normalize_text(alias) in normalized:
                matches.append(alias)
    if not matches:
        return None
    return max(matches, key=len)


def _extract_device(text: str, store: FixtureStore, access: AccessContext) -> str | None:
    normalized = normalize_text(text)
    matches: list[str] = []
    for device in store.visible_devices(access):
        for alias in [device.get("device_code", ""), device.get("name", ""), *device.get("aliases", [])]:
            if alias and len(normalize_text(alias)) >= 3 and normalize_text(alias) in normalized:
                matches.append(alias)
    if matches:
        return max(matches, key=len)
    match = re.search(r"([A-Za-z]{1,10}[-_]?\d{1,6})", text)
    return match.group(1) if match else None


def _extract_metric(text: str, metric_dictionary: dict[str, Any]) -> str | None:
    normalized = normalize_text(text)
    matches: list[tuple[int, str]] = []
    for code, spec in metric_dictionary.items():
        for alias in [code, spec.get("name", ""), *spec.get("aliases", [])]:
            if alias and normalize_text(alias) and normalize_text(alias) in normalized:
                matches.append((len(normalize_text(alias)), alias))
    if matches:
        return max(matches, key=lambda item: item[0])[1]
    if "温度" in text:
        return "温度"
    return None


def _extract_alarm_alias(text: str, alarm_dictionary: dict[str, Any]) -> str | None:
    normalized = normalize_text(text)
    matches: list[str] = []
    for alias in alarm_dictionary.get("alias_index", {}):
        if alias and normalize_text(alias) in normalized:
            matches.append(alias)
    return max(matches, key=len) if matches else None


def _detect_intent(clause: str, *, metric: str | None, alarm_alias: str | None, device: str | None) -> Intent:
    if any(word in clause for word in _CONTROL_WORDS):
        return Intent.unsupported
    if any(word in clause for word in _ANALYSIS_WORDS):
        return Intent.alarm_analysis
    if _COUNT_PATTERN.search(clause) and ("告警" in clause or "报警" in clause or alarm_alias):
        return Intent.alarm_count
    if ("告警" in clause or "报警" in clause) and not any(word in clause for word in _TREND_WORDS):
        return Intent.alarm_list
    if any(word in clause for word in _TREND_WORDS):
        return Intent.metric_trend
    if any(word in clause for word in _KNOWLEDGE_WORDS) and device is None and not alarm_alias:
        return Intent.knowledge_qa
    if metric is not None:
        if _extract_time(clause) and not any(word in clause for word in ("现在", "当前", "实时")):
            return Intent.metric_trend
        return Intent.metric_latest
    if any(word in clause for word in _KNOWLEDGE_WORDS):
        return Intent.knowledge_qa
    return Intent.unsupported


def _focus_task(context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not context:
        return None
    focus = context.get("focus") or context.get("last_focus")
    if isinstance(focus, dict):
        return focus
    tasks = context.get("previous_tasks") or []
    if tasks and isinstance(tasks[-1], dict):
        return tasks[-1]
    return None


def _build_from_focus(query: str, context: dict[str, Any] | None) -> IntentPlan | None:
    focus = _focus_task(context)
    if not focus:
        return None
    time_text = _extract_time(query)
    if not time_text:
        return None
    if not re.fullmatch(r"(那|那么)?\s*(今天|今日|昨天|昨日|本周|这周|现在|当前|最近.*|近.*|过去.*)(呢|怎么样|如何)?\s*[?？]?", query.strip()):
        return None
    try:
        intent = Intent(focus.get("intent", ""))
    except ValueError:
        return None
    if intent not in {Intent.metric_latest, Intent.metric_trend, Intent.alarm_list, Intent.alarm_count, Intent.alarm_analysis}:
        return None
    task = SemanticTask(
        semantic_id="s1",
        intent=intent,
        room=focus.get("room"),
        device=focus.get("device"),
        metric=focus.get("metric"),
        time=time_text,
        count_kind=focus.get("count_kind"),
        context_ref=None,
    )
    return IntentPlan(tasks=[task], relations=[], ask=None, action="modify")


def parse_with_rules(
    query: str,
    *,
    store: FixtureStore,
    access: AccessContext,
    metric_dictionary: dict[str, Any],
    alarm_dictionary: dict[str, Any],
    session_context: dict[str, Any] | None = None,
) -> ParseOutcome:
    query = (query or "").strip()
    if not query:
        return ParseOutcome(
            plan=IntentPlan(
                tasks=[SemanticTask(semantic_id="s1", intent=Intent.unsupported, room=None, device=None, metric=None, time=None)],
                relations=[],
                ask="请说明要查询的设备、指标或需要分析的问题。",
            ),
            parser="rules",
            warnings=["empty_query"],
        )

    modify_plan = _build_from_focus(query, session_context)
    if modify_plan is not None:
        return ParseOutcome(plan=modify_plan, parser="rules")

    raw_clauses = _clauses(query)
    tasks: list[SemanticTask] = []
    analysis_clauses: list[tuple[int, str]] = []
    previous_alarm: SemanticTask | None = None
    for clause in raw_clauses:
        room = _extract_room(clause, store, access)
        device = _extract_device(clause, store, access)
        alarm_alias = _extract_alarm_alias(clause, alarm_dictionary)
        metric = _extract_metric(clause, metric_dictionary)
        # “现在还有几条未恢复”承接上一告警子句的对象；不继承“再”等泛化上下文。
        follow_up_alarm = (
            previous_alarm is not None
            and previous_alarm.intent in {Intent.alarm_count, Intent.alarm_list, Intent.alarm_analysis}
            and any(word in clause for word in ("未恢复", "还在", "仍然", "活动", "恢复", "还有", "几条"))
        )
        if follow_up_alarm:
            room = room or previous_alarm.room
            device = device or previous_alarm.device
            alarm_alias = alarm_alias or previous_alarm.metric
            if metric is None and alarm_alias is not None:
                metric = alarm_alias
        intent = _detect_intent(clause, metric=metric, alarm_alias=alarm_alias, device=device)
        time_text = _extract_time(clause)

        if intent is Intent.knowledge_qa and device is None:
            metric = None
            alarm_alias = None
        if intent is Intent.alarm_analysis:
            analysis_clauses.append((len(tasks), clause))
        count_kind = None
        if intent is Intent.alarm_count:
            if any(word in clause for word in ("活动", "当前", "还在", "未恢复")):
                count_kind = "active"
            elif "恢复" in clause:
                count_kind = "recovered"
        if intent in {Intent.alarm_count, Intent.alarm_list, Intent.alarm_analysis}:
            metric = alarm_alias or metric
        if intent is Intent.knowledge_qa:
            metric = None

        if len(tasks) >= 4:
            return ParseOutcome(
                plan=IntentPlan(tasks=tasks, relations=[], ask="一次最多处理四个需求，请先选择要优先处理的任务。"),
                parser="rules",
                warnings=["too_many_tasks"],
            )

        semantic_id = f"s{len(tasks) + 1}"
        tasks.append(
            SemanticTask(
                semantic_id=semantic_id,
                intent=intent,
                room=room,
                device=device,
                metric=metric,
                time=time_text,
                count_kind=count_kind,
                context_ref=None,
            )
        )
        if intent in {Intent.alarm_count, Intent.alarm_list, Intent.alarm_analysis}:
            previous_alarm = tasks[-1]

    if not tasks:
        return ParseOutcome(
            plan=IntentPlan(
                tasks=[SemanticTask(semantic_id="s1", intent=Intent.unsupported, room=None, device=None, metric=None, time=None)],
                relations=[],
                ask="请说明要查询的设备、指标或需要分析的问题。",
            ),
            parser="rules",
            warnings=["no_task_detected"],
        )

    relations: list[SemanticRelation] = []
    for task_index, clause in analysis_clauses:
        target = tasks[task_index]
        previous = [index for index in range(task_index) if tasks[index].intent is not Intent.unsupported]
        if not previous:
            continue
        explicit_reference = any(word in clause for word in ("结果", "这个", "上面", "刚才", "再分析", "分析一下", "它"))
        has_own_target = target.device is not None and target.metric is not None
        if not explicit_reference and has_own_target:
            continue
        source_index = previous[-1]
        target.context_ref = "focus_result"
        condition = "if_abnormal" if ("如果" in clause and "异常" in clause) else "always"
        relations.append(
            SemanticRelation(
                type="analyze_result",
                source=tasks[source_index].semantic_id,
                target=target.semantic_id,
                condition=condition,
            )
        )

    plan = IntentPlan(tasks=tasks, relations=relations, ask=None)
    validate_semantic_structure(plan)
    if all(task.intent is Intent.unsupported for task in tasks) and not any(word in query for word in _CONTROL_WORDS):
        plan = IntentPlan(
            tasks=tasks,
            relations=relations,
            ask="请说明要查询的设备、指标、时间范围，或需要分析的具体告警。",
        )
    return ParseOutcome(plan=plan, parser="rules")


def validate_semantic_structure(plan: IntentPlan) -> None:
    ids = [task.semantic_id for task in plan.tasks]
    if len(ids) != len(set(ids)):
        raise SemanticParseError("semantic_id_duplicated", "semantic_id 必须唯一")
    by_id = {task.semantic_id: task for task in plan.tasks}
    seen_edges: set[tuple[str, str]] = set()
    for relation in plan.relations:
        if relation.source not in by_id or relation.target not in by_id:
            raise SemanticParseError("relation_endpoint_missing", "关系引用了不存在的任务")
        if relation.source == relation.target:
            raise SemanticParseError("relation_self_loop", "关系不能指向自身")
        target_task = by_id[relation.target]
        if target_task.intent is not Intent.alarm_analysis:
            raise SemanticParseError("relation_target_not_analysis", "analyze_result 的目标必须是 alarm_analysis")
        if int(relation.source[1:]) >= int(relation.target[1:]):
            raise SemanticParseError("relation_order_invalid", "关系源任务必须位于目标任务之前")
        edge = (relation.source, relation.target)
        if edge in seen_edges:
            raise SemanticParseError("relation_duplicated", "关系边重复")
        seen_edges.add(edge)


_ALLOWED_ID_PATTERN = re.compile(r"^(asset_|point_|alarm_|task_)[A-Za-z0-9_-]*$|^[0-9a-f]{8}-[0-9a-f]{4}-", re.IGNORECASE)


def _reject_generated_ids(plan: IntentPlan) -> None:
    for task in plan.tasks:
        for name in ("room", "device", "metric", "time"):
            value = getattr(task, name)
            if isinstance(value, str) and _ALLOWED_ID_PATTERN.match(value.strip()):
                raise SemanticParseError("model_generated_business_id", f"模型输出包含业务 ID 字段 {name}={value!r}")


async def parse_query(
    query: str,
    *,
    store: FixtureStore,
    access: AccessContext,
    metric_dictionary: dict[str, Any],
    alarm_dictionary: dict[str, Any],
    session_context: dict[str, Any] | None = None,
    llm: LLMClient | None = None,
) -> ParseOutcome:
    warnings: list[str] = []
    if llm is not None and llm.enabled:
        try:
            payload = await llm.chat_json(
                system="你是严格按 semantic-plan/0.5 输出 JSON 的语义解析器。",
                user=build_intent_prompt(query, session_context),
            )
            plan = IntentPlan.model_validate(payload)
            validate_semantic_structure(plan)
            _reject_generated_ids(plan)
            return ParseOutcome(plan=plan, parser="llm")
        except (LLMError, SemanticParseError, ValueError) as exc:
            warnings.append(f"llm_parse_failed:{type(exc).__name__}")
    outcome = parse_with_rules(
        query,
        store=store,
        access=access,
        metric_dictionary=metric_dictionary,
        alarm_dictionary=alarm_dictionary,
        session_context=session_context,
    )
    outcome.warnings = warnings + outcome.warnings
    return outcome
