from copy import deepcopy

import pytest
from pydantic import ValidationError

from factory_graph.factory_models import FactorySetup, example_factory
from factory_graph.hourly import cost_schedule, hourly_proposal, overlaps


def context():
    spec = example_factory().model_dump()
    orders = {o["id"]: o for o in spec["orders"]}
    machines = {m["id"]: m for m in spec["machines"]}
    jobs = [
        {
            **j,
            "kind": machines[j["machine"]]["kind"],
            "order_id": j["order"],
            "due_hour": orders[j["order"]]["due_hour"],
        }
        for j in spec["jobs"]
    ]
    return {
        "jobs": jobs,
        "operators": spec["workers"],
        "machines": spec["machines"],
        "orders": spec["orders"],
        "policy": spec["policy"],
        "revision": 0,
        "event": {
            "kind": "breakdown",
            "breakdown": "test",
            "machine": "M1",
            "start_hour": 8,
            "end_hour": 10,
            "repair_start_hour": 8,
        },
        "affected": ["J1", "J2"],
        "dependencies": [["J2", "J1"]],
        "memories": [],
        "impact_paths": [["J1"], ["J2", "J1"]],
        "blocks": [],
        "qualifications": [[w["id"], m] for w in spec["workers"] for m in w["machines"]],
    }


def test_hourly_reroute_saves_estimated_cost_without_erasing_downtime():
    ctx = context()
    baseline = hourly_proposal(ctx, "wait")
    recovery = hourly_proposal(ctx, "standard")
    assert baseline["financial"]["total_paise"] == 400000
    assert recovery["financial"]["total_paise"] == 375000
    assert (
        baseline["financial"]["components"]["equipment_downtime_paise"]
        == recovery["financial"]["components"]["equipment_downtime_paise"]
        == 240000
    )
    assert recovery["financial"]["orders"][0]["late_hours"] == 0
    assert recovery["assignments"][0]["start_hour"] == 9


def test_order_value_is_deduplicated_and_is_not_counted_as_loss():
    result = hourly_proposal(context(), "wait")["financial"]
    assert result["order_value_at_risk_paise"] == 4000000
    assert result["total_paise"] == sum(result["components"].values()) == 400000
    assert result["idle_workers"] == [
        {"worker": "O1", "hours": 2, "rate_paise": 15000, "cost_paise": 30000},
        {"worker": "O3", "hours": 2, "rate_paise": 15000, "cost_paise": 30000},
    ]


def test_hourly_schedule_respects_resources_dependencies_and_outage():
    ctx = context()
    plan = hourly_proposal(ctx, "standard")
    schedule = {j["id"]: {"job": j["id"], **j} for j in ctx["jobs"]}
    schedule.update({a["job"]: a for a in plan["assignments"]})
    jobs = list(schedule.values())
    for index, a in enumerate(jobs):
        if a["machine"] == "M1":
            assert not overlaps(a["start_hour"], a["start_hour"] + a["hours"], 8, 10)
        for b in jobs[index + 1 :]:
            if a["machine"] == b["machine"] or a["operator"] == b["operator"]:
                assert not overlaps(
                    a["start_hour"],
                    a["start_hour"] + a["hours"],
                    b["start_hour"],
                    b["start_hour"] + b["hours"],
                )
    assert schedule["J1"]["start_hour"] + 3 <= schedule["J2"]["start_hour"]


def test_technician_queue_is_not_charged_as_repair_labour():
    ctx = context()
    ctx["event"].update(repair_start_hour=10, end_hour=12)
    result = cost_schedule(ctx, [], "wait")
    assert result["components"]["estimated_repair_paise"] == 80000
    assert result["components"]["equipment_downtime_paise"] == 480000


def test_restoration_does_not_recharge_historical_repair():
    ctx = context()
    ctx["event"].update(kind="restoration", start_hour=10, end_hour=10)
    financial = cost_schedule(ctx, [], "keep_current")
    assert financial["components"]["equipment_downtime_paise"] == 0
    assert financial["components"]["estimated_repair_paise"] == 0
    assert financial["equivalent_cost_per_outage_hour_paise"] is None


def test_cost_assumptions_are_bound_to_the_proposal_hash():
    ctx = context()
    one = hourly_proposal(ctx, "standard")
    ctx["policy"]["changeover_paise"] += 100
    two = hourly_proposal(ctx, "standard")
    assert one["hash"] != two["hash"]


@pytest.mark.parametrize(
    "mutation",
    ["double_booking", "unknown_worker", "unknown_parent", "negative_cost", "duplicate_machine"],
)
def test_factory_setup_rejects_invalid_models(mutation):
    data = deepcopy(example_factory().model_dump())
    if mutation == "double_booking":
        data["jobs"][2]["start_hour"] = 9
    elif mutation == "unknown_worker":
        data["jobs"][0]["operator"] = "O999"
    elif mutation == "unknown_parent":
        data["jobs"][0]["depends_on"] = ["J999"]
    elif mutation == "negative_cost":
        data["machines"][0]["downtime_paise_per_hour"] = -1
    else:
        data["machines"].append(data["machines"][0])
    with pytest.raises(ValidationError):
        FactorySetup.model_validate(data)
