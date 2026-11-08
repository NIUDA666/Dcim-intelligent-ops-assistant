from __future__ import annotations

import asyncio

from conftest import read_events, submit_query, wait_for_status

FIXED_TIME = "2026-10-26T10:30:00+08:00"
DONE = {"success", "partial", "failed", "cancelled"}


def test_health_contract(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app_name"] == "数据中心基础设施智能运维助手"
    assert body["short_name"] == "运维助手"
    assert body["timezone"] == "Asia/Shanghai"
    assert body["source_mode"] == "mock"
    assert body["fixed_query_time"] == FIXED_TIME
    assert body["config_bundle_version"] == "demo-2026-10-26.3"
    assert body["fallback"] == "self_only"
    assert [item["id"] for item in body["scenarios"]] == [
        "ac_supply_temperature_high",
        "ac_supply_temperature_high_dx",
    ]


def test_conditional_analysis_uses_published_plan(client, session_id):
    request_id = submit_query(
        client,
        session_id,
        "一号机房AC001送风温度最近两小时趋势，如果有异常再分析",
    )
    snapshot = wait_for_status(client, request_id, DONE, timeout=20)
    assert snapshot["overall_status"] in {"success", "partial"}
    assert snapshot["fixed_query_time"] == FIXED_TIME

    tasks = {task["intent"]: task for task in snapshot["tasks"]}
    assert set(tasks) == {"metric_trend", "alarm_analysis"}

    trend = tasks["metric_trend"]
    assert trend["status"] == "success"
    assert trend["time_window"] == {
        "start": "2026-10-26T08:30:00+08:00",
        "end": FIXED_TIME,
    }
    assert trend["data"]["actual_points"] == 120
    assert trend["data"]["missing_ratio"] == 0
    assert len(trend["data"]["points"]) == 120

    analysis = tasks["alarm_analysis"]
    assert analysis["status"] in {"success", "partial"}
    assert analysis["result_type"] == "alarm_analysis"
    bundle = analysis["analysis"]
    assert bundle is not None
    assert bundle["status"] in {"completed", "partial"}
    assert bundle["config_bundle_version"] == "demo-2026-10-26.3"
    assert bundle["scenario_id"] == "ac_supply_temperature_high"
    assert bundle["scenario_version"] == "1"
    assert bundle["fallback_used"] is False
    assert bundle["plan_hash"]
    assert bundle["fixed_query_time"] == FIXED_TIME
    assert bundle["evidence_window"] == {
        "start": "2026-10-26T08:30:00+08:00",
        "end": FIXED_TIME,
    }

    assertions = bundle["anomaly_assertions"]
    statuses = {item["status"] for item in assertions}
    assert "reported_by_platform" in statuses
    assert "confirmed_abnormal" in statuses
    assert all(
        item["source"] in {"platform_alarm", "backend_evaluation"}
        for item in assertions
    )
    assert bundle["features"]
    assert any(item["status"] == "matched" for item in bundle["features"])
    assert bundle["gaps"]
    assert bundle["sop_snippets"]
    assert analysis["data"]["report"]
    assert analysis["data"]["report_mode"] in {"template", "llm", "llm_fallback_template"}

    events = read_events(client, request_id)
    event_types = [item["event"] for item in events]
    assert event_types[0] == "accepted"
    assert event_types[-1] == "done"
    assert "task_result" in event_types
    assert "answer_delta" in event_types
    assert events[-1]["data"]["overall_status"] == snapshot["overall_status"]

    ids = [item["id"] for item in events]
    assert all(item.startswith(f"{request_id}:") for item in ids)
    first_seq = int(ids[0].split(":")[1])
    replay = read_events(client, request_id, last_event_id=ids[0])
    assert replay
    assert all(int(item["id"].split(":")[1]) > first_seq for item in replay)
    assert "accepted" not in [item["event"] for item in replay]


def test_alarm_count_separates_new_occurrences_and_active(client, session_id):
    request_id = submit_query(
        client,
        session_id,
        "梳理一下今天一号机房AC001高温告警发生了几次，现在还有几条未恢复",
    )
    snapshot = wait_for_status(client, request_id, DONE)
    assert snapshot["overall_status"] == "success"

    tasks = [task for task in snapshot["tasks"] if task["intent"] == "alarm_count"]
    assert len(tasks) == 2
    by_policy = {task["data"]["policy"]: task["data"] for task in tasks}
    assert set(by_policy) == {"new_occurrences_in_window", "active_at_query_time"}

    new_count = by_policy["new_occurrences_in_window"]
    assert new_count["count"] == 3
    assert new_count["window"] == {
        "start": "2026-10-26T00:00:00+08:00",
        "end": FIXED_TIME,
    }
    active = by_policy["active_at_query_time"]
    assert active["count"] == 1
    assert active["window"] is None
    assert active["query_time"] == FIXED_TIME
    assert [task["data"]["count"] for task in tasks] == [3, 1]


def test_faq_fast_path_requires_valid_sources(client, session_id):
    request_id = submit_query(client, session_id, "精密空调巡检项有哪些")
    snapshot = wait_for_status(client, request_id, DONE)
    assert snapshot["overall_status"] == "success"
    card = snapshot["tasks"][0]
    assert card["intent"] == "knowledge_qa"
    assert card["data"]["faq_used"] is True
    assert card["data"]["route"] == "faq"
    assert len(card["data"]["sources"]) >= 2
    assert "已审核" not in card["data"]["answer"] or card["data"]["sources"]
    assert "根据已审核且当前有效的知识资料" not in card["data"]["answer"]


def test_ambiguous_device_requires_confirmation_and_stale_revision_is_rejected(
    client, session_id
):
    request_id = submit_query(client, session_id, "AC001送风温度现在多少")
    waiting = wait_for_status(client, request_id, {"waiting_user"}, timeout=10)
    assert waiting["pending_confirmations"]
    confirmation = waiting["pending_confirmations"][0]
    assert confirmation["field"] == "device"
    assert confirmation["session_revision"] == waiting["revision"]
    candidates = confirmation["candidates"]
    assert len(candidates) == 2
    assert {item["asset_id"] for item in candidates} == {"asset_ac001", "asset_ac001_r2"}

    stale = client.post(
        f"/api/v1/confirmations/{confirmation['confirmation_id']}",
        json={
            "selected_id": candidates[0]["id"],
            "session_revision": waiting["revision"] - 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_REVISION"

    accepted = client.post(
        f"/api/v1/confirmations/{confirmation['confirmation_id']}",
        json={
            "selected_id": candidates[0]["id"],
            "session_revision": waiting["revision"],
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["request_id"] == request_id

    final = wait_for_status(client, request_id, DONE)
    assert final["overall_status"] == "success"
    assert final["pending_confirmations"] == []
    card = final["tasks"][0]
    assert card["device_refs"][0]["asset_id"] == "asset_ac001"
    assert card["result_type"] == "metric_latest"
    assert card["data"]["value"] is not None


def test_query_idempotency_returns_same_request(client, session_id):
    first = submit_query(
        client,
        session_id,
        "精密空调巡检项有哪些",
        client_request_id="idem-faq-001",
    )
    second = submit_query(
        client,
        session_id,
        "精密空调巡检项有哪些",
        client_request_id="idem-faq-001",
    )
    assert second == first
    snapshot = wait_for_status(client, first, DONE)
    assert snapshot["overall_status"] == "success"


def test_feedback_is_recorded(client, session_id):
    request_id = submit_query(client, session_id, "精密空调巡检项有哪些")
    snapshot = wait_for_status(client, request_id, DONE)
    task_id = snapshot["tasks"][0]["task_id"]

    response = client.post(
        "/api/v1/feedback",
        json={"request_id": request_id, "task_id": task_id, "label": "有帮助", "comment": "pytest"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["feedback_id"].startswith("fb_")
    assert body["status"] == "recorded"

    invalid = client.post(
        "/api/v1/feedback",
        json={"request_id": request_id, "label": "   "},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "FEEDBACK_LABEL_INVALID"

    unknown = client.post(
        "/api/v1/feedback",
        json={"request_id": "req_missing", "label": "有帮助"},
    )
    assert unknown.status_code == 404


def test_cancel_is_idempotent_after_completion(client, session_id):
    request_id = submit_query(client, session_id, "精密空调巡检项有哪些")
    snapshot = wait_for_status(client, request_id, DONE)
    response = client.post(
        f"/api/v1/queries/{request_id}/cancel", json={"reason": "pytest"}
    )
    assert response.status_code == 200
    assert response.json()["overall_status"] == snapshot["overall_status"]


def test_control_request_is_rejected_without_execution(client, session_id):
    request_id = submit_query(client, session_id, "帮我关闭一号机房AC001")
    snapshot = wait_for_status(client, request_id, DONE)
    assert snapshot["overall_status"] == "failed"
    card = snapshot["tasks"][0]
    assert card["intent"] == "unsupported"
    assert card["status"] == "blocked"
    assert card["error_code"] is None
    assert any(
        error["code"] == "UNSUPPORTED_OPERATION" for error in snapshot["errors"]
    )
    assert "没有取得可用的查询结果" in snapshot["answer"]


def test_unrecognized_query_asks_for_clarification(client, session_id):
    request_id = submit_query(client, session_id, "今天天气怎么样")
    snapshot = wait_for_status(client, request_id, {"waiting_user"})
    assert snapshot["tasks"] == []
    assert snapshot["pending_confirmations"] == []
    assert any(error["code"] == "NEED_CLARIFICATION" for error in snapshot["errors"])
    assert "请说明要查询的设备" in (snapshot["answer"] or "")


def test_missing_device_and_metric_returns_chinese_follow_up(client, session_id):
    request_id = submit_query(client, session_id, "空调温度多少")
    snapshot = wait_for_status(client, request_id, {"waiting_user"})

    assert snapshot["overall_status"] == "waiting_user"
    assert snapshot["pending_confirmations"] == []
    card = snapshot["tasks"][0]
    assert card["status"] == "waiting_user"
    assert card["title"] == "温度最新值 · 待补充设备"
    assert card["clarification"]
    assert "请补充要查询的设备" in card["clarification"]
    assert "送风温度" in card["clarification"] and "回风温度" in card["clarification"]
    answer = snapshot["answer"] or ""
    assert "请补充要查询的设备" in answer
    assert "device_metric_required" not in answer
    assert "device_required" not in answer


def test_missing_metric_returns_specific_metric_options(client, session_id):
    request_id = submit_query(client, session_id, "一号机房AC001温度多少")
    snapshot = wait_for_status(client, request_id, {"waiting_user"})

    assert snapshot["overall_status"] == "waiting_user"
    card = snapshot["tasks"][0]
    assert card["status"] == "waiting_user"
    assert "请补充要查询的具体指标" in card["clarification"]
    assert "送风温度" in card["clarification"] and "回风温度" in card["clarification"]
    assert "device_required" not in (snapshot["answer"] or "")


def test_cancel_running_query(client, session_id, container, monkeypatch):
    async def slow_knowledge(record, task):
        await asyncio.sleep(30)
        raise AssertionError("cancelled task must not resume")

    monkeypatch.setattr(container.query_service, "_invoke_knowledge", slow_knowledge)
    request_id = submit_query(client, session_id, "精密空调巡检项有哪些")
    wait_for_status(client, request_id, {"running"}, timeout=5)

    response = client.post(
        f"/api/v1/queries/{request_id}/cancel", json={"reason": "pytest-cancel"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["overall_status"] == "cancelled"
    assert all(task["status"] != "success" for task in body["tasks"])
