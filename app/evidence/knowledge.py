"""受限知识检索改写与 SOP/手册检索（§8.7.1）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.adapters.fixtures import parse_ts
from app.rag.llm_client import LLMClient, LLMError
from app.rag.prompts import build_knowledge_query_prompt
from app.schemas.analysis import (
    AnomalyAssertion,
    AnomalyClaim,
    EvidenceBundle,
    EvidenceFact,
    EvidenceFeature,
    EvidenceGap,
    Hypothesis,
    SopSnippet,
)
from app.tools.gateway import ToolGateway


@dataclass(frozen=True)
class KnowledgeQuery:
    purpose: str
    text: str
    basis: list[str]


_ALLOWED_PURPOSES = {"initial_checks", "technical_explanation", "evidence_requirements"}
_FORBIDDEN = re.compile(r"(asset_|point_|alarm_|tool_calls|select\s|https?://)", re.IGNORECASE)


def _visible_view(
    *,
    device_name: str,
    device_type: str,
    claim: AnomalyClaim | None,
    assertions: list[AnomalyAssertion],
    facts: list[EvidenceFact],
    features: list[EvidenceFeature],
    hypotheses: list[Hypothesis],
    gaps: list[EvidenceGap],
) -> dict[str, Any]:
    return {
        "device": {"name": device_name, "device_type": device_type},
        "claim": claim.model_dump(mode="json") if claim else None,
        "assertions": [
            {
                "id": item.id,
                "source": item.source,
                "status": item.status,
                "anomaly_kind": item.anomaly_kind,
                "evidence_ids": item.evidence_ids,
            }
            for item in assertions
        ],
        "facts": [
            {"id": item.id, "kind": item.kind, "statement": item.statement} for item in facts
        ],
        "features": [
            {
                "id": item.id,
                "name": item.name,
                "status": item.status,
                "reason": item.reason,
                "value": item.value,
                "unit": item.unit,
            }
            for item in features
        ],
        "hypotheses": [
            {"id": item.id, "label": item.label, "assessment": item.assessment}
            for item in hypotheses
        ],
        "gaps": [{"id": item.id, "type": item.type, "item": item.item} for item in gaps],
    }


def _template_queries(
    *,
    claim: AnomalyClaim | None,
    assertions: list[AnomalyAssertion],
    features: list[EvidenceFeature],
    hypotheses: list[Hypothesis],
    gaps: list[EvidenceGap],
    device_name: str,
) -> list[KnowledgeQuery]:
    queries: list[KnowledgeQuery] = []
    platform = [item for item in assertions if item.source == "platform_alarm"]
    if platform:
        queries.append(
            KnowledgeQuery(
                purpose="initial_checks",
                text="精密空调送风温度高告警的初步核查规程",
                basis=[platform[0].id, *platform[0].evidence_ids[:1]],
            )
        )
    elif claim is not None:
        queries.append(
            KnowledgeQuery(
                purpose="initial_checks",
                text="疑似精密空调送风温度偏高的核查方法",
                basis=[f"claim_01"],
            )
        )
    if gaps:
        queries.append(
            KnowledgeQuery(
                purpose="evidence_requirements",
                text="精密空调温度异常核查所需的风机状态与持续判断参数",
                basis=[gap.id for gap in gaps[:2]],
            )
        )
    common = [item for item in hypotheses if item.code == "common_upstream_cooling"]
    if common:
        queries.append(
            KnowledgeQuery(
                purpose="technical_explanation",
                text="多台精密空调同步升温时供冷侧需核对的指标与状态",
                basis=[common[0].id],
            )
        )
    if not queries:
        queries.append(
            KnowledgeQuery(
                purpose="evidence_requirements",
                text=f"{device_name}送风温度观测所需的数据与检查项",
                basis=[features[0].id] if features else [],
            )
        )
    # 最多 3 条，按 purpose 和文本去重。
    seen: set[str] = set()
    result: list[KnowledgeQuery] = []
    for query in queries:
        if query.text in seen:
            continue
        seen.add(query.text)
        result.append(query)
    return result[:3]


def _validate_llm_queries(
    payload: dict[str, Any],
    *,
    valid_ids: set[str],
) -> list[KnowledgeQuery]:
    items = payload.get("queries")
    if not isinstance(items, list) or not 1 <= len(items) <= 3:
        raise ValueError("queries_count_invalid")
    result: list[KnowledgeQuery] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("query_not_object")
        purpose = item.get("purpose")
        text = item.get("text")
        basis = item.get("basis")
        if purpose not in _ALLOWED_PURPOSES:
            raise ValueError("query_purpose_invalid")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 60:
            raise ValueError("query_text_invalid")
        if _FORBIDDEN.search(text):
            raise ValueError("query_contains_forbidden_token")
        if not isinstance(basis, list) or not basis or any(
            not isinstance(identifier, str) or identifier not in valid_ids for identifier in basis
        ):
            raise ValueError("query_basis_invalid")
        result.append(KnowledgeQuery(purpose=purpose, text=text.strip(), basis=basis))
    return result


async def build_queries(
    *,
    llm: LLMClient | None,
    device_name: str,
    device_type: str,
    claim: AnomalyClaim | None,
    assertions: list[AnomalyAssertion],
    facts: list[EvidenceFact],
    features: list[EvidenceFeature],
    hypotheses: list[Hypothesis],
    gaps: list[EvidenceGap],
) -> tuple[list[KnowledgeQuery], list[str]]:
    warnings: list[str] = []
    templates = _template_queries(
        claim=claim,
        assertions=assertions,
        features=features,
        hypotheses=hypotheses,
        gaps=gaps,
        device_name=device_name,
    )
    if llm is None or not llm.enabled:
        return templates, warnings
    valid_ids = (
        {"claim_01"} if claim is not None else set()
    ) | {item.id for item in assertions} | {item.id for item in facts} | {
        item.id for item in features
    } | {item.id for item in hypotheses} | {item.id for item in gaps}
    try:
        payload = await llm.chat_json(
            system="你是受限知识检索改写节点，只输出 semantic 检索 JSON。",
            user=build_knowledge_query_prompt(
                _visible_view(
                    device_name=device_name,
                    device_type=device_type,
                    claim=claim,
                    assertions=assertions,
                    facts=facts,
                    features=features,
                    hypotheses=hypotheses,
                    gaps=gaps,
                )
            ),
        )
        queries = _validate_llm_queries(payload, valid_ids=valid_ids)
        return queries, warnings
    except (LLMError, ValueError) as exc:
        warnings.append(f"knowledge_query_rewrite_fallback:{type(exc).__name__}")
        return templates, warnings


def retrieve(
    *,
    gateway: ToolGateway,
    queries: list[KnowledgeQuery],
    query_time: datetime,
    device_type: str | None,
    site_id: str | None,
    limit: int = 3,
) -> list[SopSnippet]:
    snippets: list[SopSnippet] = []
    seen: set[str] = set()
    for query in queries:
        chunks = gateway.knowledge_search(
            query=query.text,
            query_time=query_time,
            device_type=device_type,
            site_id=site_id,
            top_k=2,
            doc_types=("sop", "manual"),
        )
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            snippets.append(
                SopSnippet(
                    doc_id=chunk["doc_id"],
                    doc_version=chunk["doc_version"],
                    doc_type=chunk["doc_type"],
                    chunk_id=chunk_id,
                    title=chunk["title"],
                    section=chunk.get("section"),
                    text=chunk["text"],
                    device_types=list(chunk.get("device_types") or []),
                    site_scope=list(chunk.get("site_scope") or []),
                    review_status=chunk.get("review_status") or "approved",
                    publication_status=chunk.get("publication_status") or "published",
                    valid_from=parse_ts(chunk["valid_from"]) if chunk.get("valid_from") else None,
                    valid_to=parse_ts(chunk["valid_to"]) if chunk.get("valid_to") else None,
                    score=chunk.get("score"),
                    purpose=query.purpose,
                )
            )
            if len(snippets) >= limit:
                return snippets
    return snippets


def append_sop_gap(bundle: EvidenceBundle) -> None:
    if bundle.sop_snippets:
        return
    bundle.gaps.append(
        EvidenceGap(
            id=f"gap_sop_{len(bundle.gaps) + 1:02d}",
            type="no_valid_sop",
            item="本轮检索没有取得适用的有效 SOP 或手册片段",
            impact="报告只能说明缺口并建议现场按权限核查，不生成未经审核的操作步骤",
        )
    )
