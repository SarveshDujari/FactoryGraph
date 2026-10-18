import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from factory_graph.api import app, scoped_graph
from factory_graph.factory_models import example_factory
from factory_graph.operations import Operations, create_factory
from factory_graph.seed import seed
from factory_graph.store import Store

pytestmark = pytest.mark.integration


def test_existing_demo_migration_is_repeatable_and_preserves_history():
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Requires real FalkorDB")
    store = Store("test_migration_" + uuid.uuid4().hex)
    try:
        seed(store)
        store.q("CREATE (:Memory {id:'existing',category:'episodic',source:'Earlier demo'})")
        ops = Operations(store)
        first = ops.board()
        second = ops.board()
        assert first["revision"] == second["revision"]
        assert len(first["orders"]) == len(second["orders"]) > 0
        assert all(isinstance(j["start_hour"], int) for j in first["jobs"])
        assert store.board()["hourly_mode"]
        assert store.q("MATCH (m:Memory {id:'existing'}) RETURN m.source")[0][0] == "Earlier demo"
    finally:
        store.graph.delete()


@pytest.fixture
def workshop():
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Requires real FalkorDB")
    root = Store("test_ops_" + uuid.uuid4().hex)
    registration = create_factory(root, example_factory().model_dump())
    graph = root.name + "_factory_" + registration["id"]
    store = Store(graph)
    try:
        yield Operations(store), root, registration
    finally:
        store.graph.delete()
        root.graph.delete()


def test_financial_graph_recovery_replay_and_repair_memory(workshop):
    ops, _, _ = workshop
    review = ops.report("M1", 8, 2)
    assert review["affected_jobs"] == 2 and review["affected_workers"] == 2
    assert review["baseline"]["total_paise"] == 400000
    plan = review["plans"][0]
    assert plan["financial"]["total_paise"] == 375000
    assert ops.decision(review["id"], plan["hash"], True)["status"] == "applied"
    assert ops.decision(review["id"], plan["hash"], True)["replayed"]
    assert ops.board()["maintenance"][0]["status"] == "assigned"
    before = ops.s.props("Job")
    repair = ops.complete_repair(review["event"]["breakdown"], 11, 300000, "Bearing replaced")
    assert repair["observed_repair_hours"] == 3
    assert ops.s.props("Job") == before
    restored = ops.reanalyse(review["event"]["breakdown"], restoration=True)
    assert restored["plans"]
    assert restored["plans"][0]["financial"]["components"]["estimated_repair_paise"] == 0
    assert ops.s.props("Job") == before
    ops.decision(restored["id"], restored["plans"][0]["hash"], True)
    ops.s.q(
        "MATCH (m:Machine {id:'M1'}),(o:Order {id:'ORD2'}),(s:Shop) "
        "CREATE (j:Job {id:'J5',name:'Follow-up batch',kind:'machining',machine:'M1',"
        "original_machine:'M1',operator:'O1',start_hour:32,hours:2,day:1,due:1,"
        "due_hour:40,order_id:'ORD2',priority:5}) "
        "CREATE (j)-[:ASSIGNED_TO]->(m) CREATE (j)-[:FOR_ORDER]->(o) "
        "SET s.revision=s.revision+1"
    )
    next_review = ops.report("M1", 32, 1)
    assert next_review["event"]["used_repair_hours"] == 3
    assert next_review["event"]["repair_memory_used"]
    assert next_review["plans"][0]["policy"]["repair_paise_per_hour"] == 100000


def test_rejected_review_can_be_reanalysed_and_maintenance_approved_separately(workshop):
    ops, _, _ = workshop
    review = ops.report("M1", 8, 2)
    plan = review["plans"][0]
    before = ops.s.props("Job")
    ops.decision(review["id"], plan["hash"], False)
    assert ops.s.props("Job") == before
    newer = ops.reanalyse(review["event"]["breakdown"])
    assert newer["plans"][0]["hash"] != plan["hash"]
    assert ops.assign_maintenance(review["event"]["breakdown"])["status"] == "assigned"
    with pytest.raises(ValueError, match="changed"):
        ops.decision(newer["id"], newer["plans"][0]["hash"], True)
    ops.complete_repair(review["event"]["breakdown"], 10, 80000, "Repaired without schedule change")
    assert ops.board()["machines"][0]["status"] == "operational"


