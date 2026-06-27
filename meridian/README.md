# Meridian — Plan. Fly. Land.

> Agentic travel concierge on Aurora PostgreSQL, MCP, and Strands Agents.

Meridian is a workshop demo that climbs a five-phase capability ladder: Query → Tool → Intent → Trust → Durable Workflow. The technical modes are SQL → MCP → Retrieval → Production → Workflow, and the live UI talks to a real FastAPI backend backed by Aurora (RDS Data API + pgvector), not a client-side mock.

## Quick start

### Prerequisites

- Python 3.11+
- Node.js 20.19+ (or 22.12+)
- AWS credentials with Bedrock and RDS Data API access
- Aurora PostgreSQL 17 cluster with pgvector (or provision via `scripts/create_cluster.sh`)

### Backend

```bash
cd meridian
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in Aurora cluster ARN, secret ARN, and region

python scripts/init_aurora_schema.py
python scripts/seed_data.py

uvicorn backend.main:app --reload --port 8000
```

### Frontend

```bash
cd meridian/frontend
npm install
npm run dev
```

Open http://localhost:5173/showcase — the agent demo calls `http://localhost:8000`.

## Primary live surface

The Summit chalk talk uses **Device Showcase** as the primary surface. The root route redirects to `/showcase` so attendees and presenters land in the right experience automatically.

| Surface | Route | Audience | Visual direction |
| ------- | ----- | -------- | ---------------- |
| **Device Showcase** | `/showcase`, `/device-showcase` | 300-level chalk talk live demo | Full concierge workspace — chat, phase selector, trace, traveler memory, RLS proof, trip cards. |

The legacy overview remains available at `/pro` for local builder walkthroughs, and the cinematic stage remains available at `/demo-stage` for kiosk use. Neither is the primary participant URL.

### Run

Open two terminals.

**Terminal 1 — backend (FastAPI on :8000)**

```bash
cd meridian
python -m venv venv               # one time
source venv/bin/activate
pip install -r requirements.txt   # one time

cp .env.example .env              # one time — fill in Aurora cluster/secret ARN + region
python scripts/init_aurora_schema.py   # one time — provisions tables + extensions
python scripts/seed_data.py            # one time — 30 curated packages + demo traveler

uvicorn backend.main:app --reload --port 8000
```

Health check: `curl http://localhost:8000/health` → `{"status":"healthy", ...}`.

**Terminal 2 — frontend (Vite on :5173)**

```bash
cd meridian/frontend
npm install        # one time
npm run dev        # http://localhost:5173/showcase
npm run build      # tsc && vite build — gates on TypeScript + production bundle
```

Device Showcase requires the backend and Aurora. Memory facts, traces, and trip results come from live API calls — there are no offline fixture fallbacks.

### Device Showcase — open `/showcase`

```text
http://localhost:5173/showcase
```

Use this surface for the Summit chalk talk. It starts at Phase 1 so you can walk the audience through Query → Tool → Intent → Trust → Durable Workflow with one visible chat, trace, proof badge, and memory rail.

#### Switching phases

The showcase starts at Phase 1 so the audience can climb the ladder live. To change phases:

- Click any phase pill in the showcase top bar.
- Or call the bridge programmatically from elsewhere: `openConcierge({ phase: 5, focus: true })`.

The shared `AgentBridge` (`src/context/AgentBridge.tsx`) holds `phase` as React state, so any section that calls `useAgentBridge()` re-renders when the phase changes.

#### Data sources

| Source | Live endpoint |
| ------ | ------------- |
| Memory facts | `GET /api/memory/{traveler_id}` |
| Traveler profile | `GET /api/memory/{traveler_id}` |
| Trip catalog | `GET /api/products` |
| Trace spans | `POST /api/chat` → `ChatResponse.activities` |
| Device Showcase | `POST /api/chat` from `/showcase` |
| Demo Stage scenarios | `POST /api/chat` (prompts in `src/stage/data/stageScenarios.ts`) |

MCP tool catalog labels in the System section are static reference copy; latencies are inferred from live trace activities when available.

The lightweight named adapters (`chatResponseToMessages`, `activityTraceToSpans`, `memoryResponseToFacts`, `packagesResponseToTripCards`) live in `src/lib/traceAdapter.ts`. The heavier preamble-synthesis logic lives in `src/utils/traceTelemetry.ts`.

### Demo Stage — open `/demo-stage`

```bash
npm run dev
# Presenter mode (keyboard: Space=play/pause, ←/→=step, R=replay, B=builder view)
open http://localhost:5173/demo-stage
# Kiosk mode (auto-loops 3 scenarios, hides controls, respects reduced motion)
open http://localhost:5173/demo-stage?kiosk=1
# Start in builder view (more technical row labels)
open http://localhost:5173/demo-stage?view=builder
```

See `meridian/frontend/src/stage/DemoStage.tsx` for the full keyboard map and scenario list.

## Five phases

| Phase | UI label | What it does |
| ----- | -------- | ------------ |
| **1** | SQL Agent | Direct SQL filters on `trip_packages` via RDS Data API (trip type, operator, price) |
| **2** | MCP Agent | Same catalog queries through `postgres-mcp-server` / MCP `run_query` |
| **3** | Retrieval Agent | Cohere Embed v4 (1024d) hybrid pgvector + `tsvector` candidates, reranked by **Cohere Rerank 3.5**; Retrieval Agent (supervisor) delegates to specialist agents. The Search Agent exposes one `_hybrid_search_tool` that runs the full pipeline |
| **4** | Production Agent | `ProductionAgent` + `MemoryAgent` (`@tool`) on AgentCore Runtime/Gateway/Memory/Identity; Aurora RLS + per-turn audit |
| **5** | Workflow Agent | LangGraph `StateGraph` (classify → search/availability/recall → synthesize) with `PostgresSaver` checkpointing in Aurora |

