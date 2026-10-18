"""Graph-backed financial recovery, maintenance handoffs and simulated health observations."""

import json
import time
import uuid
from collections import defaultdict

from .factory_models import CostPolicy, FactorySetup
from .hourly import cost_schedule, hourly_proposal, overlaps, working
from .planner import digest
from .store import MATERIALS, Store


def stamp():
    return int(time.time())


class Operations:
    def __init__(self, store):
        self.s = store

    def ensure(self):
        """Enrich the existing synthetic shop once; existing records are preserved."""
        with self.s.lock():
            if self.s.q("MATCH (p:FactoryProfile {hourly_ready:true}) RETURN p"):
                return
            policy = CostPolicy(
                source="Synthetic demo assumptions — configure rates for your factory"
            ).model_dump()
            self.s.q(
                "MERGE (p:CostPolicy {id:'costs'}) ON CREATE SET p += $p "
                "MERGE (:FactoryProfile {id:'profile',name:'Demo factory'}) "
                "MERGE (:Technician {id:'TECH1',name:'Maintenance desk'})",
                {"p": policy},
            )
            machines = self.s.props("Machine")
            for machine in machines:
                self.s.q(
                    "MATCH (m:Machine {id:$id}) SET m.downtime_paise_per_hour=100000,"
                    "m.temperature_limit=80,m.vibration_limit=10,m.load_limit=95,"
                    "m.status='operational'",
                    {"id": machine["id"]},
                )
            # A labelled synthetic relief operator makes timing explicit without overbooking.
            groups = defaultdict(list)
            for machine in machines:
                groups[machine["kind"]].append(machine["id"])
            for index, (kind, ids) in enumerate(sorted(groups.items())):
                count = self.s.q(
                    "MATCH (o:Operator)-[:QUALIFIED_FOR]->(m:Machine) "
                    "WHERE m.kind=$kind RETURN count(DISTINCT o)",
                    {"kind": kind},
                )[0][0]
                if len(ids) > 1 and count < 2:
                    self.s.q(
                        "CREATE (o:Operator {id:$id,name:'Demo relief operator',skill:$kind}) "
                        "WITH o MATCH (m:Machine) WHERE m.kind=$kind "
                        "CREATE (o)-[:QUALIFIED_FOR]->(m)",
                        {"id": f"O{900 + index}", "kind": kind},
                    )
            self.s.q("MATCH (o:Operator) SET o.idle_paise_per_hour=15000")
            rows = self.s.q(
                "MATCH (j:Job)<-[:DEPENDS_ON*0..12]-(terminal:Job)-[:FULFILLS]->"
                "(c:Commitment)-[:PROMISED_TO]->(customer:Customer) "
                "RETURN DISTINCT j.id,c.id,c.due,customer.name ORDER BY j.id,c.id"
            )
            orders, job_orders = {}, {}
            for jid, oid, due, customer in rows:
                orders[oid] = {
                    "id": oid,
                    "customer": customer,
                    "due_hour": (due + 1) * 24,
                    "value_paise": 4000000 if oid == "K1" else 2500000,
                    "penalty_paise_per_hour": 10000,
                }
                job_orders.setdefault(jid, oid)
            for order in orders.values():
                self.s.q("MERGE (o:Order {id:$id}) SET o += $o", {"id": order["id"], "o": order})
            jobs = self.s.props("Job")
            qualifications = self.s.q(
                "MATCH (o:Operator)-[:QUALIFIED_FOR]->(m:Machine) RETURN o.id,m.id"
            )
            occupied = set()
            done = {}
            deps = self.s.q("MATCH (a:Job)-[:DEPENDS_ON]->(b:Job) RETURN a.id,b.id")
            pending = {j["id"]: j for j in jobs}
            while pending:
                ready = sorted(
                    (
                        j
                        for j in pending.values()
                        if all(b in done for a, b in deps if a == j["id"])
                    ),
                    key=lambda j: (j["day"], -j["priority"], j["id"]),
                )
                if not ready:
                    raise ValueError("Cannot initialise an hourly view of cyclic dependencies")
                for job in ready:
                    start = max(
                        job["day"] * 24 + 8,
                        max((done[b] for a, b in deps if a == job["id"]), default=0),
                    )
                    ops = sorted(op for op, m in qualifications if m == job["machine"])
                    chosen = None
                    for hour in range(start, 30 * 24 - job["hours"] + 1):
                        if not all(working(h, policy) for h in range(hour, hour + job["hours"])):
                            continue
                        for op in ops:
                            keys = {
                                (r, h)
                                for r in (op, job["machine"])
                                for h in range(hour, hour + job["hours"])
                            }
                            if not occupied & keys:
                                chosen = (hour, op, keys)
                                break
                        if chosen:
                            break
                    if chosen is None:
                        raise ValueError("Existing jobs need a valid shift-based initial schedule")
                    hour, op, keys = chosen
                    occupied.update(keys)
                    done[job["id"]] = hour + job["hours"]
                    oid = job_orders.get(job["id"])
                    if oid is None:
                        oid = "ORDER_" + job["id"]
                        order = {
                            "id": oid,
                            "customer": job["name"],
                            "due_hour": (job["due"] + 1) * 24,
                            "value_paise": 2500000,
                            "penalty_paise_per_hour": 10000,
                        }
                        self.s.q(
                            "MERGE (o:Order {id:$id}) SET o += $o", {"id": order["id"], "o": order}
                        )
                    self.s.q(
                        "MATCH (j:Job {id:$id}),(o:Order {id:$order}) "
                        "SET j.start_hour=$start,j.original_machine=j.machine,j.operator=$operator,"
                        "j.day=$day,j.due_hour=$due,j.order_id=$order "
                        "MERGE (j)-[:FOR_ORDER]->(o)",
                        {
                            "id": job["id"],
                            "start": hour,
                            "operator": op,
                            "day": hour // 24,
                            "due": (job["due"] + 1) * 24,
                            "order": oid,
                        },
                    )
                    pending.pop(job["id"])
            self.s.q(
                "MATCH (s:Shop),(p:FactoryProfile) SET s.hour_now=0,s.hourly_version=1,s.hourly_mode=true,s.revision=s.revision+1,p.hourly_ready=true"
            )

    def board(self):
        self.ensure()
        with self.s.lock():
            return {
                "revision": self.s.props("Shop")[0]["revision"],
                "name": self.s.props("FactoryProfile")[0]["name"],
                "hour_now": self.s.props("Shop")[0].get("hour_now", 0),
                "policy": self.s.props("CostPolicy")[0],
                "jobs": self.s.props("Job"),
                "machines": self.s.props("Machine"),
                "operators": self.s.props("Operator"),
                "orders": self.s.props("Order"),
                "breakdowns": self.s.props("Breakdown"),
                "maintenance": self.s.props("MaintenanceTask"),
                "health": [
                    r[0].properties
                    for r in self.s.q(
                        "MATCH (r:HealthReading) RETURN r ORDER BY r.created DESC,r.id LIMIT 30"
                    )
                ],
                "reviews": [
                    r[0].properties
                    for r in self.s.q(
                        "MATCH (p:HourlyReview) RETURN p ORDER BY p.created DESC,p.id LIMIT 20"
                    )
                ],
                "repair_memory": [
                    m for m in self.s.props("Memory") if "observed_repair_hours" in m
                ],
            }

    def _context(self, event):
        machines = self.s.props("Machine")
        jobs = self.s.props("Job")
        if event.get("kind") == "restoration":
            origin = self.s.q(
                "MATCH (b:Breakdown {id:$id}) RETURN b.original_jobs", {"id": event["breakdown"]}
            )[0][0]
            blocked = {
                r[0]
                for r in self.s.q(
                    "MATCH (b:Job {hourly_blocked:true}) MATCH (j:Job)-[:DEPENDS_ON*0..12]->(b) RETURN DISTINCT j.id"
                )
            }
            affected = sorted(
                j["id"]
                for j in jobs
                if (
                    (j["id"] in json.loads(origin) or j.get("original_machine") == event["machine"])
                    and j["start_hour"] >= event["start_hour"]
                )
                or j["id"] in blocked
            )
            paths = [[jid] for jid in affected]
        else:
            rows = self.s.q(
                "MATCH (m:Machine {id:$machine})<-[:ASSIGNED_TO]-(root:Job) "
                "WHERE root.start_hour < $end AND root.start_hour+root.hours > $start "
                "MATCH p=(j:Job)-[:DEPENDS_ON*0..12]->(root) "
                "RETURN DISTINCT j.id,[n IN nodes(p)|n.id] ORDER BY j.id",
                {
                    "machine": event["machine"],
                    "start": event["start_hour"],
                    "end": event["end_hour"],
                },
            )
            affected = sorted({r[0] for r in rows})
            paths = [r[1] for r in rows]
        memories = [
            r[0].properties
            for r in self.s.q(
                "MATCH (m:Memory) WHERE m.valid_until >= $now AND "
                "(m.machine=$machine OR m.kind='semantic' OR m.kind='procedural') "
                "RETURN m ORDER BY m.created,m.id LIMIT 50",
                {"now": stamp(), "machine": event["machine"]},
            )
        ]
        blocks = [
            {
                "machine": b["machine"],
                "start_hour": b["start_hour"],
                "end_hour": b.get("actual_end_hour", b["expected_end_hour"]),
            }
            for b in self.s.props("Breakdown")
            if b["id"] != event.get("breakdown") and b["status"] != "repaired"
        ]
        blocks += [
            {
                "machine": i["machine"],
                "start_hour": i["day"] * 24,
                "end_hour": (i["until"] + 1) * 24,
            }
            for i in self.s.props("Incident")
        ]
        context = {
            "revision": self.s.props("Shop")[0]["revision"],
            "event": event,
            "jobs": jobs,
            "machines": machines,
            "operators": self.s.props("Operator"),
            "orders": self.s.props("Order"),
            "policy": self.s.props("CostPolicy")[0],
            "affected": affected,
            "impact_paths": paths,
            "memories": memories,
            "blocks": blocks,
            "dependencies": self.s.q("MATCH (a:Job)-[:DEPENDS_ON]->(b:Job) RETURN a.id,b.id"),
            "qualifications": self.s.q(
                "MATCH (o:Operator)-[:QUALIFIED_FOR]->(m:Machine) RETURN o.id,m.id"
            ),
        }
        repair_rates = [
            (m["actual_repair_cost_paise"] + m["observed_repair_hours"] - 1)
            // m["observed_repair_hours"]
            for m in memories
            if m.get("machine") == event["machine"]
            and m.get("observed_repair_hours", 0) > 0
            and "actual_repair_cost_paise" in m
        ]
        context["policy"]["configured_repair_paise_per_hour"] = context["policy"][
            "repair_paise_per_hour"
        ]
        context["policy"]["repair_paise_per_hour"] = max(
            [context["policy"]["repair_paise_per_hour"], *repair_rates]
        )
        self._materials(context)
        return context

    def _materials(self, ctx):
        reserved = self.s.props("Reservation")
        incoming = self.s.q(
            "MATCH (t:Task)-[:FOR_JOB]->(j:Job),(t)-[:REQUESTS]->(m:Material) "
            "WHERE t.status IN ['pending','claimed','drafted','confirmed'] "
            "RETURN j.id,m.id,t.quantity,t.arrival_day,coalesce(t.arrival_hour,t.arrival_day*24+8)"
        )
        groups = defaultdict(list)
        for row in self.s.q(MATERIALS):
            if row[0] in ctx["affected"]:
                groups[row[0], row[1]].append(row)
        ready, allocations, tasks, stocks = {}, [], [], {}
        for (job, material), options in sorted(groups.items()):

            def lead(row):
                observations = [
                    m["observed_lead"]
                    for m in ctx["memories"]
                    if m.get("supplier") == row[4] and "observed_lead" in m
                ]
                return max([row[5] or 0, *observations])

            row = min(options, key=lambda r: (r[4] is None, lead(r), r[4] or ""))
            _, _, quantity, stock, supplier, _ = row
            credited = [
                r
                for r in reserved
                if r["job"] == job
                and r["material"] == material
                and r["status"] in ("reserved", "consumed")
            ]
            arriving = [r for r in incoming if r[0] == job and r[1] == material]
            demand = max(
                0, quantity - sum(r["quantity"] for r in credited) - sum(r[2] for r in arriving)
            )
            if credited or arriving:
                ready[job] = max(
                    [r.get("available_day", 0) * 24 + 8 for r in credited]
                    + [r[4] for r in arriving]
                )
            available = stocks.setdefault(material, stock)
            take = min(available, demand)
            if take:
                allocations.append({"job": job, "material": material, "quantity": take})
            stocks[material] = available - take
            if demand > take:
                if supplier is None:
                    raise ValueError(f"No supplier for material {material}")
                arrival = ctx["event"]["start_hour"] + lead(row) * 24
                ready[job] = max(ready.get(job, 0), arrival)
                tasks.append(
                    {
                        "job": job,
                        "material": material,
                        "supplier": supplier,
                        "quantity": demand - take,
                        "arrival_hour": arrival,
                        "arrival_day": arrival // 24,
                    }
                )
        ctx.update(material_ready=ready, allocations=allocations, procurement=tasks)

    def report(self, machine, start_hour, duration_hours, use_memory=True):
        self.ensure()
        with self.s.lock():
            known = self.s.q("MATCH (m:Machine {id:$id}) RETURN m", {"id": machine})
            if not known:
                raise ValueError("Unknown machine")
            if self.s.q(
                "MATCH (b:Breakdown {machine:$machine}) WHERE b.status<>'repaired' RETURN b.id",
                {"machine": machine},
            ):
                raise ValueError(
                    "This machine already has an open breakdown; reopen its review instead"
                )
            shop = self.s.props("Shop")[0]
            if start_hour < shop.get("hour_now", 0):
                raise ValueError("New reports cannot start before the factory reference clock")
            history = [
                m
                for m in self.s.props("Memory")
                if m.get("machine") == machine
                and m.get("valid_until", 0) >= stamp()
                and "observed_repair_hours" in m
            ]
            observed = max((m["observed_repair_hours"] for m in history), default=0)
            duration = max(duration_hours, observed) if use_memory else duration_hours
            reservations = [
                t
                for t in self.s.props("MaintenanceTask")
                if t["status"] in ("proposed", "assigned")
            ]
            candidates = []
            for tech in self.s.props("Technician"):
                available = start_hour
                for task in sorted(
                    (t for t in reservations if t["technician"] == tech["id"]),
                    key=lambda t: t["repair_start_hour"],
                ):
                    if overlaps(
                        available,
                        available + duration,
                        task["repair_start_hour"],
                        task["expected_end_hour"],
                    ):
                        available = task["expected_end_hour"]
                candidates.append((available, tech["id"]))
            repair_start, technician = min(candidates)
            end = repair_start + duration
            horizon = self.s.props("CostPolicy")[0]["horizon_days"] * 24
            if end > horizon:
                raise ValueError("Repair estimate exceeds the configured planning horizon")
            bid = uuid.uuid4().hex
            event = {
                "kind": "breakdown",
                "breakdown": bid,
                "machine": machine,
                "start_hour": start_hour,
                "end_hour": end,
                "repair_start_hour": repair_start,
                "technician": technician,
                "entered_duration_hours": duration_hours,
                "used_repair_hours": duration,
                "repair_memory_used": bool(use_memory and observed > duration_hours),
            }
            roots = self.s.q(
                "MATCH (m:Machine {id:$machine})<-[:ASSIGNED_TO]-(r:Job) "
                "WHERE r.start_hour<$end AND r.start_hour+r.hours>$start "
                "MATCH (j:Job)-[:DEPENDS_ON*0..12]->(r) RETURN DISTINCT j.id ORDER BY j.id",
                {"machine": machine, "start": start_hour, "end": end},
            )
            self.s.q(
                "MATCH (s:Shop),(m:Machine {id:$machine}),(tech:Technician {id:$tech}) "
                "SET s.revision=s.revision+1,s.hour_now=$start,s.hourly_mode=true,m.status='down' "
                "CREATE (b:Breakdown {id:$id,machine:$machine,start_hour:$start,"
                "expected_end_hour:$end,status:'reported',event:$event,original_jobs:$jobs,created:$now}) "
                "CREATE (b)-[:AFFECTS]->(m) "
                "CREATE (t:MaintenanceTask {id:$id,status:'proposed',machine:$machine,"
                "technician:$tech,repair_start_hour:$repair_start,expected_end_hour:$end,created:$now}) "
                "CREATE (b)-[:NEEDS_REPAIR]->(t)-[:ASSIGNED_TECHNICIAN]->(tech)",
                {
                    "machine": machine,
                    "tech": technician,
                    "start": start_hour,
                    "end": end,
                    "id": bid,
                    "event": json.dumps(event, sort_keys=True),
                    "jobs": json.dumps([r[0] for r in roots]),
                    "repair_start": repair_start,
                    "now": stamp(),
                },
            )
            return self._review(event)

    def _review(self, event):
        event = {**event, "analysis_id": uuid.uuid4().hex}
        ctx = self._context(event)
        plans, errors = [], []
        baseline = None
        if event["kind"] == "restoration":
            unchanged = [
                {
                    "job": j["id"],
                    "machine": j["machine"],
                    "operator": j["operator"],
                    "start_hour": j["start_hour"],
                    "hours": j["hours"],
                }
                for j in ctx["jobs"]
                if j["id"] in ctx["affected"]
            ]
            if unchanged and not any(
                j.get("hourly_blocked") for j in ctx["jobs"] if j["id"] in ctx["affected"]
            ):
                baseline = cost_schedule(ctx, unchanged, "keep_current")
        else:
            try:
                baseline = hourly_proposal(ctx, "wait")["financial"]
            except ValueError as exc:
                errors.append("Wait-for-repair baseline: " + str(exc))
        for strategy in ("standard", "overtime"):
            try:
                plan = hourly_proposal(ctx, strategy)
                self.s.q(
                    "MERGE (p:HourlyPlan {id:$id}) ON CREATE SET p.payload=$payload,p.status='pending'",
                    {"id": plan["hash"], "payload": json.dumps(plan, sort_keys=True)},
                )
                plans.append(plan)
            except ValueError as exc:
                errors.append(strategy + ": " + str(exc))
        rid = uuid.uuid4().hex
        result = {
            "id": rid,
            "event": event,
            "revision": ctx["revision"],
            "plans": plans,
            "baseline": baseline,
            "errors": errors,
            "affected_workers": len(
                {j["operator"] for j in ctx["jobs"] if j["id"] in ctx["affected"]}
            ),
            "affected_jobs": len(ctx["affected"]),
            "status": "pending" if plans else "no_feasible_plan",
        }
        self.s.q(
            "CREATE (r:HourlyReview {id:$id,breakdown:$breakdown,kind:$kind,status:$status,"
            "revision:$revision,payload:$payload,created:$now})",
            {
                "id": rid,
                "breakdown": event["breakdown"],
                "kind": event["kind"],
                "status": result["status"],
                "revision": ctx["revision"],
                "payload": json.dumps(result, sort_keys=True),
                "now": stamp(),
            },
        )
        return result

    def reanalyse(self, breakdown, restoration=False):
        self.ensure()
        with self.s.lock():
            rows = self.s.q("MATCH (b:Breakdown {id:$id}) RETURN b", {"id": breakdown})
            if not rows:
                raise ValueError("Unknown breakdown")
            b = rows[0][0].properties
            event = json.loads(b["event"])
            if restoration:
                if b["status"] != "repaired":
                    raise ValueError("Record repair completion before returning to normal")
                event = {
                    **event,
                    "kind": "restoration",
                    "start_hour": b["actual_end_hour"],
                    "end_hour": b["actual_end_hour"],
                }
            elif b["status"] == "repaired":
                raise ValueError("Machine is repaired; request a return-to-normal review")
            return self._review(event)

    def decision(self, review_id, plan_hash, approve):
        self.ensure()
        with self.s.lock():
            rows = self.s.q(
                "MATCH (r:HourlyReview {id:$id}),(p:HourlyPlan {id:$hash}) RETURN r,p",
                {"id": review_id, "hash": plan_hash},
            )
            if not rows:
                raise ValueError("Unknown review or proposal")
            review, stored = (r.properties for r in rows[0])
            data = json.loads(review["payload"])
            if plan_hash not in {p["hash"] for p in data["plans"]}:
                raise ValueError("Proposal was not offered by this review")
            if review["status"] == "applied" and review.get("selected_hash") == plan_hash:
                return {"status": "applied", "replayed": True}
            if review["status"] != "pending" or stored["status"] != "pending":
                raise ValueError("This review or proposal is already closed")
            plan = json.loads(stored["payload"])
            if digest(plan) != plan_hash:
                raise ValueError("Proposal content was altered")
            if not approve:
                self.s.q(
                    "MATCH (r:HourlyReview {id:$id}),(p:HourlyPlan {id:$hash}) "
                    "SET r.status='rejected',r.selected_hash=$hash,p.status='rejected'",
                    {"id": review_id, "hash": plan_hash},
                )
                return {"status": "rejected"}
            ctx = self._context(plan["event"])
            if ctx["revision"] != plan["revision"]:
                raise ValueError(
                    "Shop facts changed; reopen the breakdown analysis before approval"
                )
            if hourly_proposal(ctx, plan["strategy"])["hash"] != plan_hash:
                raise ValueError("Context or cost assumptions changed after review")
            # Assignments, inventory, procurement, maintenance and provenance commit together.
            self.s.q(
                """
                MATCH (r:HourlyReview {id:$review}),(p:HourlyPlan {id:$hash}),
                      (b:Breakdown {id:$breakdown}),(s:Shop)
                WHERE r.status='pending' AND p.status='pending' AND s.revision=$revision
                SET r.status='applied',r.selected_hash=$hash,p.status='applied',s.revision=s.revision+1
                FOREACH (_ IN $dispatch | SET b.status='recovery_applied')
                WITH r,p,b
                MATCH (b)-[:NEEDS_REPAIR]->(maintenance:MaintenanceTask)
                FOREACH (_ IN $dispatch | SET maintenance.status='assigned')
                CREATE (d:Decision {plan:$hash,outcome:'approved',created:$now})-[:APPROVED]->(p)
                CREATE (memory:Memory {id:$hash,kind:'episodic',machine:$machine,source:$hash,
                    created:$now,valid_until:$expiry,summary:'Hourly recovery approved; estimated costs retained'})
                CREATE (memory)-[:ABOUT]->(p)
                CREATE (r)-[:SELECTED]->(p)-[:RECOVERS]->(b)
                WITH p
                UNWIND $assignments AS a
                MATCH (j:Job {id:a.job})-[old:ASSIGNED_TO]->(:Machine),(m:Machine {id:a.machine})
                DELETE old
                SET j.machine=a.machine,j.operator=a.operator,j.start_hour=a.start_hour,
                    j.day=toInteger(a.start_hour/24),j.hourly_blocked=false
                CREATE (j)-[:ASSIGNED_TO]->(m)
                CREATE (p)-[:RESCHEDULES]->(j)
                WITH DISTINCT p
                UNWIND $allocations AS a
                OPTIONAL MATCH (material:Material {id:a.material})
                SET material.stock=material.stock-a.quantity
                FOREACH (_ IN CASE WHEN a.quantity>0 THEN [1] ELSE [] END |
                  CREATE (:Reservation {id:$hash+':'+a.job+':'+a.material,job:a.job,material:a.material,
                           quantity:a.quantity,status:'reserved',plan:$hash}))
                WITH DISTINCT p
                UNWIND $tasks AS t
                MATCH (j:Job {id:t.job}),(m:Material {id:t.material}),(supplier:Supplier {id:t.supplier})
                CREATE (task:Task {id:t.id,quantity:t.quantity,arrival_day:t.arrival_day,
                      arrival_hour:t.arrival_hour,order_day:$day,status:'pending',owner:'procurement',plan:$hash})
                CREATE (p)-[:HANDS_OFF]->(task)-[:FOR_JOB]->(j)
                CREATE (task)-[:SOURCES_FROM]->(supplier)
                CREATE (task)-[:REQUESTS]->(m)
                """,
                {
                    "review": review_id,
                    "hash": plan_hash,
                    "breakdown": plan["event"]["breakdown"],
                    "revision": plan["revision"],
                    "machine": plan["event"]["machine"],
                    "now": stamp(),
                    "expiry": stamp() + 2592000,
                    "dispatch": [1] if plan["kind"] == "breakdown" else [],
                    "assignments": plan["assignments"],
                    "day": plan["event"]["start_hour"] // 24,
                    "allocations": plan["allocations"]
                    or [{"job": "none", "material": "none", "quantity": 0}],
                    "tasks": [
                        {**t, "id": f"{plan_hash}:{i}"} for i, t in enumerate(plan["procurement"])
                    ],
                },
            )
            status = self.s.q("MATCH (r:HourlyReview {id:$id}) RETURN r.status", {"id": review_id})[
                0
            ][0]
            if status != "applied":
                raise ValueError("Concurrent change prevented application")
        from .workflow import procurement_handoff

        procurement_handoff(self.s)
        return {"status": "applied", "estimated_cost_paise": plan["financial"]["total_paise"]}

    def assign_maintenance(self, task_id):
        self.ensure()
        with self.s.lock():
            rows = self.s.q(
                "MATCH (t:MaintenanceTask {id:$id,status:'proposed'}),(s:Shop) "
                "SET t.status='assigned',s.revision=s.revision+1 "
                "CREATE (:RepairApproval {task:$id,created:$now}) RETURN t.id",
                {"id": task_id, "now": stamp()},
            )
            if not rows:
                raise ValueError("Only proposed maintenance can be approved")
            return {"status": "assigned"}

    def complete_repair(self, task_id, actual_end_hour, actual_cost_paise, notes):
        self.ensure()
        with self.s.lock():
            rows = self.s.q(
                "MATCH (b:Breakdown)-[:NEEDS_REPAIR]->(t:MaintenanceTask {id:$id}) RETURN b,t",
                {"id": task_id},
            )
            if not rows:
                raise ValueError("Unknown maintenance task")
            b, t = (v.properties for v in rows[0])
            if t["status"] != "assigned":
                raise ValueError(
                    "Approve maintenance before recording repair completion; completed repairs cannot repeat"
                )
            now_hour = self.s.props("Shop")[0].get("hour_now", 0)
            if actual_end_hour < max(t["repair_start_hour"] + 1, now_hour):
                raise ValueError(
                    "Repair completion must follow its start and the current reference clock"
                )
            if actual_end_hour > self.s.props("CostPolicy")[0]["horizon_days"] * 24:
                raise ValueError("Repair completion exceeds the planning horizon")
            observed = actual_end_hour - t["repair_start_hour"]
            conflicts = [
                j["id"]
                for j in self.s.props("Job")
                if j["machine"] == b["machine"]
                and overlaps(
                    j["start_hour"], j["start_hour"] + j["hours"], b["start_hour"], actual_end_hour
                )
            ]
            memory = {
                "id": uuid.uuid4().hex,
                "kind": "semantic",
                "machine": b["machine"],
                "source": task_id,
                "created": stamp(),
                "valid_until": stamp() + 2592000,
                "observed_repair_hours": observed,
                "actual_repair_cost_paise": actual_cost_paise,
                "summary": f"Repair completed in {observed} hours; reported repair cost recorded",
            }
            self.s.q(
                "MATCH (b:Breakdown {id:$id})-[:NEEDS_REPAIR]->(t:MaintenanceTask),"
                "(m:Machine {id:$machine}),(s:Shop) "
                "SET b.status='repaired',b.actual_end_hour=$end,t.status='completed',"
                "t.actual_end_hour=$end,t.actual_cost_paise=$cost,t.notes=$notes,"
                "m.status='operational',s.hour_now=$end,s.revision=s.revision+1 "
                "CREATE (memory:Memory) SET memory=$memory CREATE (memory)-[:ABOUT]->(t) "
                "WITH t UNWIND $jobs AS jid MATCH (j:Job {id:jid}) SET j.hourly_blocked=true",
                {
                    "id": task_id,
                    "machine": b["machine"],
                    "end": actual_end_hour,
                    "cost": actual_cost_paise,
                    "notes": notes,
                    "memory": memory,
                    "jobs": conflicts,
                },
            )
            return {
                "status": "completed",
                "observed_repair_hours": observed,
                "actual_cost_paise": actual_cost_paise,
                "jobs_needing_review": conflicts,
                "message": "Machine is available. Review a return-to-normal proposal before moving any jobs.",
            }

    def health(self, machine, hour, temperature, vibration, load):
        self.ensure()
        with self.s.lock():
            rows = self.s.q("MATCH (m:Machine {id:$id}) RETURN m", {"id": machine})
            if not rows:
                raise ValueError("Unknown machine")
            m = rows[0][0].properties
            breaches = [
                name
                for name, value, limit in [
                    ("temperature", temperature, m["temperature_limit"]),
                    ("vibration", vibration, m["vibration_limit"]),
                    ("load", load, m["load_limit"]),
                ]
                if value > limit
            ]
            reading = {
                "id": uuid.uuid4().hex,
                "machine": machine,
                "hour": hour,
                "temperature": temperature,
                "vibration": vibration,
                "load": load,
                "simulated": True,
                "created": stamp(),
                "status": "inspection_advised" if breaches else "within_thresholds",
                "breaches": ",".join(breaches),
            }
            self.s.q(
                "MATCH (m:Machine {id:$machine}) CREATE (r:HealthReading) SET r=$r "
                "CREATE (r)-[:OBSERVED_ON]->(m)",
                {"machine": machine, "r": reading},
            )
            return reading


