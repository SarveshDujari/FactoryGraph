import json
import os
import re
import time
import uuid

from falkordb import FalkorDB
from redis import Redis

# These queries are the product's context tools, not a decorative graph export.
IMPACT = """
MATCH (m:Machine {id:$machine})<-[:ASSIGNED_TO]-(root:Job)
WHERE root.day >= $day AND root.day <= $until
MATCH (j:Job)-[:DEPENDS_ON*0..12]->(root)
OPTIONAL MATCH (j)-[:FULFILLS]->(k:Commitment)-[:PROMISED_TO]->(c:Customer)
RETURN DISTINCT root.id, j.id, k.id, c.name
ORDER BY root.id,j.id,k.id,c.name
"""
CANDIDATES = """
MATCH (j:Job),(o:Operator)-[:QUALIFIED_FOR]->(m:Machine)-[:HAS_SLOT]->(s:Slot)
WHERE j.kind=m.kind AND coalesce(m.status,'operational')<>'down'
OPTIONAL MATCH (i:Incident)
WHERE i.machine=m.id AND s.day >= i.day AND s.day <= i.until
WITH j,o,m,s,count(i) AS blocked
WHERE blocked=0
RETURN DISTINCT j.id, m.id, s.day, s.capacity, o.id
"""
MATERIALS = """
MATCH (j:Job)-[r:REQUIRES]->(m:Material)
OPTIONAL MATCH (s:Supplier)-[:SUPPLIES]->(m)
RETURN j.id,m.id,r.quantity,m.stock,s.id,s.lead_days
ORDER BY j.id,m.id,s.id
"""


