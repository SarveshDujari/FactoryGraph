import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_integration import shop  # noqa: F401

from factory_graph.planner import plan_recovery
from factory_graph.scenarios import SCENARIOS, configure
from factory_graph.seed import seed
from factory_graph.store import Store
from factory_graph.workflow import checkpoint_path, procurement_handoff, run

pytestmark = pytest.mark.integration


def approve(s, path):
    result = run(
        s, uuid.uuid4().hex, initial={"machine": "M1", "day": 0, "until": 0}, checkpoint=path
    )
    from factory_graph.workflow import decide

    decide(s, result["plans"][0]["hash"], True)
    return result


def test_memory_changes_next_plan_and_survives_new_client(shop):
    s, _ = shop
    before = plan_recovery(s.context("M1", 0, 0))
    s.remember(
        "semantic",
        "received delivery",
        supplier="S1",
        observed_lead=2,
        summary="Supplier took two days",
    )
    after = plan_recovery(Store(s.name).context("M1", 0, 0))
    assert after["procurement"][0]["arrival_day"] == 2
    assert before["hash"] != after["hash"]
    assert any(m.get("observed_lead") == 2 for m in after["memory_evidence"])


def test_competing_workers_draft_once(shop):
    s, path = shop
    approve(s, path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(
            pool.map(lambda n: procurement_handoff(Store(s.name), f"worker-{n}"), range(4))
        )
    assert sum(map(len, result)) == 1
    assert s.board()["tasks"][0]["attempts"] == 1


def test_worker_expiry_reclaim_and_failed_retry(shop):
    s, path = shop
    approve(s, path)
    first = s.claim("dead-worker", lease=-1)
    second = s.claim("replacement", lease=-1)
    assert first["id"] == second["id"] and first["token"] != second["token"]
    s.claim("third-worker", lease=-1)
    assert s.claim("fourth-worker") is None
    assert s.board()["tasks"][0]["status"] == "failed"
    s.retry_task(first["id"])
    assert len(procurement_handoff(s)) == 1


def test_feedback_receipt_and_inventory_lifecycle(shop):
    s, path = shop
    approve(s, path)
    task = s.board()["tasks"][0]
    s.feedback(task["id"], 2, received=False)
    assert s.context("M1", 0, 0)["material_ready"]["J1"] == 2
    s.feedback(task["id"], 2, received=True)
    reservations = s.board()["reservations"]
    assert sum(r["quantity"] for r in reservations) == 3
    s.release(task["id"])
    assert s.props("Material")[0]["stock"] == 2
    with pytest.raises(ValueError):
        s.release(task["id"])
    original = next(r for r in reservations if r["id"] != task["id"])
    s.release(original["id"], consume=True)
    assert s.props("Material")[0]["stock"] == 2


def test_multiple_operators_and_shift_limits(shop):
    s, _ = shop
    s.q(
        "MATCH (m:Machine {kind:'embroidery'}) "
        "MERGE (o:Operator {id:'O3',skill:'embroidery'}) CREATE (o)-[:QUALIFIED_FOR]->(m)"
    )
    context = s.context("M1", 0, 0)
    plan = plan_recovery(context)
    assert plan["assignments"]
    configure(s, "scarce_operator")
    assert s.context("M1", 0, 0)["operator_capacity"]["O1:0"] == 6


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_scenario_is_repeatable_and_isolated(shop, scenario):
    s, _ = shop
    isolated = Store(s.name + "_" + scenario)
    try:
        assert seed(isolated, scenario)
        assert not seed(isolated, scenario)
        assert checkpoint_path(isolated) != checkpoint_path(s)
        if scenario == "impossible":
            with pytest.raises(ValueError, match="No feasible"):
                plan_recovery(isolated.context("M1", 0, 0))
        else:
            assert plan_recovery(isolated.context("M1", 0, 0))["assignments"]
    finally:
        isolated.graph.delete()


def test_two_simultaneous_approvals_only_one_applies(shop):
    from factory_graph.workflow import decide

    s, path = shop
    incident = run(s, "race", initial={"machine": "M1", "day": 0, "until": 0}, checkpoint=path)

    def apply(plan):
        try:
            return decide(Store(s.name), plan["hash"], True)["status"]
        except ValueError:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, incident["plans"]))
    assert results.count("applied") == 1
    assert s.board()["revision"] == 1
