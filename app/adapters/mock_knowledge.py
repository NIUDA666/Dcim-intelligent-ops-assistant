from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

from app.adapters.fixtures import FixtureStore, load_fixtures, parse_ts


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?：:；;\"'“”‘’（）()]+", "", text).lower()


def _bigrams(text: str) -> set[str]:
    normalized = _normalize(text)
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


class MockKnowledgeAdapter:
    source_mode = "mock"
    source_system = "mock-knowledge"

    def __init__(self, store: FixtureStore | None = None):
        self.store = store or load_fixtures()
        # 导入的资料先进入待审核区；审核发布前不会参与 FAQ 或正式检索。
        self.runtime_docs: list[dict[str, Any]] = []

    def register_document(self, document: dict[str, Any]) -> None:
        self.runtime_docs.append(document)

    def documents(self) -> list[dict[str, Any]]:
        return [*self.store.knowledge_docs, *self.runtime_docs]

    def try_faq(self, question: str, query_time: datetime) -> dict[str, Any] | None:
        target = _normalize(question)
        for entry in self.store.faq_entries:
            if entry.get("review_status") != "approved" or entry.get("publication_status") != "published":
                continue
            if entry.get("risk_level") != "low":
                continue
            if not self._valid_at(entry, query_time):
                continue
            candidates = [entry["canonical_question"], *entry.get("question_variants", [])]
            if any(_normalize(candidate) == target for candidate in candidates):
                return entry
        return None

    def search(
        self,
        *,
        query: str,
        query_time: datetime,
        device_type: str | None = None,
        site_id: str | None = None,
        top_k: int = 3,
        doc_types: Iterable[str] = ("sop", "manual"),
    ) -> list[dict[str, Any]]:
        query_terms = _bigrams(query)
        allowed_types = set(doc_types)
        scored: list[tuple[float, dict[str, Any]]] = []
        for doc in self.documents():
            if doc.get("doc_type") not in allowed_types:
                continue
            if doc.get("review_status") != "approved" or doc.get("publication_status") != "published":
                continue
            if not self._valid_at(doc, query_time):
                continue
            if device_type and doc.get("device_types") and device_type not in doc["device_types"]:
                continue
            if site_id and doc.get("site_scope") and site_id not in doc["site_scope"]:
                continue
            for chunk in doc.get("chunks", []):
                chunk_terms = _bigrams(chunk["text"] + chunk.get("section", ""))
                overlap = query_terms & chunk_terms
                if not overlap:
                    continue
                score = len(overlap) / max(1, len(query_terms))
                scored.append(
                    (
                        score,
                        {
                            "doc_id": doc["doc_id"],
                            "doc_version": doc["doc_version"],
                            "doc_type": doc["doc_type"],
                            "title": doc["title"],
                            "chunk_id": chunk["chunk_id"],
                            "section": chunk.get("section"),
                            "text": chunk["text"],
                            "device_types": list(doc.get("device_types") or []),
                            "site_scope": list(doc.get("site_scope") or []),
                            "review_status": doc.get("review_status"),
                            "publication_status": doc.get("publication_status"),
                            "valid_from": doc.get("valid_from"),
                            "valid_to": doc.get("valid_to"),
                            "source_system": self.source_system,
                            "score": round(score, 4),
                        },
                    )
                )
        scored.sort(key=lambda item: (item[0], item[1]["doc_id"], item[1]["chunk_id"]), reverse=True)
        return [chunk for _, chunk in scored[:top_k]]

    @staticmethod
    def _valid_at(doc: dict[str, Any], query_time: datetime) -> bool:
        valid_from = parse_ts(doc["valid_from"])
        valid_to = parse_ts(doc["valid_to"])
        return valid_from <= query_time <= valid_to
