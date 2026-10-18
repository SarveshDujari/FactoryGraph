# FactoryGraph

**A machine stops. Understand the impact, compare the cost, approve a recovery.**

FactoryGraph is a local operations copilot for small manufacturers. FalkorDB connects machines, production stages, qualified workers, materials and customer orders so a breakdown can be traced through the factory. Scheduling and financial calculations are deterministic; operators approve production changes.

## Features

- Hourly outage analysis with downstream dependency paths and an interactive impact graph.
- Feasible machine/operator assignments, regular-shift and overtime alternatives, and material-readiness checks.
- Transparent INR estimates for downtime, idle labour, overtime, changeovers, repair labour and delay penalties. Order value at risk is shown separately.
- Exact-proposal approval, stale-context protection and replay-safe application.
- Maintenance handoffs, recorded repair outcomes and a separately approved return-to-normal plan.
- Sourced supplier and repair observations that influence later planning.
- Optional local Gemma 3 4B briefing through Ollama, with validated fact selection and an explicit offline fallback.
- Factory setup wizard, isolated workspaces, procurement workers and inventory reservations.

## Run locally

Requires Docker Desktop with Compose.

```sh
docker compose up --build -d
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Database and workflow state persist in Docker volumes. Do not remove the volumes if you need to retain factory history.

For Python development, use Python 3.11+ and uv:

```sh
uv sync --locked --extra dev
docker compose up -d graph
uv run uvicorn factory_graph.api:app --host 127.0.0.1 --port 8001
```

The development app is at [http://127.0.0.1:8001](http://127.0.0.1:8001). `.env.example` lists configuration; Python does not automatically load it. Native database defaults are `127.0.0.1:6381`; Compose configures its internal service address.

## Try the CNC demo

1. Open **Exposure**, choose **Configure a factory**, and create the supplied precision-workshop example.
2. Report **CNC One**, start hour **8**, estimated repair duration **2**. Hour 8 means day 0 at 08:00; hour 32 means day 1 at 08:00.
3. Compare the **INR 4,000** wait-for-repair baseline with the **INR 3,750** standard recovery. Inspect graph paths, order deadlines, source rates and cost components.
4. Optionally click **Generate explanation** for the Gemma briefing. Review costs and approve the selected proposal.
5. Record repair completion at hour **11**, reported cost **INR 3,000**, and a repair note.
6. Review and approve a separate return-to-normal proposal. Historical downtime and repair costs are excluded from that comparison.
7. Inspect the sourced repair memory. Use **Review record** to revisit the original comparison.

These numbers are synthetic estimates, not measured savings. The CNC example has no material shortage; the separate supplier-focused scenario demonstrates material handoffs. A new factory gives a fresh demo without deleting earlier history.

## Gemma 3 4B through Ollama

Install [Ollama](https://ollama.com), keep it running, and download the model:

```sh
ollama pull gemma3:4b
```

The briefing model defaults to `gemma3:4b`. Configure `OLLAMA_BRIEFING_MODEL` to override it. Native Python uses `http://127.0.0.1:11434`; Compose uses `http://host.docker.internal:11434`. `OLLAMA_URL` overrides the address.

Gemma selects useful summary, trade-off and review fact IDs from calculated evidence. The server validates those selections and renders their wording. It cannot invent displayed monetary figures, change assignments, purchase supplies or approve a plan. Invalid/unavailable responses use a labelled rule-based fallback. The optional legacy investigation/recommendation layer can use `OLLAMA_MODEL=gemma3:270m`; that smaller model has not been live-validated for this role. It is separate from the tested `gemma3:4b` briefing model.

## Verification

```sh
uv run pytest -m "not integration"
uv run ruff check src tests
```

For the full suite, start the database and set `RUN_INTEGRATION=1`:

```powershell
$env:RUN_INTEGRATION='1'
uv run pytest
```

The latest full local run passed **56 tests**. Coverage includes resource constraints, financial calculations, hash/revision checks, approval replay, tenant isolation, worker leases, supplier receipts, repair memory and grounded briefing fallback. Live Gemma fact selection was checked against a temporary real factory with unchanged operational state.

Reproducible CLI demonstrations:

```sh
uv run python -m factory_graph.demo
uv run python -m factory_graph.benchmark
```

They use isolated synthetic graphs and write local reports under `docs/`; generated reports are excluded from version control.

## Independent procurement workers

Set `AUTO_PROCUREMENT=0` on the app, then start workers separately:

```sh
uv run python -m factory_graph procurement --worker worker-a
uv run python -m factory_graph procurement --worker worker-b
```

Claims have owner tokens, expiry and bounded retries. Supplier confirmations differ from actual receipts; receiving creates job-linked inventory reservations. Release returns stock; consumption records its use. No external purchase is sent.

## Configuration and limits

`FALKOR_GRAPH` defaults to `factory_graph_v2`. `SHOP_TOKENS` optionally maps shop names to distinct bearer tokens; identity selects the graph server-side. Use TLS and proper secret management for any public deployment. The default setup is a loopback-only local demo.

Hourly recovery supports up to twelve affected jobs and 100,000 search visits. The legacy day-based solver supports forty affected jobs and 200,000 visits. Both fail explicitly when bounded search cannot finish. Jobs are non-splittable; interrupted hourly jobs restart in full. This is not production-scale optimization or accounting software.

Health readings are simulated threshold checks, not live telemetry or failure predictions. Financial estimates depend on configured rates and exclude tax, energy, sunk work and lost margin. FalkorDB stores operational state; SQLite stores workflow checkpoints. Preserve both stores consistently.

## Architecture and attribution

Built with Python, FastAPI, FalkorDB, LangGraph, SQLite and vanilla HTML/CSS/JavaScript, with optional Ollama. See [architecture](docs/ARCHITECTURE.md) and [provenance](docs/PROVENANCE.md).

Inspired by James Sesler's Apache-2.0 Production Orchestrator. Attribution is retained in [NOTICE](NOTICE); the project is distributed under [Apache-2.0](LICENSE). Built with substantial Codex assistance. Hackathon track eligibility is determined by the organisers; source publication does not submit an entry.
