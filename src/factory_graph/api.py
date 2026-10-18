import hashlib
import json
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .briefing import explain
from .factory_models import FactorySetup, example_factory
from .operations import Operations, create_factory
from .scenarios import SCENARIOS
from .seed import seed
from .store import Store
from .workflow import checkpoint_path, procurement_handoff, run

request_store = ContextVar("request_store")
request_root = ContextVar("request_root")


def scoped_graph(tenant=None, scenario="default"):
    base = os.getenv("FALKOR_GRAPH", "factory_graph_v2")
    if tenant is not None:
        base += "_tenant_" + hashlib.sha256(tenant.encode()).hexdigest()[:16]
    if scenario != "default":
        base += "_scenario_" + scenario
    return base


@asynccontextmanager
async def lifespan(app):
    app.state.store = Store()
    seed(app.state.store)
    yield


app = FastAPI(title="Factory Graph", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.middleware("http")
async def shop_scope(request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    credentials = json.loads(os.getenv("SHOP_TOKENS") or "{}")
    tenant = None
    if credentials:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        tenant = next(
            (
                name
                for name, token in credentials.items()
                if secrets.compare_digest(supplied, token)
            ),
            None,
        )
        if tenant is None:
            return JSONResponse({"detail": "Valid shop access token required"}, status_code=401)
    scenario = request.headers.get("x-scenario", "default")
    if scenario not in SCENARIOS:
        return JSONResponse({"detail": "Unknown scenario"}, status_code=422)
    name = scoped_graph(tenant, scenario)
    store = Store(name)
    seed(store, scenario)
    root = store
    factory = request.headers.get("x-factory", "")
    if factory:
        try:
            uuid.UUID(factory)
        except ValueError:
            return JSONResponse({"detail": "Invalid factory identifier"}, status_code=422)
        rows = root.q("MATCH (f:FactoryRegistration {id:$id}) RETURN f.graph", {"id": factory})
        if not rows:
            return JSONResponse(
                {"detail": "Factory is not available to this shop"}, status_code=404
            )
        store = Store(rows[0][0])
    root_token = request_root.set(root)
    token = request_store.set(store)
    try:
        return await call_next(request)
    finally:
        request_store.reset(token)
        request_root.reset(root_token)


class Incident(BaseModel):
    machine: str = Field(default="M1", pattern=r"^M[0-9]+$")
    day: int = Field(default=0, ge=0, le=29)
    until: int = Field(default=0, ge=0, le=29)


class Decision(BaseModel):
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approve: bool


@app.get("/")
def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/board")
def board():
    return request_store.get().board()


@app.post("/api/incidents")
def incident(body: Incident):
    if body.until < body.day:
        raise HTTPException(422, "Outage end must follow its start")
    try:
        return run(request_store.get(), str(uuid.uuid4()), initial=body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/incidents/{thread}/decision")
def decision(thread: uuid.UUID, body: Decision):
    try:
        return run(request_store.get(), str(thread), decision=body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/incidents/{thread}")
def resume(thread: uuid.UUID):

    from langgraph.checkpoint.sqlite import SqliteSaver

    from .workflow import build

    with SqliteSaver.from_conn_string(checkpoint_path(request_store.get())) as saver:
        state = build(request_store.get(), saver).get_state(
            {"configurable": {"thread_id": str(thread)}}
        )
        if not state.values:
            raise HTTPException(404, "Unknown incident")
        return {"thread": str(thread), **state.values, "awaiting_approval": bool(state.next)}


@app.post("/api/procurement/run")
def procurement():
    return {"drafts": procurement_handoff(request_store.get())}


@app.get("/api/scenarios")
def scenarios():
    return SCENARIOS


class Feedback(BaseModel):
    arrival_day: int = Field(ge=0, le=60)
    received: bool = False


@app.post("/api/tasks/{task_id}/feedback")
def feedback(task_id: str, body: Feedback):
    try:
        return request_store.get().feedback(task_id, body.arrival_day, body.received)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class Release(BaseModel):
    consume: bool = False


@app.post("/api/reservations/{reservation_id}/close")
def close_reservation(reservation_id: str, body: Release):
    try:
        return request_store.get().release(reservation_id, body.consume)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: str):
    try:
        return request_store.get().retry_task(task_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    try:
        return request_store.get().cancel_task(task_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/factories")
def factories():
    return {
        "factories": [
            {"id": "", "name": "Original demo shop"},
            *request_root.get().props("FactoryRegistration"),
        ]
    }


@app.get("/api/factories/example")
def factory_example():
    return example_factory().model_dump()


@app.post("/api/factories")
def factory_create(body: FactorySetup):
    try:
        return create_factory(request_root.get(), body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/operations")
def operations_board():
    return Operations(request_store.get()).board()


class HourlyIncident(BaseModel):
    machine: str = Field(pattern=r"^M[0-9]{1,4}$")
    start_hour: int = Field(ge=0, le=719)
    duration_hours: int = Field(ge=1, le=24)
    use_memory: bool = True


@app.post("/api/operations/analyse")
def hourly_analyse(body: HourlyIncident):
    try:
        return Operations(request_store.get()).report(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class Reanalyse(BaseModel):
    restoration: bool = False


class BriefingRequest(BaseModel):
    hash: str = Field(pattern=r"^[a-f0-9]{64}$")


@app.post("/api/operations/reviews/{review_id}/briefing")
def recovery_briefing(review_id: uuid.UUID, body: BriefingRequest):
    store = request_store.get()
    with store.lock():
        rows = store.q("MATCH (r:HourlyReview {id:$id}) RETURN r", {"id": review_id.hex})
        if not rows:
            raise HTTPException(404, "Unknown review in this factory")
        review = json.loads(rows[0][0].properties["payload"])
        plan = next((p for p in review["plans"] if p["hash"] == body.hash), None)
        if plan is None:
            raise HTTPException(409, "Select a proposal belonging to this review")
    # Inference runs outside the factory lock and cannot mutate operational state.
    return explain(plan, review["baseline"])


@app.post("/api/operations/{breakdown}/reanalyse")
def hourly_reanalyse(breakdown: uuid.UUID, body: Reanalyse):
    try:
        return Operations(request_store.get()).reanalyse(breakdown.hex, body.restoration)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/operations/reviews/{review}/decision")
def hourly_decision(review: uuid.UUID, body: Decision):
    try:
        return Operations(request_store.get()).decision(review.hex, body.hash, body.approve)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/maintenance/{task}/assign")
def maintenance_assign(task: uuid.UUID):
    try:
        return Operations(request_store.get()).assign_maintenance(task.hex)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class RepairCompleted(BaseModel):
    actual_end_hour: int = Field(ge=1, le=720)
    actual_cost_paise: int = Field(ge=0, le=1000000000)
    notes: str = Field(default="", max_length=1000)


@app.post("/api/maintenance/{task}/complete")
def repair_completed(task: uuid.UUID, body: RepairCompleted):
    try:
        return Operations(request_store.get()).complete_repair(task.hex, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class SimulatedHealth(BaseModel):
    machine: str = Field(pattern=r"^M[0-9]{1,4}$")
    hour: int = Field(ge=0, le=720)
    temperature: int = Field(ge=0, le=300)
    vibration: int = Field(ge=0, le=100)
    load: int = Field(ge=0, le=100)


@app.post("/api/operations/health/simulate")
def health_simulate(body: SimulatedHealth):
    try:
        return Operations(request_store.get()).health(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
