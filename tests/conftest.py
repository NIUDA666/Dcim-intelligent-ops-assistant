from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def session_id(client: TestClient) -> str:
    response = client.post("/api/v1/sessions", json={"name": "pytest"})
    assert response.status_code == 201
    return response.json()["session_id"]


@pytest.fixture()
def container(client: TestClient):
    return client.app.state.container


def submit_query(
    client: TestClient,
    session_id: str,
    query: str,
    *,
    client_request_id: str | None = None,
) -> str:
    response = client.post(
        "/api/v1/queries",
        json={
            "session_id": session_id,
            "query": query,
            "client_request_id": client_request_id,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["request_id"]


def wait_for_status(
    client: TestClient,
    request_id: str,
    statuses: set[str],
    *,
    timeout: float = 15.0,
) -> dict:
    deadline = time.time() + timeout
    latest: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/v1/queries/{request_id}")
        assert response.status_code == 200
        latest = response.json()
        if latest["overall_status"] in statuses:
            return latest
        time.sleep(0.05)
    raise AssertionError(
        f"query {request_id} did not reach {sorted(statuses)}; last={latest.get('overall_status')}"
    )


def read_events(client: TestClient, request_id: str, last_event_id: str | None = None) -> list[dict]:
    headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
    response = client.get(f"/api/v1/queries/{request_id}/events", headers=headers)
    assert response.status_code == 200
    events: list[dict] = []
    for block in response.text.split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        event_id = None
        event_type = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("id: "):
                event_id = line[4:]
            elif line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data_lines.append(line[6:])
        if event_type is None:
            continue
        events.append(
            {
                "id": event_id,
                "event": event_type,
                "data": json.loads("\n".join(data_lines)) if data_lines else None,
            }
        )
    return events
