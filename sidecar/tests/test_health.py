"""Smoke test for the /health endpoint.

Doubles as a proof that the test harness — TestClient + Settings via env —
is wired up correctly. Real behavioral tests for the agent's subsystems
land in the tasks that implement those subsystems (TDD discipline).
"""

from fastapi.testclient import TestClient


def test_health_endpoint_returns_healthy(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