class Store:
    def __init__(self, graph=None):
        self.name = graph or os.getenv("FALKOR_GRAPH", "factory_graph_v2")
        if not re.fullmatch(r"[A-Za-z0-9_]+", self.name):
            raise ValueError("Invalid graph name")
        args = {
            "host": os.getenv("FALKOR_HOST", "127.0.0.1"),
            "port": int(os.getenv("FALKOR_PORT", "6381")),
            "password": os.getenv("FALKOR_PASSWORD") or None,
            "socket_timeout": 10,
        }
        self.graph = FalkorDB(**args).select_graph(self.name)
        self.redis = Redis(**args)

    def lock(self):
        # Bounded lease for the single-service demo; not a distributed fencing protocol.
        return self.redis.lock(f"{self.name}:write", timeout=120, blocking_timeout=10)

    def q(self, query, params=None):
        return self.graph.query(query, params or {}).result_set

    def props(self, label):
        return [r[0].properties for r in self.q(f"MATCH (n:{label}) RETURN n ORDER BY n.id")]

    def context(self, machine, day, until):
        if not self.q("MATCH (m:Machine {id:$id}) RETURN m", {"id": machine}):
            raise ValueError("Unknown machine")
        memories = [
            r[0].properties
            for r in self.q(
                "MATCH (m:Memory) WHERE m.valid_until >= $now "
                "AND (m.machine=$machine OR m.kind='semantic' OR m.kind='procedural') "
                "RETURN m ORDER BY m.created DESC LIMIT 20",
                {"now": int(time.time()), "machine": machine},
            )
        ]
        materials = self.q(MATERIALS)
        for row in materials:
            observations = [
                m.get("observed_lead", 0) for m in memories if m.get("supplier") == row[4]
            ]
            if row[5] is not None:
                row[5] = max([row[5], *observations])
        # Existing reservations belong to jobs, so replanning never reserves twice.
        reserved = self.q(
            "MATCH (r:Reservation) WHERE r.status IN ['reserved','consumed'] "
            "RETURN r.job,r.material,r.quantity,coalesce(r.available_day,0)"
        )
        incoming = self.q(
            "MATCH (t:Task)-[:FOR_JOB]->(j:Job),(t)-[:REQUESTS]->(m:Material) "
            "WHERE t.status IN ['pending','claimed','drafted','confirmed'] "
            "RETURN j.id,m.id,t.quantity,t.arrival_day"
        )
        material_ready = {}
        for row in materials:
            supplies = [r for r in incoming if r[0] == row[0] and r[1] == row[1]]
            job_reserved = [r for r in reserved if r[0] == row[0] and r[1] == row[1]]
            row[2] = max(
                0,
                row[2]
                - sum(r[2] for r in reserved if r[0] == row[0] and r[1] == row[1])
                - sum(r[2] for r in supplies),
            )
            if supplies:
                material_ready[row[0]] = max(r[3] for r in supplies)
            if job_reserved:
                material_ready[row[0]] = max(
                    material_ready.get(row[0], 0), max(r[3] for r in job_reserved)
                )
        return {
            "revision": self.q("MATCH (s:Shop) RETURN s.revision")[0][0],
            "hourly_mode": bool(
                self.q("MATCH (s:Shop) RETURN coalesce(s.hourly_mode,false)")[0][0]
            ),
            "machine": machine,
            "day": day,
            "until": until,
            "impact": self.q(IMPACT, {"machine": machine, "day": day, "until": until}),
            "impact_paths": self.q(
                "MATCH (m:Machine {id:$machine})<-[:ASSIGNED_TO]-(root:Job) "
                "WHERE root.day >= $day AND root.day <= $until "
                "MATCH p=(j:Job)-[:DEPENDS_ON*0..12]->(root) "
                "OPTIONAL MATCH (j)-[:FULFILLS]->(k:Commitment)-[:PROMISED_TO]->(c:Customer) "
                "RETURN DISTINCT [n IN nodes(p) | n.id],k.id,c.name ORDER BY c.name,k.id",
                {"machine": machine, "day": day, "until": until},
            ),
            "candidates": self.q(CANDIDATES),
            "materials": materials,
            "material_ready": material_ready,
            "memories": memories,
            "operator_capacity": {
                f"{r[0]}:{r[1]}": r[2]
                for r in self.q(
                    "MATCH (o:Operator)-[:WORKS_SHIFT]->(s:Shift) RETURN o.id,s.day,s.hours"
                )
            },
            "tool_trace": [
                {
                    "agent": "investigator",
                    "tool": "trace_impact",
                    "reason": "Find work and customer promises downstream of the outage",
                },
                {
                    "agent": "scheduler",
                    "tool": "check_capacity",
                    "reason": "Match qualified operators to available machine slots",
                },
                {
                    "agent": "scheduler",
                    "tool": "check_materials",
                    "reason": "Apply inventory reservations and remembered supplier observations",
                },
            ],
            "jobs": self.props("Job"),
            "policies": self.props("Policy"),
            "dependencies": self.q("MATCH (a:Job)-[:DEPENDS_ON]->(b:Job) RETURN a.id,b.id"),
        }

    def save_plan(self, plan):
        self.q(
            "MERGE (p:Plan {id:$id}) ON CREATE SET p.payload=$payload,p.status='pending'",
            {"id": plan["hash"], "payload": json.dumps(plan, sort_keys=True)},
        )

    def load_plan(self, plan_hash):
        rows = self.q("MATCH (p:Plan {id:$id}) RETURN p.payload,p.status", {"id": plan_hash})
        if not rows:
            raise ValueError("Unknown proposal")
        return json.loads(rows[0][0]), rows[0][1]

    def board(self):
        return {
            "hourly_mode": bool(
                self.q("MATCH (s:Shop) RETURN coalesce(s.hourly_mode,false)")[0][0]
            ),
            "jobs": self.props("Job"),
            "machines": self.props("Machine"),
            "incidents": self.props("Incident"),
            "tasks": self.props("Task"),
            "decisions": self.props("Decision"),
            "memories": self.props("Memory"),
            "reservations": self.props("Reservation"),
            "revision": self.q("MATCH (s:Shop) RETURN s.revision")[0][0],
        }

    def remember(self, kind, source, **facts):
        props = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "source": source,
            "created": int(time.time()),
            "valid_until": int(time.time()) + 2592000,
            **facts,
        }
        self.q("CREATE (m:Memory) SET m=$props", {"props": props})
        return props

    def claim(self, worker, lease=30):
        now = int(time.time())
        with self.lock():
            self.q(
                "MATCH (t:Task) WHERE t.status='claimed' AND t.lease_until <= $now "
                "AND t.attempts>=3 SET t.status='failed',t.error='Worker lease expired'",
                {"now": now},
            )
            rows = self.q(
                "MATCH (t:Task) WHERE (t.status='pending' OR "
                "(t.status='claimed' AND t.lease_until <= $now)) "
                "AND coalesce(t.attempts,0)<3 RETURN t.id ORDER BY t.id LIMIT 1",
                {"now": now},
            )
            if not rows:
                return None
            token = uuid.uuid4().hex
            self.q(
                "MATCH (t:Task {id:$id}) SET t.status='claimed',t.worker=$worker,"
                "t.token=$token,t.lease_until=$until,t.attempts=coalesce(t.attempts,0)+1",
                {"id": rows[0][0], "worker": worker, "token": token, "until": now + lease},
            )
            return {"id": rows[0][0], "token": token}

    def retry_task(self, task_id):
        with self.lock():
            rows = self.q(
                "MATCH (t:Task {id:$id,status:'failed'}) "
                "SET t.status='pending',t.attempts=0 RETURN t.id",
                {"id": task_id},
            )
            if not rows:
                raise ValueError("Only failed tasks can be retried")
            return {"status": "pending"}

    def cancel_task(self, task_id):
        with self.lock():
            rows = self.q(
                "MATCH (t:Task {id:$id}),(s:Shop) "
                "WHERE NOT t.status IN ['received','cancelled'] "
                "SET t.status='cancelled',s.revision=s.revision+1 RETURN t.id",
                {"id": task_id},
            )
            if not rows:
                raise ValueError("Task unknown or already closed")
            return {"status": "cancelled", "replan_required": True}

    def feedback(self, task_id, arrival_day, received=False):
        with self.lock():
            rows = self.q(
                "MATCH (t:Task {id:$id})-[:SOURCES_FROM]->(s:Supplier),"
                "(t)-[:FOR_JOB]->(j:Job),(t)-[:REQUESTS]->(m:Material) "
                "RETURN t,s.id,j.id,m.id",
                {"id": task_id},
            )
            if not rows:
                raise ValueError("Unknown procurement task")
            task, supplier, job, material = rows[0]
            task = task.properties
            if task["status"] in ("received", "cancelled"):
                raise ValueError("Task already closed")
            if arrival_day < task.get("order_day", 0):
                raise ValueError("Arrival precedes order")
            memory = {
                "id": uuid.uuid4().hex,
                "kind": "semantic",
                "source": task_id,
                "created": int(time.time()),
                "valid_until": int(time.time()) + 2592000,
                "supplier": supplier,
                ("observed_lead" if received else "confirmed_lead"): arrival_day
                - task.get("order_day", 0),
                "summary": f"Supplier arrival day {arrival_day}; received={received}",
            }
            self.q(
                "MATCH (t:Task {id:$id}),(s:Shop) "
                "SET t.arrival_day=$arrival,t.arrival_hour=$arrival*24+8,t.status=$status,s.revision=s.revision+1 "
                "CREATE (m:Memory) SET m=$memory CREATE (m)-[:ABOUT]->(t) "
                "FOREACH (_ IN $receipt | CREATE (:Reservation {id:$id,job:$job,"
                "material:$material,quantity:$quantity,status:'reserved',"
                "available_day:$arrival,plan:$plan}))",
                {
                    "id": task_id,
                    "arrival": arrival_day,
                    "status": "received" if received else "confirmed",
                    "memory": memory,
                    "receipt": [1] if received else [],
                    "job": job,
                    "material": material,
                    "quantity": task["quantity"],
                    "plan": task["plan"],
                },
            )
            return {
                "memory": memory,
                "replan_required": True,
                "message": "Start a new analysis to review changed supplier assumptions",
            }

    def release(self, reservation_id, consume=False):
        with self.lock():
            rows = self.q(
                "MATCH (r:Reservation {id:$id,status:'reserved'}) RETURN r", {"id": reservation_id}
            )
            if not rows:
                raise ValueError("Reservation unknown or already closed")
            r = rows[0][0].properties
            self.q(
                "MATCH (r:Reservation {id:$id}),(m:Material {id:$material}),(s:Shop) "
                "SET r.status=$status,m.stock=m.stock+$quantity,s.revision=s.revision+1",
                {
                    "id": reservation_id,
                    "material": r["material"],
                    "status": "consumed" if consume else "released",
                    "quantity": 0 if consume else r["quantity"],
                },
            )
            return {"status": "consumed" if consume else "released"}
