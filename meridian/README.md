# Meridian — Plan. Fly. Land.

> Agentic travel concierge on Aurora PostgreSQL, MCP, and Strands Agents.

Meridian is a workshop demo that climbs a five-phase ladder: SQL → MCP → Retrieval → Memory → Orchestration. The live UI talks to a real FastAPI backend backed by Aurora (RDS Data API + pgvector), not a client-side mock.

## Quick start

### Prerequisites

- Python 3.11+
- Node.js 18+
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

Open http://localhost:5173 — the agent demo calls `http://localhost:8000`.

## Five phases

| Phase | UI label | What it does |
| ----- | -------- | ------------ |
| **1** | SQL | Direct SQL filters on `trip_packages` via RDS Data API (trip type, operator, price) |
| **2** | MCP | Same catalog queries through `postgres-mcp-server` / MCP `run_query` |
| **3** | Retrieval | Cohere Embed v4 (1024d) + hybrid pgvector + `tsvector` search; Strands supervisor delegates to specialist agents |
| **4** | Memory | `ConciergeOrchestrator` + `MemoryAgent` (`@tool`) recall and persist traveler context in Aurora; mirrors session events to Bedrock AgentCore Memory; per-turn audit row + Aurora RLS |
| **5** | Orchestration | LangGraph `StateGraph` (classify → search/availability/recall → synthesize) with `PostgresSaver` checkpointing in Aurora |

**Phase 1 example:** `City breaks`, `Beach & Resort`, `Business travel under $1500`

**Phase 3+ example:** `Romantic week in Europe`, `Tokyo trip for two in October`

**Phase 4** uses demo traveler **Alex & Jordan Chen** (`trv_meridian_demo`) — profile, preferences, session messages, and `trip_interactions` are loaded from Aurora on every turn.

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
| Embeddings | **Cohere Embed v4** on Bedrock (`cohere.embed-v4:0`, 1024 dimensions) |
| LLM | Claude on Amazon Bedrock |
| MCP | `awslabs.postgres-mcp-server` (Phase 2) **and** `meridian-memory` (Phase 4 — `backend/mcp/memory_server.py`) |
| Memory & Identity | **Bedrock AgentCore Memory** + **AgentCore Identity** (Phase 4) |

Orchestration is **Strands** for Phases 3 and 4 (LLM-driven tool routing) and **LangGraph** for Phase 5 (explicit StateGraph with checkpointed state).

## Project structure

```
meridian/
├── backend/
│   ├── main.py
│   ├── routers/          # chat, packages, memory
│   ├── agents/
│   │   ├── phase1/       # Direct RDS filters
│   │   ├── phase2/       # MCP agent
│   │   ├── phase3/       # Supervisor + search/product/order specialists
│   │   ├── phase4/       # ConciergeOrchestrator + MemoryAgent
│   │   └── phase5/       # LangGraph StateGraph workflow
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
- `STRANDS_ORCHESTRATION` — `full` (LLM-routed) or `direct` (procedural fallback) for Phases 3/4
- `AGENTCORE_MEMORY_ID`, `AGENTCORE_REGION` — opt-in to Bedrock AgentCore Memory
- `AGENTCORE_WORKLOAD_IDENTITY`, `AGENTCORE_RESOURCE_PROVIDER` — opt-in to AgentCore Identity
- `LANGGRAPH_CHECKPOINT_DSN` — Phase 5 uses `PostgresSaver` when set, otherwise `MemorySaver`

## Demo script

See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for a 60-minute workshop walkthrough. See [STRUCTURE.md](STRUCTURE.md) for what code is live vs reference-only.
