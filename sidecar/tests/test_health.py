"""Smoke test for the /health endpoint.

Doubles as a proof that the test harness — TestClient + Settings via env —
is wired up correctly. Real behavioral tests for the agent's subsystems
land in the tasks that implement those subsystems (TDD discipline).
"""

from fastapi.testclient import TestClient


def test_health_endpoint_returns_healthy(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    # `policy_loaded` is False here because the default test client doesn't
    # wire a Redis backend; the dedicated policy-status test covers the
    # loaded-true path with a recording-redis fixture.
    assert body["policy_loaded"] is False
