"""Bounded exact search over graph-derived candidates; no LLM does arithmetic."""

import hashlib
import json
from collections import Counter


def digest(plan):
    return hashlib.sha256(
        json.dumps(
            {k: v for k, v in plan.items() if k != "hash"}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def plan_recovery(ctx, strategy="standard"):
    jobs = {j["id"]: j for j in ctx["jobs"]}
    affected = {row[1] for row in ctx["impact"]}
    if not affected:
        raise ValueError("No jobs affected by this incident")
    if len(affected) > 40:
        raise ValueError("Recovery supports at most forty affected jobs")
    overtime = ctx["policies"][0]["overtime"] if strategy == "overtime" else 0
    slots, qualified = {}, {}
    for job, machine, day, capacity, operator in ctx["candidates"]:
        slots[machine, day] = capacity + overtime
        qualified.setdefault((job, machine, day), set()).add(operator)
    # Jobs unaffected by the outage still reserve capacity and operator time.
    used, operator_used = {}, {}
    assignments = {}
    for j in jobs.values():
        if j["id"] in affected:
            continue
        key = j["machine"], j["day"]
        used[key] = used.get(key, 0) + j["hours"]
        operators = qualified.get((j["id"], *key), set())
        if not operators:
            raise ValueError("Existing assignment has no qualified operator")
        op = j.get("operator") or min(
            operators, key=lambda o: (operator_used.get((o, j["day"]), 0), o)
        )
        operator_used[op, j["day"]] = operator_used.get((op, j["day"]), 0) + j["hours"]
        if used[key] > slots.get(key, 0) or operator_used[op, j["day"]] > (
            ctx.get("operator_capacity", {}).get(f"{op}:{j['day']}", 8) + overtime
        ):
            raise ValueError("No feasible recovery: unaffected work exceeds capacity")
        assignments[j["id"]] = {
            "job": j["id"],
            "machine": key[0],
            "day": key[1],
            "operator": op,
            "hours": j["hours"],
        }
    pending = set(affected)
    order = []
    deps = {j: [b for a, b in ctx["dependencies"] if a == j] for j in jobs}
    while pending:
        ready = sorted(
            (j for j in pending if not (set(deps[j]) & pending)),
            key=lambda j: (-jobs[j]["priority"], jobs[j]["due"], j),
        )
        if not ready:
            raise ValueError("Dependency cycle")
        order.extend(ready)
        pending.difference_update(ready)
    procurement = []
    allocations = []
    earliest = dict(ctx.get("material_ready", {}))
    # Reserve stock once across all affected jobs. Procurement is an internal task,
    # not an assertion that material has arrived or an external purchase.
    stock = {}
    for job, material, quantity, available, supplier, lead in ctx["materials"]:
        if job not in affected:
            continue
        remaining = stock.setdefault(material, available)
        shortage = max(0, quantity - remaining)
        if min(remaining, quantity):
            allocations.append(
                {"job": job, "material": material, "quantity": min(remaining, quantity)}
            )
        stock[material] = max(0, remaining - quantity)
        if shortage:
            if supplier is None:
                raise ValueError(f"No supplier for {material}")
            earliest[job] = max(earliest.get(job, ctx["day"]), ctx["day"] + lead)
            procurement.append(
                {
                    "job": job,
                    "material": material,
                    "quantity": shortage,
                    "supplier": supplier,
                    "arrival_day": ctx["day"] + lead,
                }
            )
    best, best_score, visits = None, float("inf"), 0
    excluded = Counter()

    def search(index):
        nonlocal best, best_score, visits
        visits += 1
        if visits > 200000:
            raise ValueError("Search budget exceeded; narrow the incident")
        partial = sum(
            max(0, assignments[j]["day"] - jobs[j]["due"]) * jobs[j]["priority"] * 100
            + assignments[j]["day"] * jobs[j]["priority"]
            + (assignments[j]["machine"] != jobs[j]["machine"])
            for j in order[:index]
        )
        if partial >= best_score:
            return
        if index == len(order):
            score = sum(
                max(0, assignments[j]["day"] - jobs[j]["due"]) * jobs[j]["priority"] * 100
                + assignments[j]["day"] * jobs[j]["priority"]
                + (assignments[j]["machine"] != jobs[j]["machine"])
                for j in affected
            )
            if score < best_score:
                best_score, best = score, [dict(assignments[j]) for j in sorted(affected)]
            return
        job = order[index]
        j = jobs[job]
        for (jid, machine, day), operators in sorted(qualified.items()):
            if jid != job or day < max(ctx["day"], earliest.get(job, 0)):
                if jid == job:
                    excluded["before_material_or_incident_day"] += 1
                continue
            if machine == ctx["machine"] and ctx["day"] <= day <= ctx["until"]:
                excluded["machine_outage"] += 1
                continue
            # Day buckets: a successor starts on a later day than its prerequisite.
            if any(day <= assignments[b]["day"] for b in deps[job]):
                excluded["dependency_precedence"] += 1
                continue
            # Also respect unaffected downstream jobs that keep their existing slots.
            if any(
                a not in affected and day >= jobs[a]["day"]
                for a, b in ctx["dependencies"]
                if b == job
            ):
                continue
            key = machine, day
            if used.get(key, 0) + j["hours"] > slots[key]:
                excluded["machine_capacity"] += 1
                continue
            for operator in sorted(operators):
                opkey = operator, day
                shift = ctx.get("operator_capacity", {}).get(f"{operator}:{day}", 8)
                if operator_used.get(opkey, 0) + j["hours"] > shift + overtime:
                    excluded["operator_shift"] += 1
                    continue
                used[key] = used.get(key, 0) + j["hours"]
                operator_used[opkey] = operator_used.get(opkey, 0) + j["hours"]
                assignments[job] = {
                    "job": job,
                    "machine": machine,
                    "day": day,
                    "hours": j["hours"],
                    "operator": operator,
                }
                search(index + 1)
                del assignments[job]
                used[key] -= j["hours"]
                operator_used[opkey] -= j["hours"]

    search(0)
    if best is None:
        raise ValueError("No feasible recovery inside the configured planning horizon")
    final_assignments = {**assignments, **{a["job"]: a for a in best}}
    daily_hours = Counter()
    for assignment in final_assignments.values():
        daily_hours[assignment["operator"], assignment["day"]] += assignment["hours"]
    plan = {
        "incident_id": ctx.get("incident_id", "standalone"),
        "revision": ctx["revision"],
        "machine": ctx["machine"],
        "day": ctx["day"],
        "until": ctx["until"],
        "strategy": strategy,
        "overtime_limit": overtime,
        "assignments": best,
        "procurement": procurement,
        "allocations": allocations,
        "score": best_score,
        "impact": ctx["impact"],
        "impact_paths": ctx.get("impact_paths", []),
        "policy_sources": ctx["policies"],
        "memory_evidence": ctx.get("memories", []),
        "tool_trace": ctx.get("tool_trace", []),
        "before": [
            {"job": j, "machine": jobs[j]["machine"], "day": jobs[j]["day"]}
            for j in sorted(affected)
        ],
        "excluded_candidates": dict(excluded),
        "metrics": {
            "operator_overtime_hours": sum(
                max(0, hours - ctx.get("operator_capacity", {}).get(f"{op}:{day}", 8))
                for (op, day), hours in daily_hours.items()
            ),
            "weighted_delay": sum(
                max(0, a["day"] - jobs[a["job"]]["due"]) * jobs[a["job"]]["priority"] for a in best
            ),
            "baseline_weighted_delay": sum(
                max(0, max(jobs[j]["day"], ctx["until"] + 1) - jobs[j]["due"]) * jobs[j]["priority"]
                for j in affected
            ),
            "baseline_note": "Outage-only postponement; not a feasible optimized schedule",
            "search_visits": visits,
        },
        "late_jobs": [a["job"] for a in best if a["day"] > jobs[a["job"]]["due"]],
    }
    plan["hash"] = digest(plan)
    return plan