def create_factory(root, specification):
    spec = FactorySetup.model_validate(specification)
    fid = uuid.uuid4().hex
    target = Store(root.name + "_factory_" + fid)
    try:
        policy = spec.policy.model_dump()
        target.q(
            "CREATE (:Shop {id:'shop',revision:0,hour_now:0,hourly_version:1,hourly_mode:true}) "
            "CREATE (p:CostPolicy {id:'costs'}) SET p += $policy "
            "CREATE (:FactoryProfile {id:'profile',name:$name,hourly_ready:true}) "
            "CREATE (:Technician {id:'TECH1',name:'Maintenance desk'}) "
            "CREATE (:Policy {id:'P1',name:'Configured overtime policy',overtime:$overtime,source:$source})",
            {
                "policy": policy,
                "name": spec.name,
                "overtime": policy["overtime_hours"],
                "source": policy["source"],
            },
        )
        for record in spec.machines:
            props = record.model_dump()
            props["status"] = "operational"
            target.q("CREATE (m:Machine) SET m=$props", {"props": props})
            for day in range(policy["horizon_days"]):
                target.q(
                    "MATCH (m:Machine {id:$machine}) CREATE (m)-[:HAS_SLOT]->"
                    "(:Slot {id:$id,day:$day,capacity:$hours})",
                    {
                        "machine": record.id,
                        "id": f"{record.id}:{day}",
                        "day": day,
                        "hours": policy["shift_hours"],
                    },
                )
        for record in spec.workers:
            props = record.model_dump(exclude={"machines"})
            props["skill"] = "configured"
            target.q("CREATE (o:Operator) SET o=$props", {"props": props})
            for machine in record.machines:
                target.q(
                    "MATCH (o:Operator {id:$operator}),(m:Machine {id:$machine}) "
                    "CREATE (o)-[:QUALIFIED_FOR]->(m)",
                    {"operator": record.id, "machine": machine},
                )
            for day in range(policy["horizon_days"]):
                target.q(
                    "MATCH (o:Operator {id:$operator}) CREATE (o)-[:WORKS_SHIFT]->"
                    "(:Shift {day:$day,hours:$hours})",
                    {"operator": record.id, "day": day, "hours": policy["shift_hours"]},
                )
        orders = {o.id: o for o in spec.orders}
        for order in spec.orders:
            target.q("CREATE (o:Order) SET o=$props", {"props": order.model_dump()})
            target.q(
                "CREATE (c:Customer {id:$id,name:$name}) CREATE (k:Commitment {id:$id,due:$due}) "
                "CREATE (k)-[:PROMISED_TO]->(c)",
                {"id": order.id, "name": order.customer, "due": (order.due_hour - 1) // 24},
            )
        machines = {m.id: m for m in spec.machines}
        for job in spec.jobs:
            props = job.model_dump(exclude={"depends_on", "order"})
            props.update(
                kind=machines[job.machine].kind,
                order_id=job.order,
                original_machine=job.machine,
                day=job.start_hour // 24,
                due=(orders[job.order].due_hour - 1) // 24,
                due_hour=orders[job.order].due_hour,
            )
            target.q(
                "CREATE (j:Job) SET j=$props WITH j MATCH (m:Machine {id:$machine}),"
                "(o:Order {id:$order}),(c:Commitment {id:$order}) "
                "CREATE (j)-[:ASSIGNED_TO]->(m) CREATE (j)-[:FOR_ORDER]->(o) CREATE (j)-[:FULFILLS]->(c)",
                {"props": props, "machine": job.machine, "order": job.order},
            )
        for job in spec.jobs:
            for parent in job.depends_on:
                target.q(
                    "MATCH (a:Job {id:$a}),(b:Job {id:$b}) CREATE (a)-[:DEPENDS_ON]->(b)",
                    {"a": job.id, "b": parent},
                )
        target.q("MATCH (p:Policy),(s:Shop) CREATE (s)-[:GOVERNED_BY]->(p)")
        with root.lock():
            root.q(
                "CREATE (:FactoryRegistration {id:$id,graph:$graph,name:$name,created:$now})",
                {"id": fid, "graph": target.name, "name": spec.name, "now": stamp()},
            )
        return {"id": fid, "name": spec.name}
    except Exception:
        target.graph.delete()
        raise
