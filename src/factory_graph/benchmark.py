"""Reproducible measurements against isolated real FalkorDB scenario graphs."""

import json
import time
import uuid
from pathlib import Path

from .planner import plan_recovery
from .scenarios import SCENARIOS
from .seed import seed
from .store import Store


def naive_baseline(context):
    """Move only directly disrupted jobs to their first compatible free slot.

    Deliberately ignores dependencies, material and operator constraints. Report
    violations rather than presenting this heuristic as a feasible recovery.
    """
    jobs = {j["id"]: j for j in context["jobs"]}
    moved = {r[0] for r in context["impact"]}
    schedule = {j: {"machine": v["machine"], "day": v["day"]} for j, v in jobs.items()}
    used = {}
    for j, a in schedule.items():
        if j not in moved:
            key = a["machine"], a["day"]
            used[key] = used.get(key, 0) + jobs[j]["hours"]
    violations = []
    for job in sorted(moved):
        for jid, machine, day, capacity, _ in sorted(
            context["candidates"], key=lambda r: (r[2], r[1], r[0])
        ):
            if jid != job or day < context["day"]:
                continue
            if machine == context["machine"] and context["day"] <= day <= context["until"]:
                continue
            key = machine, day
            if used.get(key, 0) + jobs[job]["hours"] <= capacity:
                schedule[job] = {"machine": machine, "day": day}
                used[key] = used.get(key, 0) + jobs[job]["hours"]
                break
        else:
            violations.append(f"{job}: no machine slot")
    for successor, predecessor in context["dependencies"]:
        if schedule[successor]["day"] <= schedule[predecessor]["day"]:
            violations.append(f"{successor}: predecessor {predecessor} unfinished")
    for job, material, quantity, stock, supplier, lead in context["materials"]:
        if quantity > stock and (supplier is None or schedule[job]["day"] < context["day"] + lead):
            violations.append(f"{job}: {material} not available")
    return {
        "weighted_delay": sum(
            max(0, a["day"] - jobs[j]["due"]) * jobs[j]["priority"] for j, a in schedule.items()
        ),
        "known_violations": violations,
        "note": "Machine-only heuristic; operator feasibility is not evaluated",
    }


def main():
    output = []
    for scenario in SCENARIOS:
        store = Store("test_benchmark_" + uuid.uuid4().hex)
        try:
            seed(store, scenario)
            ctx = store.context("M1", 0, 0)
            row = {"scenario": scenario, "machine_only": naive_baseline(ctx), "plans": []}
            for strategy in ("standard", "overtime"):
                start = time.perf_counter()
                try:
                    plan = plan_recovery(ctx, strategy)
                    result = {
                        "strategy": strategy,
                        "metrics": plan["metrics"],
                        "late_jobs": plan["late_jobs"],
                    }
                except ValueError as exc:
                    result = {"strategy": strategy, "infeasible": str(exc)}
                result["milliseconds"] = round((time.perf_counter() - start) * 1000, 2)
                row["plans"].append(result)
            output.append(row)
        finally:
            store.graph.delete()
    Path("docs/benchmark.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
