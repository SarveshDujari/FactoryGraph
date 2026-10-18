"""Validated factory configuration; money is integer paise, time is whole clock hours."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MachineSpec(Record):
    id: str = Field(pattern=r"^M[0-9]{1,4}$")
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(min_length=1, max_length=40)
    downtime_paise_per_hour: int = Field(default=100000, ge=0, le=100000000)
    temperature_limit: int = Field(default=80, ge=1, le=300)
    vibration_limit: int = Field(default=10, ge=1, le=100)
    load_limit: int = Field(default=95, ge=1, le=100)


class WorkerSpec(Record):
    id: str = Field(pattern=r"^O[0-9]{1,4}$")
    name: str = Field(min_length=1, max_length=80)
    machines: list[str] = Field(min_length=1, max_length=20)
    idle_paise_per_hour: int = Field(default=15000, ge=0, le=10000000)


class OrderSpec(Record):
    id: str = Field(pattern=r"^[A-Za-z0-9_]{1,30}$")
    customer: str = Field(min_length=1, max_length=80)
    due_hour: int = Field(ge=1, le=720)
    value_paise: int = Field(ge=0, le=10000000000)
    penalty_paise_per_hour: int = Field(default=10000, ge=0, le=100000000)


class JobSpec(Record):
    id: str = Field(pattern=r"^J[0-9]{1,4}$")
    name: str = Field(min_length=1, max_length=80)
    machine: str
    operator: str
    start_hour: int = Field(ge=0, le=719)
    hours: int = Field(ge=1, le=8)
    order: str
    priority: int = Field(default=5, ge=1, le=100)
    depends_on: list[str] = Field(default_factory=list, max_length=20)


class CostPolicy(Record):
    shift_start: int = Field(default=8, ge=0, le=16)
    shift_hours: int = Field(default=8, ge=1, le=12)
    overtime_hours: int = Field(default=2, ge=0, le=4)
    overtime_paise_per_hour: int = Field(default=10000, ge=0, le=10000000)
    changeover_paise: int = Field(default=25000, ge=0, le=100000000)
    repair_paise_per_hour: int = Field(default=40000, ge=0, le=100000000)
    horizon_days: int = Field(default=7, ge=2, le=30)
    source: str = Field(
        default="Participant-configured planning assumptions", min_length=1, max_length=300
    )

    @model_validator(mode="after")
    def shift_fits_day(self):
        if self.shift_start + self.shift_hours + self.overtime_hours > 24:
            raise ValueError("Shift plus overtime must finish within the same calendar day")
        return self


class FactorySetup(Record):
    name: str = Field(min_length=1, max_length=80)
    machines: list[MachineSpec] = Field(min_length=1, max_length=12)
    workers: list[WorkerSpec] = Field(min_length=1, max_length=30)
    orders: list[OrderSpec] = Field(min_length=1, max_length=30)
    jobs: list[JobSpec] = Field(min_length=1, max_length=30)
    policy: CostPolicy = Field(default_factory=CostPolicy)

    @model_validator(mode="after")
    def connected_and_feasible(self):
        for records in (self.machines, self.workers, self.orders, self.jobs):
            if len({r.id for r in records}) != len(records):
                raise ValueError("Identifiers must be unique within each record type")
        machines = {m.id: m for m in self.machines}
        workers = {w.id: w for w in self.workers}
        orders = {o.id: o for o in self.orders}
        jobs = {j.id: j for j in self.jobs}
        for worker in self.workers:
            if set(worker.machines) - machines.keys():
                raise ValueError("Worker qualification names an unknown machine")
        occupied = set()
        for job in self.jobs:
            if (
                job.machine not in machines
                or job.operator not in workers
                or job.order not in orders
            ):
                raise ValueError("Every job needs a known machine, operator and order")
            if job.machine not in workers[job.operator].machines:
                raise ValueError("Job operator is not qualified for its machine")
            if set(job.depends_on) - jobs.keys() or job.id in job.depends_on:
                raise ValueError("Dependency names an unknown job or the job itself")
            hour = job.start_hour % 24
            if (
                hour < self.policy.shift_start
                or hour + job.hours > self.policy.shift_start + self.policy.shift_hours
                or job.start_hour + job.hours > self.policy.horizon_days * 24
            ):
                raise ValueError("Initial jobs must fit regular shifts and the configured horizon")
            for h in range(job.start_hour, job.start_hour + job.hours):
                for resource in (job.machine, job.operator):
                    key = resource, h
                    if key in occupied:
                        raise ValueError("Initial schedule double-books a machine or operator")
                    occupied.add(key)
            if any(jobs[p].start_hour + jobs[p].hours > job.start_hour for p in job.depends_on):
                raise ValueError("A job cannot start before its prerequisite finishes")
        remaining = set(jobs)
        while remaining:
            ready = {j for j in remaining if not (set(jobs[j].depends_on) & remaining)}
            if not ready:
                raise ValueError("Job dependencies contain a cycle")
            remaining -= ready
        return self


def example_factory():
    return FactorySetup.model_validate(
        {
            "name": "Demo precision workshop",
            "machines": [
                {
                    "id": "M1",
                    "name": "CNC One",
                    "kind": "machining",
                    "downtime_paise_per_hour": 120000,
                },
                {
                    "id": "M2",
                    "name": "CNC Two",
                    "kind": "machining",
                    "downtime_paise_per_hour": 100000,
                },
                {
                    "id": "M3",
                    "name": "Inspection bench",
                    "kind": "inspection",
                    "downtime_paise_per_hour": 30000,
                },
            ],
            "workers": [
                {"id": "O1", "name": "Asha", "machines": ["M1", "M2"]},
                {"id": "O2", "name": "Ravi", "machines": ["M1", "M2"]},
                {"id": "O3", "name": "Meera", "machines": ["M3"]},
            ],
            "orders": [
                {
                    "id": "ORD1",
                    "customer": "Metro Components",
                    "due_hour": 14,
                    "value_paise": 4000000,
                    "penalty_paise_per_hour": 20000,
                },
                {
                    "id": "ORD2",
                    "customer": "North Engineering",
                    "due_hour": 40,
                    "value_paise": 2500000,
                    "penalty_paise_per_hour": 10000,
                },
            ],
            "jobs": [
                {
                    "id": "J1",
                    "name": "Machine precision brackets",
                    "machine": "M1",
                    "operator": "O1",
                    "start_hour": 8,
                    "hours": 3,
                    "order": "ORD1",
                    "priority": 10,
                },
                {
                    "id": "J2",
                    "name": "Inspect brackets",
                    "machine": "M3",
                    "operator": "O3",
                    "start_hour": 11,
                    "hours": 2,
                    "order": "ORD1",
                    "priority": 10,
                    "depends_on": ["J1"],
                },
                {
                    "id": "J3",
                    "name": "Mill pump housings",
                    "machine": "M1",
                    "operator": "O1",
                    "start_hour": 13,
                    "hours": 3,
                    "order": "ORD2",
                    "priority": 5,
                },
                {
                    "id": "J4",
                    "name": "Finish retail batch",
                    "machine": "M2",
                    "operator": "O2",
                    "start_hour": 8,
                    "hours": 1,
                    "order": "ORD2",
                    "priority": 2,
                },
            ],
        }
    )
