import json
import os
import time
from pathlib import Path
from typing import TypedDict

import httpx
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agents import investigate as agent_investigate
from .planner import digest, plan_recovery


class State(TypedDict, total=False):
    incident_id: str
    machine: str
    day: int
    until: int
    plans: list
    recommendation: dict
    result: dict
    investigation: list


def recommend(plans):
    """Optional local model selects only among already validated alternatives."""
    default = {
        "hash": plans[0]["hash"],
        "reason": "Lowest overtime; review late jobs before approving.",
        "provider": "deterministic policy",
    }
    model = os.getenv("OLLAMA_MODEL")
    if not model:
        return default
    try:
        response = httpx.post(
            os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": "json",
                "messages": [
                    {
                        "role": "system",
                        "content": "Recommend one plan. Return JSON with hash and reason. "
                        "Only choose a supplied hash. Never authorize execution.",
                    },
                    {"role": "user", "content": json.dumps(plans)},
                ],
            },
            timeout=45,
        )
        response.raise_for_status()
        answer = json.loads(response.json()["message"]["content"])
        if not isinstance(answer, dict):
            raise TypeError("Model answer must be an object")
        if answer.get("hash") not in {p["hash"] for p in plans}:
            raise ValueError("Unknown model selection")
        return {
            "hash": answer["hash"],
            "reason": str(answer["reason"])[:1200],
            "provider": f"Ollama / {model}",
        }
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return {**default, "provider": "deterministic policy (Ollama unavailable or invalid)"}


def decide(store, plan_hash, approve):
    with store.lock():
        plan, status = store.load_plan(plan_hash)
        if status == "applied":
            return {"status": "applied", "hash": plan_hash, "replayed": True}
        if status != "pending":
            raise ValueError("Proposal is no longer pending")
        if digest(plan) != plan_hash:
            raise ValueError("Proposal content was altered")
        if not approve:
            store.q(
                "MATCH (p:Plan {id:$id}) SET p.status='rejected' "
                "CREATE (:Decision {plan:$id,outcome:'rejected'})",
                {"id": plan_hash},
            )
            return {"status": "rejected", "hash": plan_hash}
        current = store.context(plan["machine"], plan["day"], plan["until"])
        current["incident_id"] = plan["incident_id"]
        if current["revision"] != plan["revision"]:
            raise ValueError("Shop changed after review. Start a new incident analysis.")
        if plan_recovery(current, plan["strategy"])["hash"] != plan_hash:
            raise ValueError("Graph context changed after review")
        # One graph write updates assignments, revision, provenance and outbox together.
        store.q(
            """
            MATCH (p:Plan {id:$id}),(s:Shop {id:'shop'})
            WHERE p.status='pending' AND s.revision=$revision
            SET p.status='applied',s.revision=s.revision+1
            CREATE (i:Incident {id:$id,machine:$machine,day:$day,until:$until})
            CREATE (d:Decision {plan:$id,outcome:'approved'})-[:APPROVED]->(p)
            CREATE (i)-[:RESOLVED_BY]->(p)
            CREATE (memory:Memory {id:$id,kind:'episodic',machine:$machine,
                    source:$id,created:$now,valid_until:$expiry,
                    summary:'Approved recovery; outcome awaiting operational feedback'})
            CREATE (memory)-[:ABOUT]->(p)
            WITH p,i
            UNWIND $assignments AS a
            MATCH (j:Job {id:a.job})-[old:ASSIGNED_TO]->(:Machine),
                  (m:Machine {id:a.machine})
            DELETE old
            SET j.machine=a.machine,j.day=a.day,j.operator=a.operator
            CREATE (j)-[:ASSIGNED_TO]->(m)
            CREATE (p)-[:RESCHEDULES]->(j)
            WITH DISTINCT p
            UNWIND $allocations AS allocation
            OPTIONAL MATCH (material:Material {id:allocation.material})
            SET material.stock=material.stock-allocation.quantity
            FOREACH (_ IN CASE WHEN allocation.quantity>0 THEN [1] ELSE [] END |
                CREATE (:Reservation {id:$id+':'+allocation.job+':'+allocation.material,
                    job:allocation.job,material:allocation.material,
                    quantity:allocation.quantity,status:'reserved',plan:$id}))
            WITH DISTINCT p
            UNWIND $tasks AS t
            MATCH (j:Job {id:t.job}),(m:Material {id:t.material}),
                  (s:Supplier {id:t.supplier})
            CREATE (task:Task {id:t.id,quantity:t.quantity,arrival_day:t.arrival_day,
                              order_day:$day,status:'pending',owner:'procurement',plan:$id})
            CREATE (p)-[:HANDS_OFF]->(task)-[:FOR_JOB]->(j)
            CREATE (task)-[:SOURCES_FROM]->(s)
            CREATE (task)-[:REQUESTS]->(m)
        """,
            {
                "id": plan_hash,
                "now": int(time.time()),
                "expiry": int(time.time()) + 2592000,
                "revision": plan["revision"],
                "machine": plan["machine"],
                "day": plan["day"],
                "until": plan["until"],
                "assignments": plan["assignments"],
                "allocations": plan["allocations"]
                or [{"job": "__none__", "material": "__none__", "quantity": 0}],
                "tasks": [
                    {**t, "id": f"{plan_hash}:{i}"} for i, t in enumerate(plan["procurement"])
                ],
            },
        )
        if store.load_plan(plan_hash)[1] != "applied":
            raise ValueError("Concurrent change prevented application")
        return {"status": "applied", "hash": plan_hash}


