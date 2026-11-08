"""资料导入（§7.2 / §7.7.6 的演示实现）。

首版只接收 UTF-8 的 Markdown/TXT，按标题和段落切块，登记为
`pending / draft`，必须人工审核发布后才会进入检索；不会自动把上传内容
当作已批准 SOP，也不会覆盖同 ID 的既有版本。
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.adapters.mock_knowledge import MockKnowledgeAdapter
from app.services.clock import clock

_MAX_BYTES = 1_000_000
_ALLOWED_SUFFIXES = {".md", ".txt", ".markdown"}
_ALLOWED_DOC_TYPES = {"sop", "manual", "case"}


class ImportValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ImportTaskRecord:
    import_task_id: str
    doc_id: str
    filename: str
    status: str
    stages: list[dict[str, Any]] = field(default_factory=list)
    chunks: int = 0
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: clock.now())
    updated_at: datetime = field(default_factory=lambda: clock.now())


class ImportService:
    def __init__(self, knowledge: MockKnowledgeAdapter):
        self.knowledge = knowledge
        self.tasks: dict[str, ImportTaskRecord] = {}

    async def create(
        self,
        *,
        filename: str,
        content: bytes,
        title: str | None,
        doc_type: str,
        doc_version: str | None,
        device_types: str | None,
        site_scope: str | None,
    ) -> ImportTaskRecord:
        clean_name = (filename or "").replace("\\", "/").split("/")[-1].strip()
        suffix = "." + clean_name.rsplit(".", 1)[-1].lower() if "." in clean_name else ""
        if suffix not in _ALLOWED_SUFFIXES:
            raise ImportValidationError("FILE_TYPE_UNSUPPORTED", "仅支持 Markdown 或 TXT 文件")
        if len(content) > _MAX_BYTES:
            raise ImportValidationError("FILE_TOO_LARGE", "文件大小超过演示上限 1 MB")
        if doc_type not in _ALLOWED_DOC_TYPES:
            raise ImportValidationError("DOC_TYPE_INVALID", "doc_type 只能是 sop、manual 或 case")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ImportValidationError("FILE_ENCODING_INVALID", "文件必须为 UTF-8 编码") from exc
        if not text.strip():
            raise ImportValidationError("FILE_EMPTY", "文件没有可导入的文本内容")

        doc_id = f"import_{uuid.uuid4().hex[:10]}"
        digest = hashlib.sha256(content).hexdigest()
        chunk_records = _chunk_text(doc_id, text)
        if not chunk_records:
            raise ImportValidationError("FILE_NO_CHUNKS", "未能从文件中切分出有效内容")
        now = clock.now()
        document = {
            "doc_id": doc_id,
            "doc_version": (doc_version or f"draft-{now:%Y%m%d%H%M}").strip(),
            "doc_type": doc_type,
            "title": (title or clean_name.rsplit(".", 1)[0]).strip(),
            "device_types": _split_scope(device_types),
            "site_scope": _split_scope(site_scope),
            "review_status": "pending",
            "publication_status": "draft",
            "valid_from": now.isoformat(),
            "valid_to": (now + timedelta(days=3650)).isoformat(),
            "source_revision_hash": f"sha256:{digest}",
            "source_system": "knowledge-import",
            "chunks": chunk_records,
        }
        self.knowledge.register_document(document)
        record = ImportTaskRecord(
            import_task_id=f"imp_{uuid.uuid4().hex[:10]}",
            doc_id=doc_id,
            filename=clean_name,
            status="pending_review",
            stages=[
                {"name": "upload", "status": "completed"},
                {"name": "parse", "status": "completed", "chunks": len(chunk_records)},
                {
                    "name": "review",
                    "status": "pending",
                    "message": "需人工审核并发布后才会参与检索",
                },
            ],
            chunks=len(chunk_records),
            warnings=["imported_document_pending_review"],
            metadata={
                "title": document["title"],
                "doc_type": doc_type,
                "doc_version": document["doc_version"],
                "device_types": document["device_types"],
                "site_scope": document["site_scope"],
                "review_status": "pending",
                "publication_status": "draft",
                "source_revision_hash": document["source_revision_hash"],
            },
        )
        self.tasks[record.import_task_id] = record
        return record

    def get(self, import_task_id: str) -> ImportTaskRecord:
        record = self.tasks.get(import_task_id)
        if record is None:
            raise ImportValidationError("IMPORT_TASK_NOT_FOUND", "导入任务不存在")
        return record


def _split_scope(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[,，;；\s]+", value) if item.strip()]


def _chunk_text(doc_id: str, text: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    section = ""
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        content = "\n".join(buffer).strip()
        if not content:
            buffer = []
            return
        chunks.append(
            {
                "chunk_id": f"{doc_id}#{len(chunks) + 1}",
                "section": section or None,
                "text": content[:1200],
            }
        )
        buffer = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            section = stripped.lstrip("#").strip()
            continue
        if not stripped:
            flush()
            continue
        buffer.append(stripped)
        if sum(len(item) for item in buffer) >= 800:
            flush()
    flush()
    return chunks
