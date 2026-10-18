"""Feed to the built container's Python stdin for a packaged Linux smoke test."""

import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from factory_graph.api import app
from factory_graph.factory_models import example_factory
from factory_graph.store import Store

name = "test_linux_" + uuid.uuid4().hex
os.environ["FALKOR_GRAPH"] = name
os.environ["CHECKPOINT_PATH"] = "/tmp/smoke.sqlite"
factory_graph = None
try:
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert "machine-schedule" in client.get("/static/app.js").text
        assert client.get("/static/styles.css").status_code == 200
        response = client.post("/api/incidents", json={"machine": "M1", "day": 0, "until": 0})
        assert response.status_code == 200, response.text
        incident = response.json()
        assert incident["awaiting_approval"]
        assert incident["plans"][0]["impact_paths"]
        response = client.post(
            "/api/incidents/" + incident["thread"] + "/decision",
            json={"hash": incident["plans"][0]["hash"], "approve": True},
        )
        assert response.status_code == 200, response.text
        board = client.get("/api/board").json()
        assert board["revision"] == 1 and board["tasks"][0]["status"] == "drafted"
        task = board["tasks"][0]
        response = client.post(
            "/api/tasks/" + task["id"] + "/feedback", json={"arrival_day": 2, "received": True}
        )
        assert response.status_code == 200, response.text
        assert response.json()["memory"]["observed_lead"] == 2
        assert client.get("/static/operations.js").status_code == 200
        assert client.get("/static/operations.css").status_code == 200
        response = client.post("/api/factories", json=example_factory().model_dump())
        assert response.status_code == 200, response.text
        factory_id = response.json()["id"]
        factory_graph = name + "_factory_" + factory_id
        headers = {"X-Factory": factory_id}
        response = client.post(
            "/api/operations/analyse",
            headers=headers,
            json={"machine": "M1", "start_hour": 8, "duration_hours": 2},
        )
        assert response.status_code == 200, response.text
        review = response.json()
        assert review["baseline"]["total_paise"] == 400000
        assert review["plans"][0]["financial"]["total_paise"] == 375000
        response = client.post(
            "/api/operations/reviews/" + review["id"] + "/briefing",
            headers=headers,
            json={"hash": review["plans"][0]["hash"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["provider"] in {"ollama", "deterministic"}
        assert response.json()["hash"] == review["plans"][0]["hash"]
        response = client.post(
            "/api/operations/reviews/" + review["id"] + "/decision",
            headers=headers,
            json={"hash": review["plans"][0]["hash"], "approve": True},
        )
        assert response.status_code == 200, response.text
        response = client.post(
            "/api/maintenance/" + review["event"]["breakdown"] + "/complete",
            headers=headers,
            json={
                "actual_end_hour": 11,
                "actual_cost_paise": 300000,
                "notes": "Packaged smoke repair",
            },
        )
        assert response.status_code == 200, response.text
        response = client.post(
            "/api/operations/" + review["event"]["breakdown"] + "/reanalyse",
            headers=headers,
            json={"restoration": True},
        )
        assert response.status_code == 200 and response.json()["plans"], response.text
        assert Path("/app/src/factory_graph/static/app.js").exists()
    print("Packaged Linux API, assets, approval, worker, receipt, hourly costs and repair passed")
finally:
    if factory_graph:
        Store(factory_graph).graph.delete()
    Store(name).graph.delete()
