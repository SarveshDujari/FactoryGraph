"""Whole-hour scheduling and transparent estimated costs. No model supplies numbers."""

from collections import defaultdict
from copy import deepcopy

from .planner import digest


def working(hour, policy, overtime=0):
    return (
        policy["shift_start"]
        <= hour % 24
        < policy["shift_start"] + policy["shift_hours"] + overtime
    )


def overlaps(start, end, other_start, other_end):
    return start < other_end and end > other_start


def cost_schedule(ctx, assignments, strategy):
    policy, event = ctx["policy"], ctx["event"]
    before = {j["id"]: j for j in ctx["jobs"]}
    schedule = {
        j["id"]: {
            "job": j["id"],
            "machine": j["machine"],
            "operator": j["operator"],
            "start_hour": j["start_hour"],
            "hours": j["hours"],
        }
        for j in ctx["jobs"]
    }
    schedule.update({a["job"]: a for a in assignments})
    workers = {w["id"]: w for w in ctx["operators"]}
    occupied, waiting = defaultdict(set), defaultdict(set)
    for a in schedule.values():
        occupied[a["operator"]].update(range(a["start_hour"], a["start_hour"] + a["hours"]))
    for a in assignments:
        original = before[a["job"]]
        for hour in range(max(event["start_hour"], original["start_hour"]), a["start_hour"]):
            if working(hour, policy):
                waiting[original["operator"]].add(hour)
    idle_rows = []
    for worker, hours in sorted(waiting.items()):
        idle = len(hours - occupied[worker])
        if idle:
            rate = workers[worker]["idle_paise_per_hour"]
            idle_rows.append(
                {"worker": worker, "hours": idle, "rate_paise": rate, "cost_paise": idle * rate}
            )
    overtime = sum(sum(not working(h, policy) for h in hours) for hours in occupied.values())
    order_rows = []
    for order in ctx["orders"]:
        related = [j for j in ctx["jobs"] if j.get("order_id") == order["id"]]
        if not related:
            continue
        finish = max(schedule[j["id"]]["start_hour"] + j["hours"] for j in related)
        late = max(0, finish - order["due_hour"])
        order_rows.append(
            {
                **order,
                "finish_hour": finish,
                "late_hours": late,
                "penalty_paise": late * order["penalty_paise_per_hour"],
                "at_risk": late > 0,
            }
        )
    restoration = event.get("kind") == "restoration"
    outage_hours = sum(working(h, policy) for h in range(event["start_hour"], event["end_hour"]))
    machine = next(m for m in ctx["machines"] if m["id"] == event["machine"])
    downtime = 0 if restoration else outage_hours * machine["downtime_paise_per_hour"]
    repair = (
        0
        if restoration
        else (event["end_hour"] - event.get("repair_start_hour", event["start_hour"]))
        * policy["repair_paise_per_hour"]
    )
    changes = sum(a["machine"] != before[a["job"]]["machine"] for a in assignments)
    components = {
        "equipment_downtime_paise": downtime,
        "idle_labour_paise": sum(r["cost_paise"] for r in idle_rows),
        "overtime_premium_paise": overtime * policy["overtime_paise_per_hour"],
        "changeover_paise": changes * policy["changeover_paise"],
        "estimated_repair_paise": repair,
        "delay_penalties_paise": sum(o["penalty_paise"] for o in order_rows),
    }
    total = sum(components.values())
    duration = event["end_hour"] - event["start_hour"]
    return {
        "currency": "INR",
        "estimated": True,
        "strategy": strategy,
        "components": components,
        "total_paise": total,
        "equivalent_cost_per_outage_hour_paise": (total + duration // 2) // duration
        if duration
        else None,
        "outage_hours": duration,
        "productive_downtime_hours": outage_hours,
        "idle_workers": idle_rows,
        "overtime_hours": overtime,
        "orders": order_rows,
        "order_value_at_risk_paise": sum(o["value_paise"] for o in order_rows if o["at_risk"]),
        "assumptions": [
            policy["source"],
            "Money uses integer paise; costs are estimates, not booked accounting losses.",
            "Equipment downtime excludes wages; labour and contractual penalties are separate.",
            "Rerouting does not remove physical equipment downtime cost.",
            "Idle worker-hours cover delay-related waiting, are unioned and exclude hours worked on other jobs; this is not whole-shift payroll.",
            "Order value at risk is counted once per late order and is not a loss component.",
            "Repair labour excludes technician queue time; its rate is the maximum of configured and valid observed repair rates.",
            "Equivalent hourly cost divides the total estimate by outage duration; it is not a live loss rate.",
            "Interrupted non-splittable jobs restart in full; sunk work, tax, energy and lost margin are not modelled.",
            "Restoration comparisons exclude historical downtime and repair costs."
            if restoration
            else "All strategies use the same outage window and cost assumptions.",
        ],
    }


def solve_hourly(ctx, strategy="standard"):
    policy, event = ctx["policy"], ctx["event"]
    jobs = {j["id"]: j for j in ctx["jobs"]}
    affected = set(ctx["affected"])
    if not affected:
        raise ValueError("No unfinished jobs intersect this outage or restoration window")
    if len(affected) > 12:
        raise ValueError("Hourly exact recovery currently supports at most twelve affected jobs")
    deps = {j: [b for a, b in ctx["dependencies"] if a == j] for j in jobs}
    pending, order = set(affected), []
    while pending:
        ready = sorted(
            (j for j in pending if not (set(deps[j]) & pending)),
            key=lambda j: (-jobs[j]["priority"], jobs[j]["due_hour"], j),
        )
        if not ready:
            raise ValueError("Dependency cycle")
        order.extend(ready)
        pending -= set(ready)
    overtime = policy["overtime_hours"] if strategy == "overtime" else 0
    horizon = policy["horizon_days"] * 24
    assigned, used = {}, set()
    for j in jobs.values():
        if j["id"] in affected:
            continue
        a = {
            "job": j["id"],
            "machine": j["machine"],
            "operator": j["operator"],
            "start_hour": j["start_hour"],
            "hours": j["hours"],
        }
        assigned[j["id"]] = a
        for h in range(a["start_hour"], a["start_hour"] + a["hours"]):
            for resource in (a["machine"], a["operator"]):
                key = resource, h
                if key in used:
                    raise ValueError("Existing unaffected schedule double-books a resource")
                used.add(key)
    blocks = list(ctx["blocks"])
    if event.get("kind") != "restoration":
        blocks.append(
            {
                "machine": event["machine"],
                "start_hour": event["start_hour"],
                "end_hour": event["end_hour"],
            }
        )
    for a in assigned.values():
        if a["start_hour"] + a["hours"] > event["start_hour"] and any(
            b["machine"] == a["machine"]
            and overlaps(
                a["start_hour"], a["start_hour"] + a["hours"], b["start_hour"], b["end_hour"]
            )
            for b in blocks
        ):
            raise ValueError(
                "Another outage blocks unchanged work; resolve or reanalyse that breakdown first"
            )
    qualifications = sorted({tuple(q) for q in ctx["qualifications"]})
    machines = {m["id"]: m for m in ctx["machines"]}
    best, best_key, visits = None, None, 0

    def search(index):
        nonlocal best, best_key, visits
        visits += 1
        if visits > 100000:
            raise ValueError(
                "Hourly search budget exceeded; shorten the horizon or affected workload"
            )
        # A nonnegative lower bound from already assigned order penalties and changeovers.
        if best_key is not None:
            lower_penalties = 0
            for order_record in ctx["orders"]:
                ends = [
                    a["start_hour"] + a["hours"]
                    for j, a in assigned.items()
                    if jobs[j].get("order_id") == order_record["id"]
                ]
                lower_penalties += (
                    max(0, max(ends, default=0) - order_record["due_hour"])
                    * order_record["penalty_paise_per_hour"]
                )
            if lower_penalties > best_key[0]:
                return
        if index == len(order):
            result = [dict(assigned[j]) for j in sorted(affected)]
            finance = cost_schedule(ctx, result, strategy)
            lateness = sum(
                max(0, a["start_hour"] + a["hours"] - jobs[a["job"]]["due_hour"])
                * jobs[a["job"]]["priority"]
                for a in result
            )
            key = (
                finance["total_paise"],
                lateness,
                sum(a["start_hour"] + a["hours"] for a in result),
                sum(a["machine"] != jobs[a["job"]]["machine"] for a in result),
            )
            if best_key is None or key < best_key:
                best, best_key = (result, finance), key
            return
        jid = order[index]
        job = jobs[jid]
        earliest = max(
            event["start_hour"],
            ctx.get("material_ready", {}).get(jid, 0),
            max((assigned[p]["start_hour"] + assigned[p]["hours"] for p in deps[jid]), default=0),
        )
        choices = [
            (m, op)
            for op, m in qualifications
            if machines[m]["kind"] == job["kind"] and (strategy != "wait" or m == job["machine"])
        ]
        for start in range(earliest, horizon - job["hours"] + 1):
            if not all(working(h, policy, overtime) for h in range(start, start + job["hours"])):
                continue
            if start // 24 != (start + job["hours"] - 1) // 24:
                continue
            if any(
                successor not in affected and start + job["hours"] > jobs[successor]["start_hour"]
                for successor, predecessor in ctx["dependencies"]
                if predecessor == jid
            ):
                continue
            for machine, operator in choices:
                if any(
                    b["machine"] == machine
                    and overlaps(start, start + job["hours"], b["start_hour"], b["end_hour"])
                    for b in blocks
                ):
                    continue
                keys = {
                    (r, h) for r in (machine, operator) for h in range(start, start + job["hours"])
                }
                if used & keys:
                    continue
                assigned[jid] = {
                    "job": jid,
                    "machine": machine,
                    "operator": operator,
                    "start_hour": start,
                    "hours": job["hours"],
                }
                used.update(keys)
                search(index + 1)
                used.difference_update(keys)
                del assigned[jid]

    search(0)
    if best is None:
        raise ValueError("No feasible hourly recovery within the configured shifts and horizon")
    return {"assignments": best[0], "financial": best[1], "search_visits": visits}


def hourly_proposal(ctx, strategy):
    solved = solve_hourly(ctx, strategy)
    plan = {
        "kind": ctx["event"].get("kind", "breakdown"),
        "event": ctx["event"],
        "revision": ctx["revision"],
        "strategy": strategy,
        **solved,
        "affected": ctx["affected"],
        "impact_paths": ctx["impact_paths"],
        "before": [
            {
                "job": j["id"],
                "machine": j["machine"],
                "operator": j["operator"],
                "start_hour": j["start_hour"],
                "hours": j["hours"],
            }
            for j in ctx["jobs"]
            if j["id"] in ctx["affected"]
        ],
        "allocations": ctx.get("allocations", []),
        "procurement": ctx.get("procurement", []),
        "memory_evidence": ctx["memories"],
        "policy": deepcopy(ctx["policy"]),
    }
    plan["hash"] = digest(plan)
    return plan
