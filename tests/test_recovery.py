import copy

import pytest

from factory_graph.planner import digest, plan_recovery


def context():
    jobs = [
        {
            "id": "J1",
            "kind": "sew",
            "hours": 4,
            "priority": 10,
            "due": 1,
            "machine": "M1",
            "day": 0,
        },
        {"id": "J2", "kind": "sew", "hours": 4, "priority": 5, "due": 2, "machine": "M1", "day": 0},
    ]
    return {
        "revision": 0,
        "machine": "M1",
        "day": 0,
        "until": 0,
        "jobs": jobs,
        "impact": [["J1", "J1", None, None], ["J2", "J2", None, None]],
        "candidates": [
            [j["id"], m, d, 4, "O1"] for j in jobs for m in ("M1", "M2") for d in range(3)
        ],
        "materials": [],
        "policies": [{"overtime": 2}],
        "dependencies": [],
    }


def test_free_capacity_is_used_and_assignment_is_unique():
    p = plan_recovery(context())
    assert len(p["assignments"]) == 2
    assert any(a["machine"] == "M2" and a["day"] == 0 for a in p["assignments"])
    assert len({a["job"] for a in p["assignments"]}) == 2


def test_multiple_displaced_jobs_reserve_future_capacity():
    c = context()
    c["candidates"] = [r for r in c["candidates"] if r[1] == "M1"]
    p = plan_recovery(c)
    assert sorted(a["day"] for a in p["assignments"]) == [1, 2]


def test_operator_is_not_double_booked_across_machines():
    c = context()
    for j in c["jobs"]:
        j["hours"] = 6
    c["candidates"] = [[r[0], r[1], r[2], 8, r[4]] for r in c["candidates"]]
    p = plan_recovery(c)
    assert len({a["day"] for a in p["assignments"]}) == 2


def test_material_arrival_and_dependency_are_respected():
    c = context()
    c["materials"] = [["J1", "T1", 3, 1, "S1", 1]]
    c["dependencies"] = [["J2", "J1"]]
    p = plan_recovery(c)
    days = {a["job"]: a["day"] for a in p["assignments"]}
    assert days["J1"] >= 1 and days["J2"] > days["J1"]
    assert p["procurement"][0]["quantity"] == 2


def test_infeasible_does_not_return_partial_plan():
    c = context()
    c["until"] = 3
    c["candidates"] = [r for r in c["candidates"] if r[1] == "M1"]
    with pytest.raises(ValueError, match="No feasible"):
        plan_recovery(c)


def test_hash_binds_exact_content():
    p = plan_recovery(context())
    altered = copy.deepcopy(p)
    altered["assignments"][0]["day"] += 1
    assert digest(altered) != p["hash"]


def test_more_than_nine_jobs_with_multiple_operators():
    c = context()
    prototype = c["jobs"][0]
    c["jobs"] = [{**prototype, "id": f"J{i}", "due": 20} for i in range(12)]
    c["impact"] = [[j["id"], j["id"], None, None] for j in c["jobs"]]
    c["candidates"] = [
        [j["id"], "M2", i, 8, op] for i, j in enumerate(c["jobs"]) for op in ("O1", "O2")
    ]
    plan = plan_recovery(c)
    assert len(plan["assignments"]) == 12


@pytest.mark.parametrize("payload", ["[]", '{"hash":[]}', '{"hash":"unknown"}'])
def test_invalid_model_recommendation_falls_back(monkeypatch, payload):
    from factory_graph.workflow import recommend

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": payload}}

    monkeypatch.setenv("OLLAMA_MODEL", "test")
    monkeypatch.setattr("factory_graph.workflow.httpx.post", lambda *a, **k: Response())
    plans = [plan_recovery(context())]
    result = recommend(plans)
    assert result["hash"] == plans[0]["hash"]
    assert "invalid" in result["provider"]
