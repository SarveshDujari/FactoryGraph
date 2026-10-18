"""Small synthetic shop. Relative integer days keep the demo reproducible."""

import os

NODES = {
    "Machine": [
        {"id": "M1", "name": "Atlas • embroidery", "kind": "embroidery"},
        {"id": "M2", "name": "Nova • embroidery", "kind": "embroidery"},
        {"id": "M3", "name": "Finish • packing", "kind": "packing"},
    ],
    "Job": [
        {
            "id": "J1",
            "name": "Hospital uniforms",
            "kind": "embroidery",
            "hours": 4,
            "due": 1,
            "priority": 10,
            "day": 0,
            "machine": "M1",
        },
        {
            "id": "J2",
            "name": "Hospital packing",
            "kind": "packing",
            "hours": 2,
            "due": 1,
            "priority": 10,
            "day": 1,
            "machine": "M3",
        },
        {
            "id": "J3",
            "name": "School team shirts",
            "kind": "embroidery",
            "hours": 4,
            "due": 2,
            "priority": 5,
            "day": 0,
            "machine": "M1",
        },
        {
            "id": "J4",
            "name": "Retail monograms",
            "kind": "embroidery",
            "hours": 6,
            "due": 2,
            "priority": 2,
            "day": 0,
            "machine": "M2",
        },
    ],
    "Customer": [
        {"id": "C1", "name": "City Hospital"},
        {"id": "C2", "name": "North School"},
        {"id": "C3", "name": "Corner Retail"},
    ],
    "Commitment": [
        {"id": "K1", "name": "Uniform delivery", "due": 1},
        {"id": "K2", "name": "Team delivery", "due": 2},
        {"id": "K3", "name": "Retail delivery", "due": 2},
    ],
    "Material": [{"id": "T1", "name": "Medical blue thread", "stock": 1}],
    "Supplier": [{"id": "S1", "name": "Local Thread Co", "lead_days": 1}],
    "Operator": [
        {"id": "O1", "name": "Asha", "skill": "embroidery"},
        {"id": "O2", "name": "Ravi", "skill": "packing"},
    ],
    "Policy": [
        {
            "id": "P1",
            "name": "Recovery policy",
            "overtime": 2,
            "source": "Synthetic shop handbook §4: at most two overtime hours per day",
        }
    ],
}


def seed(store, scenario="default"):
    """Initialize once, under the same lock as every operational write."""
    horizon = int(os.getenv("PLANNING_DAYS", "7"))
    if not 1 <= horizon <= 30:
        raise ValueError("PLANNING_DAYS must be between 1 and 30")
    with store.lock():
        if store.q("MATCH (s:Shop) RETURN s.revision"):
            return False
        store.q("CREATE (:Shop {id:'shop',revision:0})")
        for label, nodes in NODES.items():
            for props in nodes:
                store.q(f"CREATE (n:{label}) SET n = $props", {"props": props})
        for machine in NODES["Machine"]:
            for day in range(horizon):
                props = {"id": f"{machine['id']}:{day}", "day": day, "capacity": 8}
                store.q(
                    "MATCH (m:Machine {id:$id}) CREATE (m)-[:HAS_SLOT]->(s:Slot) SET s=$props",
                    {"id": machine["id"], "props": props},
                )
        for job in NODES["Job"]:
            store.q(
                "MATCH (j:Job {id:$j}),(m:Machine {id:$m}) CREATE (j)-[:ASSIGNED_TO]->(m)",
                {"j": job["id"], "m": job["machine"]},
            )
        for j, k, c in [("J2", "K1", "C1"), ("J3", "K2", "C2"), ("J4", "K3", "C3")]:
            store.q(
                "MATCH (j:Job {id:$j}),(k:Commitment {id:$k}),(c:Customer {id:$c}) "
                "CREATE (j)-[:FULFILLS]->(k)-[:PROMISED_TO]->(c)",
                {"j": j, "k": k, "c": c},
            )
        store.q("MATCH (a:Job {id:'J2'}),(b:Job {id:'J1'}) CREATE (a)-[:DEPENDS_ON]->(b)")
        store.q(
            "MATCH (j:Job {id:'J1'}),(m:Material {id:'T1'}) "
            "CREATE (j)-[:REQUIRES {quantity:3}]->(m)"
        )
        store.q("MATCH (s:Supplier),(m:Material) CREATE (s)-[:SUPPLIES]->(m)")
        store.q(
            "MATCH (o:Operator),(m:Machine) WHERE o.skill=m.kind CREATE (o)-[:QUALIFIED_FOR]->(m)"
        )
        store.q("MATCH (p:Policy),(s:Shop) CREATE (s)-[:GOVERNED_BY]->(p)")
        for operator in NODES["Operator"]:
            for day in range(horizon):
                store.q(
                    "MATCH (o:Operator {id:$id}) "
                    "CREATE (o)-[:WORKS_SHIFT]->(:Shift {day:$day,hours:8})",
                    {"id": operator["id"], "day": day},
                )
        store.remember(
            "procedural",
            "Synthetic shop handbook section 4",
            summary="Validate capacity, dependencies and material before human approval",
        )
        from .scenarios import configure

        configure(store, scenario)
        return True