**Phase 1 example:** `City breaks`, `Beach & Resort`, `Business travel under $1500`

**Phase 3+ example:** `A romantic slow week somewhere with great wine`, `Tokyo trip for two in October`

**Phase 4** uses demo traveler **Alex Morgan** (`trv_meridian_demo`) — profile, preferences, session messages, and `trip_interactions` are loaded from Aurora on every turn.

## Aurora schema (travel-native)

Core tables in `backend/db/schema.sql`:

- **`trip_packages`** — catalog with `embedding vector(1024)` and generated `search_vector`
- **`travelers`**, **`traveler_profiles`**, **`traveler_preferences`** — Phase 4 identity and long-term memory
- **`conversations`**, **`conversation_messages`**, **`trip_interactions`** — session + semantic recall
- **`bookings`**, **`booking_lines`**, **`agent_traces`** — demo booking flow and observability
- **`agent_audit_log`** + **`agent_iam_audit`** view — every Phase 4 turn writes an audit row recording the IAM principal, RLS scope, and rows returned (see `examples/rls_for_agents.sql`)

Seed data: `scripts/travel_catalog.py` → `scripts/seed_data.py` (30 packages + demo traveler).

## API

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/api/chat` | Chat by phase (`phase`: 1–5). Phase 4 accepts `customer_id`, `conversation_id`. Phase 5 routes through the LangGraph workflow |
| `GET` | `/api/memory/{traveler_id}` | Traveler profile + preference facts |
| `GET` | `/api/packages` | Trip catalog (native shape) |
| `GET` | `/api/products` | Legacy product-shaped catalog for the UI |
| `POST` | `/api/chat/order` | Demo booking |
| `GET` | `/health` | Health check |

Activity traces are returned inline on each `POST /api/chat` response (no separate WebSocket stream).

## Tech stack

| Layer | Technology |
| ----- | ---------- |
| Frontend | React 18, Vite, Tailwind, TypeScript |
| Backend | FastAPI, Python 3.11+ |
| Agents | **Strands Agents** (`strands-agents`) — supervisor delegation, `@tool` memory |
| Orchestration | **LangGraph** `StateGraph` with `PostgresSaver` checkpoints (Phase 5) |
| Database | Aurora PostgreSQL 17, RDS Data API, pgvector HNSW, Row-Level Security |
| Embeddings & rerank | **Cohere Embed v4** (`cohere.embed-v4:0`, 1024d) + **Cohere Rerank 3.5** (`us.cohere.rerank-v3-5:0`) on Bedrock |
| LLM | **Claude Sonnet 4.6** on Amazon Bedrock (`global.anthropic.claude-sonnet-4-6`), fallback Sonnet 4.6 → Haiku 4.5 → Opus 4.8 |
| MCP | `awslabs.postgres-mcp-server` (generic SQL) **and** custom `meridian-concierge` FastMCP server (domain tools); plus `meridian-memory` (`backend/mcp/memory_server.py`) |
| Memory & Identity | **Bedrock AgentCore Memory** + **AgentCore Identity** (Phase 4) |

Orchestration is **Strands** for Phases 3 and 4 (LLM-driven tool routing) and **LangGraph** for Phase 5 (explicit StateGraph with checkpointed state).

## Project structure

```
meridian/
├── backend/
│   ├── main.py
│   ├── routers/          # chat, packages, memory
│   ├── agents/
│   │   ├── sql_01/          # Direct RDS filters
│   │   ├── mcp_02/          # MCP agent
│   │   ├── retrieval_03/    # Supervisor + search/package/booking specialists
│   │   ├── production_04/   # ProductionAgent + MemoryAgent
│   │   └── orchestration_05/ # LangGraph StateGraph workflow
│   ├── agentcore/        # Bedrock AgentCore Memory + Identity adapters
│   ├── memory/           # Aurora memory store
│   ├── db/               # RDS client, embeddings, schema.sql
│   └── mcp/              # postgres MCP client + custom memory MCP server
├── frontend/src/
│   ├── sections/         # Hero, Products, HowItWorks, Agent, Vision
│   └── components/       # TravelerPersona, TraceSpan, …
├── scripts/
│   ├── init_aurora_schema.py
│   ├── seed_data.py
│   └── travel_catalog.py
└── tests/
```

## Configuration

Key environment variables (see `.env.example`):

- `EMBEDDING_MODEL=cohere.embed-v4:0`
- `EMBEDDING_DIMENSION=1024`
- `AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`, `AURORA_DATABASE`
- `AGENTCORE_*` — Phase 4 requires deployed Runtime, Gateway, and Memory (sync via `scripts/sync_agentcore_env.py`)
- `AGENTCORE_MEMORY_ID`, `AGENTCORE_REGION` — opt-in to Bedrock AgentCore Memory
- `AGENTCORE_WORKLOAD_IDENTITY`, `AGENTCORE_RESOURCE_PROVIDER` — opt-in to AgentCore Identity
- `LANGGRAPH_CHECKPOINT_DSN` — Phase 5 uses `PostgresSaver` when set, otherwise `MemorySaver`

## Documentation

All docs live in [`docs/`](docs/):

| Doc | Purpose |
| --- | ------- |
| [PRESENTER_GUIDE.md](docs/PRESENTER_GUIDE.md) | The single presenter guide — narration script (what to say, per phase) + code reference (files, snippets, env knobs, FAQ) + dry-run checklist |
| [OPERATIONS.md](docs/OPERATIONS.md) | Deploy AgentCore (day-before) + kiosk/booth runbook (day-of) + learnings & gotchas |
| [STRUCTURE.md](docs/STRUCTURE.md) | Repository layout — what code is live vs reference-only |