def procurement_handoff(store, worker="procurement-local"):
    """A separate worker consumes durable graph tasks; no external purchase is sent."""
    result = []
    while claim := store.claim(worker):
        with store.lock():
            rows = store.q(
                """
            MATCH (p:Plan)-[:HANDS_OFF]->(t:Task)-[:SOURCES_FROM]->(s:Supplier),
                  (t)-[:REQUESTS]->(m:Material),(t)-[:FOR_JOB]->(j:Job)
            WHERE t.id=$id AND t.status='claimed' AND t.token=$token
            SET t.status='drafted',t.draft='Request '+toString(t.quantity)+' units of '+m.name
            CREATE (:Memory {id:t.id,kind:'episodic',task:t.id,agent:'procurement',
                    source:t.id,created:$now,valid_until:$expiry,
                    summary:'Procurement worker drafted material request'})-[:ABOUT]->(t)
            RETURN t.id,s.name,j.id,t.draft
            """,
                {**claim, "now": int(time.time()), "expiry": int(time.time()) + 2592000},
            )
            result.extend(rows)
    return result


def build(store, saver):
    def investigate(state):
        with store.lock():
            trace = agent_investigate(store, state["machine"], state["day"], state["until"])
        return {"investigation": trace}

    def schedule(state):
        with store.lock():
            if store.q("MATCH (s:Shop) RETURN coalesce(s.hourly_mode,false)")[0][0]:
                raise ValueError(
                    "This factory uses hourly planning. Open Hourly exposure & repair."
                )
            ctx = store.context(state["machine"], state["day"], state["until"])
            ctx["incident_id"] = state["incident_id"]
            plans = []
            for strategy in ("standard", "overtime"):
                try:
                    plan = plan_recovery(ctx, strategy)
                    store.save_plan(plan)
                    plans.append(plan)
                except ValueError:
                    if strategy == "overtime" and not plans:
                        raise
            if not plans:
                raise ValueError("No feasible plan")
        return {"plans": plans, "recommendation": recommend(plans)}

    def approval(state):
        decision = interrupt({"plans": state["plans"], "recommendation": state["recommendation"]})
        if decision.get("hash") not in {p["hash"] for p in state["plans"]}:
            raise ValueError("Approval must name an exact proposal from this incident")
        return {"result": decide(store, decision["hash"], decision["approve"])}

    def handoff(state):
        if state["result"]["status"] == "applied" and os.getenv("AUTO_PROCUREMENT", "1") == "1":
            procurement_handoff(store)
        return {}

    graph = StateGraph(State)
    graph.add_node("investigate", investigate)
    graph.add_node("schedule", schedule)
    graph.add_node("human_review", approval)
    graph.add_node("procurement", handoff)
    graph.add_edge(START, "investigate")
    graph.add_edge("investigate", "schedule")
    graph.add_edge("schedule", "human_review")
    graph.add_edge("human_review", "procurement")
    graph.add_edge("procurement", END)
    return graph.compile(checkpointer=saver)


def run(store, thread, initial=None, decision=None, checkpoint=None):
    path = checkpoint or checkpoint_path(store)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Serialize each thread, including across separate local worker processes.
    with (
        store.redis.lock(f"{store.name}:thread:{thread}", timeout=120, blocking_timeout=10),
        SqliteSaver.from_conn_string(path) as saver,
    ):
        graph = build(store, saver)
        config = {"configurable": {"thread_id": thread}}
        previous = graph.get_state(config)
        if initial is not None and previous.values:
            raise ValueError("Incident ID already exists")
        if decision is not None:
            if not previous.values or not previous.next:
                raise ValueError("Incident is unknown or already completed")
            value = Command(resume=decision)
        else:
            value = {**initial, "incident_id": thread} if initial is not None else None
        result = graph.invoke(value, config)
        return {
            "thread": thread,
            "plans": result.get("plans", []),
            "recommendation": result.get("recommendation"),
            "investigation": result.get("investigation", []),
            "result": result.get("result"),
            "awaiting_approval": bool(result.get("__interrupt__")),
        }


def checkpoint_path(store):
    base = Path(os.getenv("CHECKPOINT_PATH", "data/checkpoints.sqlite"))
    if store.name == os.getenv("FALKOR_GRAPH", "factory_graph_v2"):
        return str(base)
    return str(base.with_name(f"{base.stem}_{store.name}{base.suffix}"))
