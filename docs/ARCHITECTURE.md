# Architecture

Browser -> shop authentication/scenario scope -> bounded investigator -> deterministic scheduler -> human interrupt -> graph application -> procurement worker -> supplier feedback -> memory -> next investigation.

FalkorDB is the operational context and shared state. LangGraph checkpoints are stored in separate SQLite files per graph. Server-side token identity selects a shop graph; clients cannot supply arbitrary graph names. A scenario suffix selects a separate synthetic shop. Default graph v2 deliberately preserves the previous prototype graph.

## Investigation and scheduling

The investigator can select named read-only Cypher tools with optional Ollama; unknown tool names and invalid/unavailable responses fall back to a labelled policy. It has no arbitrary query or execution access. Scheduler validation always retrieves the complete required operational context independently, so omitted model tool choices cannot omit safety constraints. Investigator-selected tools and the scheduler's required checks are separately visible in returned evidence.

Impact traversal follows up to twelve dependency edges and returns actual paths, including intermediate jobs. Resource candidates follow operator qualifications, machine slots and recorded outages. Daily operator shifts constrain assignments. Missing shift records retain the prototype's eight-hour default. Existing unaffected work also reserves capacity. Daily buckets enforce strict next-day precedence.

The bounded exact solver supports up to forty affected jobs, applies a nonnegative partial-score lower bound, and fails explicitly after 200,000 visits. The default seeded horizon is seven days, configurable to 1–30. It does not claim production-scale optimization, split work, shift-level start times, holidays or transit calendars.

## Approval and inventory

Canonical SHA-256 binds the complete proposal, including retrieved memory, evidence paths, assignments, allocations and metrics. On approval the current context and solver result must reproduce the reviewed hash. Revision checks and a graph write condition guard competing approvals. A single graph query applies schedule changes, records the decision and incident, reserves stock, and creates handoff tasks. Replay returns the already-applied result.

Reservations are attributed to jobs so repeated planning does not reserve the same material again. Reserved or consumed quantities satisfy that job's requirement. Open incoming tasks cover only their linked job and impose arrival readiness. Received supplies become reservations instead of being counted as generally free stock. Release returns stock; consumption closes a reservation without returning stock. Cancelled procurement does not automatically release separate stock reservations: the UI exposes both actions explicitly.

Supplier feedback writes task status, revision, sourced memory and any receipt reservation together in one graph query. A confirmation changes only that task's expected arrival. Actual receipts create observed lead history; the planner uses valid observations conservatively for future shortages. Feedback invalidates outstanding proposals and requests a fresh human-reviewed analysis.

## Memory and coordination

Episodic approvals/handoffs, semantic supplier observations and sourced procedural context persist in FalkorDB. Retrieval selects up to twenty valid records matching the machine or shared semantic/procedural context. Expiry is thirty days by default. Procedures are explanatory context; they are not executable arbitrary code. The actual policy constraints remain graph facts.

Investigation and scheduling are distinct durable workflow stages, not independently autonomous distributed LLMs. Procurement may run in a different process. Worker claims are serialized, owner-token checked, expiring and bounded to three attempts. Expired exhausted claims become failed on the next claim scan; retry is explicit. Multiple workers and competing approvals have real-database tests. Mutations serialize per graph, so concurrency correctness is demonstrated, not unlimited write throughput.

## Operational limits

The hourly workspace uses a separate bounded exact scheduler: whole-hour bookings,
up to twelve affected jobs, 100,000 search visits, explicit worker qualifications,
shift boundaries, dependency completion and material readiness. It compares wait,
standard-capacity and overtime strategies. A workspace using hourly scheduling
rejects legacy day-based scheduling writes to protect its explicit bookings.

Financial estimates use integer paise for equipment downtime, delay-related idle
labour, overtime premiums, changeovers, repair labour and contractual penalties.
Order value at risk is separate from loss. Repair completion records actual
outcomes and sourced observations; moving production again requires a separate
restoration approval. Machine-health readings are explicitly simulated.

Gemma 3 4B runs through Ollama as an optional read-only briefing editor. It selects
IDs from server-calculated facts; the server validates those IDs and supplies the
displayed wording. Model responses cannot introduce arbitrary figures, schedules
or actions. Inference runs outside the factory lock. Missing models, timeouts and
invalid selections return a labelled deterministic fallback. Briefings are scoped
to a factory-owned review and exact proposal hash.

Redis leases last 120 seconds. There is no cross-store distributed transaction; a graph application is idempotent but graph and SQLite backups must remain consistent. Initial seeding is not a crash-recoverable import pipeline. Authentication uses server-configured bearer tokens, not a complete account management product. Default mode remains loopback-only. Use a fresh v2 graph after upgrading; old pending plan payloads are intentionally not silently migrated.