def test_financial_tampering_and_unoffered_plan_fail(workshop):
    ops, _, _ = workshop
    review = ops.report("M1", 8, 2)
    plan = review["plans"][0]
    altered = json.loads(json.dumps(plan))
    altered["financial"]["total_paise"] = 0
    ops.s.q(
        "MATCH (p:HourlyPlan {id:$id}) SET p.payload=$payload",
        {"id": plan["hash"], "payload": json.dumps(altered)},
    )
    with pytest.raises(ValueError, match="altered"):
        ops.decision(review["id"], plan["hash"], True)
    assert ops.s.props("MaintenanceTask")[0]["status"] == "proposed"


def test_late_repair_flags_conflicts_and_restoration_requires_review(workshop):
    ops, _, _ = workshop
    review = ops.report("M1", 8, 2)
    ops.decision(review["id"], review["plans"][0]["hash"], True)
    repair = ops.complete_repair(review["event"]["breakdown"], 15, 280000, "Long repair")
    assert "J3" in repair["jobs_needing_review"]
    restore = ops.reanalyse(review["event"]["breakdown"], restoration=True)
    assert "J3" in restore["plans"][0]["affected"]
    assert next(j for j in ops.s.props("Job") if j["id"] == "J3")["start_hour"] == 13
    ops.decision(restore["id"], restore["plans"][0]["hash"], True)
    assert next(j for j in ops.s.props("Job") if j["id"] == "J3")["start_hour"] >= 15


def test_simulated_signals_do_not_claim_prediction_or_stop_machine(workshop):
    ops, _, _ = workshop
    reading = ops.health("M1", 8, 100, 14, 99)
    assert reading["simulated"] is True
    assert reading["status"] == "inspection_advised"
    assert ops.s.props("Machine")[0]["status"] == "operational"
    assert not ops.s.props("Breakdown")


def test_factory_api_isolation_and_full_hourly_flow(monkeypatch, tmp_path):
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Requires real FalkorDB")
    base = "test_factory_api_" + uuid.uuid4().hex
    monkeypatch.setenv("FALKOR_GRAPH", base)
    monkeypatch.setenv("CHECKPOINT_PATH", str(tmp_path / "check.sqlite"))
    monkeypatch.setenv("SHOP_TOKENS", '{"alpha":"alpha-token","beta":"beta-token"}')
    child = None
    try:
        with TestClient(app) as client:
            alpha = {"Authorization": "Bearer alpha-token"}
            result = client.post(
                "/api/factories", headers=alpha, json=example_factory().model_dump()
            )
            assert result.status_code == 200, result.text
            factory = result.json()
            child = scoped_graph("alpha") + "_factory_" + factory["id"]
            headers = {**alpha, "X-Factory": factory["id"]}
            assert client.get("/api/operations", headers=headers).status_code == 200
            assert (
                client.get(
                    "/api/board",
                    headers={"Authorization": "Bearer beta-token", "X-Factory": factory["id"]},
                ).status_code
                == 404
            )
            response = client.post(
                "/api/operations/analyse",
                headers=headers,
                json={"machine": "M1", "start_hour": 8, "duration_hours": 2},
            )
            assert response.status_code == 200, response.text
            review = response.json()
            assert review["plans"]
            monkeypatch.setattr(
                "factory_graph.api.explain",
                lambda plan, baseline: {
                    "hash": plan["hash"],
                    "provider": "ollama",
                    "summary": "Read-only explanation",
                },
            )
            snapshot = client.get("/api/operations", headers=headers).json()
            briefing_url = "/api/operations/reviews/" + review["id"] + "/briefing"
            response = client.post(
                briefing_url, headers=headers, json={"hash": review["plans"][0]["hash"]}
            )
            assert response.status_code == 200 and response.json()["provider"] == "ollama"
            assert client.get("/api/operations", headers=headers).json() == snapshot
            assert (
                client.post(briefing_url, headers=headers, json={"hash": "f" * 64}).status_code
                == 409
            )
            assert (
                client.post(
                    briefing_url,
                    headers={"Authorization": "Bearer beta-token"},
                    json={"hash": review["plans"][0]["hash"]},
                ).status_code
                == 404
            )
            response = client.post(
                "/api/operations/reviews/" + review["id"] + "/decision",
                headers=headers,
                json={"hash": review["plans"][0]["hash"], "approve": True},
            )
            assert response.status_code == 200, response.text
            response = client.post(
                "/api/maintenance/" + review["event"]["breakdown"] + "/complete",
                headers=headers,
                json={"actual_end_hour": 10, "actual_cost_paise": 80000, "notes": "Test repair"},
            )
            assert response.status_code == 200, response.text
    finally:
        if child:
            Store(child).graph.delete()
        for name in (base, scoped_graph("alpha"), scoped_graph("beta")):
            Store(name).graph.delete()
