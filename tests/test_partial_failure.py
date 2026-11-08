from __future__ import annotations

from conftest import submit_query, wait_for_status


def test_single_task_failure_does_not_clear_successful_results(
    client, container, session_id, monkeypatch
):
    service = container.query_service

    async def failing_trend(record, task):
        raise RuntimeError("synthetic trend failure")

    monkeypatch.setattr(service, "_invoke_trend", failing_trend)

    request_id = submit_query(
        client,
        session_id,
        "一号机房AC001送风温度最近两小时趋势；精密空调巡检项有哪些",
    )
    snapshot = wait_for_status(
        client, request_id, {"success", "partial", "failed"}, timeout=20
    )
    assert snapshot["overall_status"] == "partial"

    tasks = {task["intent"]: task for task in snapshot["tasks"]}
    assert tasks["metric_trend"]["status"] == "error"
    assert tasks["metric_trend"]["error_code"] == "TASK_EXECUTION_FAILED"
    assert tasks["knowledge_qa"]["status"] == "success"
    assert tasks["knowledge_qa"]["data"]["sources"]

    assert any(
        error.get("task_id") == tasks["metric_trend"]["task_id"]
        for error in snapshot["errors"]
    )
    assert "根据已审核且当前有效的知识资料" in (snapshot["answer"] or "")
    assert "查询失败" in (snapshot["answer"] or "")
