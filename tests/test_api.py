import os
import uuid

import pytest
from fastapi.testclient import TestClient

from factory_graph.api import app, scoped_graph
from factory_graph.store import Store

pytestmark = pytest.mark.integration


def test_authenticated_tenants_and_checkpoint_isolation(monkeypatch, tmp_path):
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Requires real FalkorDB")
    base = "test_api_" + uuid.uuid4().hex
    monkeypatch.setenv("FALKOR_GRAPH", base)
    monkeypatch.setenv("CHECKPOINT_PATH", str(tmp_path / "scope.sqlite"))
    monkeypatch.setenv("SHOP_TOKENS", '{"alpha":"alpha-test","beta":"beta-test"}')
    try:
        with TestClient(app) as client:
            assert client.get("/api/board").status_code == 401
            alpha = {"Authorization": "Bearer alpha-test"}
            beta = {"Authorization": "Bearer beta-test"}
            result = client.post(
                "/api/incidents", headers=alpha, json={"machine": "M1", "day": 0, "until": 0}
            )
            assert result.status_code == 200, result.text
            incident = result.json()
            assert (
                client.get("/api/incidents/" + incident["thread"], headers=beta).status_code == 404
            )
            assert (
                client.post(
                    "/api/incidents/" + incident["thread"] + "/decision",
                    headers=alpha,
                    json={"hash": incident["plans"][0]["hash"], "approve": True},
                ).status_code
                == 200
            )
            assert client.get("/api/board", headers=alpha).json()["revision"] == 1
            assert client.get("/api/board", headers=beta).json()["revision"] == 0
            assert (
                client.get("/api/board", headers={**alpha, "X-Scenario": "bad"}).status_code == 422
            )
    finally:
        for name in (base, scoped_graph("alpha"), scoped_graph("beta")):
            Store(name).graph.delete()
