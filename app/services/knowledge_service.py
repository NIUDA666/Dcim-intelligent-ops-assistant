"""知识问答子服务（§7.7）。

路由顺序：
1. 仅低风险、设备无关、单意图问题尝试 FAQ 快速通道；
2. FAQ 必须通过审核/发布/有效期/范围/权限与全部底层来源校验；
3. 不满足则按问题类型分组检索 SOP 或手册，引用原文回答；
4. 检索不到时明确说明资料缺口，不用旧 FAQ 冒充当前规范。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.adapters.fixtures import parse_ts
from app.schemas.execution import ExecutionTask
from app.tools.gateway import ToolGateway


@dataclass
class KnowledgeAnswer:
    answer: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    faq_used: bool = False
    route: str = "formal_rag"
    complete: bool = False
    warnings: list[str] = field(default_factory=list)


# FAQ 快速通道只面向设备无关的低风险概念/规范问题；命中这些词一律走正式流程。
_RISK_PATTERN = re.compile(
    r"(操作|步骤|怎么|如何|设置|复位|重启|更换|清洗|加注|接线|拆卸|"
    r"告警|故障|异常|分析|为什么|原因|温度|压力|电流|电压|功率|"
    r"型号|参数|限值|读数|数值|趋势|现在|当前|今天|昨天|最近|"
    r"[A-Za-z]{1,6}[-_]?\d{2,})",
    re.IGNORECASE,
)

_OPERATION_HINTS = ("巡检", "检查", "维护", "处理", "操作", "步骤", "规程", "流程")
_TECHNICAL_HINTS = ("参数", "原理", "型号", "技术", "手册", "为什么", "影响", "含义", "作用")


def _doc_index(gateway: ToolGateway) -> dict[tuple[str, str], dict[str, Any]]:
    documents = getattr(gateway.knowledge, "documents", None)
    docs = documents() if callable(documents) else gateway.store.knowledge_docs
    return {(doc["doc_id"], doc["doc_version"]): doc for doc in docs}


def _source_problem(
    source: dict[str, Any],
    *,
    docs: dict[tuple[str, str], dict[str, Any]],
    query_time: datetime,
    device_type: str | None,
    site_id: str | None,
) -> str | None:
    doc = docs.get((source.get("doc_id"), source.get("doc_version")))
    if doc is None:
        return "source_missing"
    if doc.get("review_status") != "approved" or doc.get("publication_status") != "published":
        return "source_not_published"
    if not (parse_ts(doc["valid_from"]) <= query_time <= parse_ts(doc["valid_to"])):
        return "source_expired"
    chunks = {chunk["chunk_id"] for chunk in doc.get("chunks", [])}
    if source.get("chunk_id") not in chunks:
        return "source_chunk_missing"
    allowed_types = doc.get("device_types") or []
    if device_type and allowed_types and device_type not in allowed_types:
        return "source_device_scope_mismatch"
    allowed_sites = doc.get("site_scope") or []
    if site_id and allowed_sites and site_id not in allowed_sites:
        return "source_site_scope_mismatch"
    return None


def _faq_eligible(*, task: ExecutionTask, query: str, single_task: bool) -> bool:
    if not single_task:
        return False
    bindings = task.input_bindings or {}
    if bindings.get("device") or bindings.get("room"):
        return False
    return _RISK_PATTERN.search(query) is None


def _route_doc_types(query: str) -> tuple[tuple[str, ...], str]:
    operation = any(word in query for word in _OPERATION_HINTS)
    technical = any(word in query for word in _TECHNICAL_HINTS)
    if operation and not technical:
        return ("sop", "manual"), "sop_first"
    if technical and not operation:
        return ("manual", "sop"), "manual_first"
    return ("sop", "manual"), "mixed"


def _build_answer(
    *,
    query: str,
    snippets: list[dict[str, Any]],
    device_type: str | None,
    single_task: bool,
) -> str:
    if not snippets:
        scope = "当前知识库" if not device_type else "当前知识库中与设备类型匹配的资料"
        return (
            f"{scope}没有检索到可回答“{query}”的已审核有效 SOP 或手册片段。"
            "本次不作推测性回答；请补充设备型号/站点范围，或转知识负责人核实资料。"
        )
    lines = ["根据已审核且当前有效的知识资料："]
    for index, snippet in enumerate(snippets, start=1):
        section = f" / {snippet['section']}" if snippet.get("section") else ""
        lines.append(
            f"{index}. 《{snippet['title']}》{section}：{snippet['text']}"
            f"（{snippet['doc_id']}，版本 {snippet['doc_version']}）"
        )
    lines.append(
        "以上内容仅作为检查或技术依据，不构成对具体设备当前状态的判断；"
        "涉及现场操作时，请以适用站点和型号的审核 SOP 及授权为准。"
    )
    if not single_task:
        lines.append("本次请求包含多个子任务，知识回答只对应其中的知识问答部分。")
    return "\n".join(lines)


def answer_question(
    *,
    gateway: ToolGateway,
    task: ExecutionTask,
    query: str,
    query_time: datetime,
    device_type: str | None = None,
    site_id: str | None = None,
    single_task: bool = True,
) -> KnowledgeAnswer:
    warnings: list[str] = []
    docs = _doc_index(gateway)
    eligible = _faq_eligible(task=task, query=query, single_task=single_task)
    if eligible:
        faq = gateway.faq_lookup(question=query, query_time=query_time)
        if faq is not None:
            problems = [
                problem
                for source in faq.get("source_refs", [])
                if (
                    problem := _source_problem(
                        source,
                        docs=docs,
                        query_time=query_time,
                        device_type=device_type,
                        site_id=site_id,
                    )
                )
                is not None
            ]
            if not problems and faq.get("source_refs"):
                return KnowledgeAnswer(
                    answer=(
                        f"{faq['answer']}\n"
                        "（来源：" + "；".join(
                            f"{source['doc_id']} / {source['chunk_id']} / 版本 {source['doc_version']}"
                            for source in faq["source_refs"]
                        ) + "）"
                    ),
                    sources=[
                        {
                            "doc_id": source["doc_id"],
                            "doc_version": source["doc_version"],
                            "chunk_id": source["chunk_id"],
                        }
                        for source in faq["source_refs"]
                    ],
                    faq_used=True,
                    route="faq",
                    complete=True,
                )
            warnings.append(
                "faq_candidate_rejected:" + ",".join(sorted(set(problems) or {"no_source_ref"}))
            )
    elif not single_task:
        warnings.append("faq_skipped_multi_task")
    else:
        warnings.append("faq_skipped_not_device_independent_low_risk")

    doc_types, route = _route_doc_types(query)
    snippets = gateway.knowledge_search(
        query=query,
        query_time=query_time,
        device_type=device_type,
        site_id=site_id,
        top_k=3,
        doc_types=doc_types,
    )
    answer = _build_answer(
        query=query,
        snippets=snippets,
        device_type=device_type,
        single_task=single_task,
    )
    sources = [
        {
            "doc_id": snippet["doc_id"],
            "doc_version": snippet["doc_version"],
            "doc_type": snippet["doc_type"],
            "chunk_id": snippet["chunk_id"],
            "title": snippet["title"],
            "section": snippet.get("section"),
            "score": snippet.get("score"),
        }
        for snippet in snippets
    ]
    if not snippets:
        warnings.append("no_knowledge_found")
    return KnowledgeAnswer(
        answer=answer,
        sources=sources,
        faq_used=False,
        route=route,
        complete=bool(snippets),
        warnings=warnings,
    )
