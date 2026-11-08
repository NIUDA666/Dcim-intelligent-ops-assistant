from __future__ import annotations

from conftest import submit_query, wait_for_status

DOC = """# 精密空调换季检查

## 检查项

换季前检查冷凝器翅片、过滤网和加湿罐，确认排水通畅。

## 安全边界

涉及停机操作时必须执行工作票，不得仅按本文档直接操作。
"""


def test_import_registers_pending_review_document(client):
    response = client.post(
        "/api/v1/documents",
        files={"file": ("season_check.md", DOC.encode("utf-8"), "text/markdown")},
        data={
            "title": "精密空调换季检查",
            "doc_type": "sop",
            "doc_version": "draft-1",
            "device_types": "precision_air_conditioner",
            "site_scope": "site_01",
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["import_task_id"].startswith("imp_")
    assert body["doc_id"].startswith("import_")
    assert body["status"] == "pending_review"
    assert "imported_document_pending_review" in body["warnings"]

    detail = client.get(f"/api/v1/import-tasks/{body['import_task_id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["chunks"] >= 2
    assert detail_body["filename"] == "season_check.md"
    assert detail_body["metadata"]["review_status"] == "pending"
    assert detail_body["metadata"]["publication_status"] == "draft"
    assert detail_body["metadata"]["doc_version"] == "draft-1"
    assert {stage["name"] for stage in detail_body["stages"]} == {
        "upload",
        "parse",
        "review",
    }


def test_imported_draft_is_not_searchable(client):
    response = client.post(
        "/api/v1/documents",
        files={"file": ("unique_import.md", DOC.encode("utf-8"), "text/markdown")},
        data={"title": "独特术语换季检查", "doc_type": "sop"},
    )
    assert response.status_code == 202

    session = client.post("/api/v1/sessions", json={"name": "import-check"}).json()
    request_id = submit_query(
        client, session["session_id"], "独特术语换季检查有哪些注意事项"
    )
    snapshot = wait_for_status(client, request_id, {"success", "partial", "failed"})
    card = snapshot["tasks"][0]
    sources = (card.get("data") or {}).get("sources") or []
    assert all(not item["doc_id"].startswith("import_") for item in sources)
    assert "import_" not in ((card.get("data") or {}).get("answer") or "")


def test_import_rejects_unsupported_file_and_doc_type(client):
    unsupported = client.post(
        "/api/v1/documents",
        files={"file": ("manual.pdf", b"%PDF-1.4", "application/pdf")},
        data={"doc_type": "manual"},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "FILE_TYPE_UNSUPPORTED"

    invalid_type = client.post(
        "/api/v1/documents",
        files={"file": ("manual.md", DOC.encode("utf-8"), "text/markdown")},
        data={"doc_type": "policy"},
    )
    assert invalid_type.status_code == 422
    assert invalid_type.json()["detail"]["code"] == "DOC_TYPE_INVALID"

    empty = client.post(
        "/api/v1/documents",
        files={"file": ("empty.md", b"   \n", "text/markdown")},
        data={"doc_type": "sop"},
    )
    assert empty.status_code == 422
    assert empty.json()["detail"]["code"] == "FILE_EMPTY"

    missing = client.get("/api/v1/import-tasks/imp_missing")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "IMPORT_TASK_NOT_FOUND"
