# Meridian — Plan. Fly. Land.

> Agentic travel concierge built on Aurora PostgreSQL, MCP, Strands Agents, Bedrock AgentCore, and LangGraph.

Meridian is a live workshop demo for the five-phase progression **Query → Tool → Intent → Trust → Durable Workflow**. The technical phases are **SQL → MCP → Retrieval → Production → Workflow**. The showcase UI calls a real FastAPI backend backed by Aurora PostgreSQL through the RDS Data API and pgvector.

The primary demo surface is:

```text
http://localhost:5173/showcase
```

The root route redirects to `/showcase`.

## Prerequisites

- Python 3.11+
- Node.js 20.19+ or 22.12+
- AWS credentials with Amazon Bedrock and RDS Data API access
- Aurora PostgreSQL 17 with pgvector enabled, or a cluster created through `scripts/create_cluster.sh`
- Bedrock model access for `global.anthropic.claude-sonnet-4-6`

## Quick Start

### Backend

```bash
cd meridian
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in AURORA_CLUSTER_ARN, AURORA_SECRET_ARN, AURORA_DATABASE, and AWS region.

python scripts/init_aurora_schema.py
python scripts/seed_data.py

uvicorn backend.main:app --reload --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
```

Expected result: `{"status":"healthy", ...}`.

### Frontend

```bash
cd meridian/frontend
npm install
npm run dev
```

Open:

```text
http://localhost:5173/showcase
```

The showcase requires the backend and Aurora. Memory facts, trace spans, RLS proof, and trip results come from live API calls.

## Demo Surfaces

| Surface | Route | Use |
| ------- | ----- | --- |
| **Device Showcase** | `/showcase`, `/device-showcase` | Primary AWS Summit chalk-talk experience: chat, phase selector, trace, traveler memory, RLS proof, and trip cards |
| **Demo Stage** | `/demo-stage` | Kiosk or presenter playback mode |
| **Legacy Pro** | `/pro` | Local builder walkthrough and older overview surface |

Useful Demo Stage variants:

```text
http://localhost:5173/demo-stage
http://localhost:5173/demo-stage?kiosk=1
http://localhost:5173/demo-stage?view=builder
```

## Five-Phase Demo Ladder

| Phase | Capability | What the audience should see |
| ----- | ---------- | ---------------------------- |
| **1 · SQL** | Query | Direct Aurora rows returned through RDS Data API filters |
| **2 · MCP** | Tool | Aurora access through MCP plus custom domain tools such as package comparison, FX conversion, and seasonal pricing |
| **3 · Retrieval** | Intent | Hybrid pgvector + full-text candidates reranked by Cohere, with specialist-agent routing |
| **4 · Production** | Trust | AgentCore, traveler memory, Aurora RLS, and auditable per-turn scope |
| **5 · Workflow** | Durable Workflow | Explicit LangGraph routing with Aurora-backed checkpoints |

## Prompt Ladder

Each phase has two safe wins and one prompt that naturally motivates the next phase.

| Phase | Known-good prompts | Tee-up prompt |
| ----- | ------------------ | ------------- |
| SQL | `City breaks under $2000`; `Beach & Resort trips under $2500` | `Compare our top trips and show prices in EUR` → needs custom MCP tools |
| MCP | `Compare our top trips and show prices in EUR`; `What is the cheapest month to visit Tokyo?` | `A romantic slow week somewhere with great wine` → needs intent retrieval |
| Retrieval | `A romantic slow week somewhere with great wine`; `Check availability for the Tuscany Wine & Wellness week` | `What did we discuss last time? Pick up where we left off.` → needs durable memory |
| Production | `Tokyo culture trip for two — boutique stays, local food, walkable neighborhoods`; `What did we discuss last time? Pick up where we left off.` | `Plan our October Tokyo trip — find open dates, pick a Marriott property, and hold a Kyoto side trip` → needs explicit workflow |
| Workflow | `What dates are open for the Amalfi Coast Villa Week?`; `What did we discuss last time? Pick up where we left off.`; `Plan a Kyoto cultural trip end-to-end: find matching trips, then check which November departures are open.` | Finale: all three are successful branches |

## Architecture

```text
meridian/
├── backend/
│   ├── main.py
│   ├── routers/              # chat, packages, memory, diagnostics
│   ├── agents/
│   │   ├── sql_01/           # Phase 1 direct SQL
│   │   ├── mcp_02/           # Phase 2 MCP agent
│   │   ├── retrieval_03/     # Phase 3 supervisor + specialists
│   │   ├── production_04/    # Phase 4 AgentCore + memory
│   │   └── orchestration_05/ # Phase 5 LangGraph workflow
│   ├── agentcore/            # AgentCore Runtime, Gateway, Memory, Identity adapters
│   ├── db/                   # RDS client, embeddings, schema
│   ├── memory/               # Aurora-backed memory store
│   └── mcp/                  # MCP clients and custom memory server
├── frontend/src/
│   ├── showcase/             # Primary /showcase experience
│   ├── stage/                # /demo-stage playback surface
│   ├── sections/             # Legacy /pro sections
│   └── lib/                  # Shared adapters and run config
├── examples/                 # RLS and setup SQL
├── scripts/                  # Cluster, schema, seed, and sync helpers
└── tests/
```

## Aurora Schema

Core tables live in `backend/db/schema.sql`:

- `trip_packages` — catalog with `embedding vector(1024)` and generated `search_vector`
- `travelers`, `traveler_profiles`, `traveler_preferences` — identity and long-term memory
- `conversations`, `conversation_messages`, `trip_interactions` — session history and semantic recall
- `bookings`, `booking_lines`, `agent_traces` — demo booking and observability
- `agent_audit_log` and `agent_iam_audit` — Phase 4 IAM, RLS scope, and rows-returned audit trail

Seed data is generated by `scripts/travel_catalog.py` and loaded by `scripts/seed_data.py`.

## API

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/api/chat` | Chat by phase (`phase`: 1–5). Phase 4 accepts `customer_id` and `conversation_id`; Phase 5 routes through LangGraph |
| `GET` | `/api/memory/{traveler_id}` | Traveler profile and preference facts |
| `GET` | `/api/packages` | Trip catalog in native schema shape |
| `GET` | `/api/products` | Product-shaped catalog for UI compatibility |
| `POST` | `/api/chat/order` | Demo booking flow |
| `GET` | `/health` | Backend health and run configuration |

Trace spans are returned inline on each `POST /api/chat` response as `ChatResponse.activities`.

## Configuration

Key environment variables are documented in `.env.example`.

| Variable | Purpose |
| -------- | ------- |
| `BEDROCK_MODEL_ID` | LLM used by all Strands agents. Default: `global.anthropic.claude-sonnet-4-6` |
| `BEDROCK_REGION` / `AWS_DEFAULT_REGION` | Bedrock and AWS SDK region |
| `EMBEDDING_MODEL` | Default: `cohere.embed-v4:0` |
| `EMBEDDING_DIMENSION` | Default: `1024` |
| `AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`, `AURORA_DATABASE` | RDS Data API connection |
| `RLS_APP_ROLE` | Least-privilege role used for scoped Aurora RLS sessions |
| `AGENTCORE_*` | Phase 4 Runtime, Gateway, Memory, and Identity configuration |
| `LANGGRAPH_CHECKPOINT_DSN` | Enables Phase 5 `PostgresSaver`; otherwise falls back to `MemorySaver` |

## Tech Stack

| Layer | Technology |
| ----- | ---------- |
| Frontend | React 18, Vite, TypeScript |
| Backend | FastAPI, Python 3.11+ |
| Agents | Strands Agents for Phases 1–4 |
| Workflow | LangGraph `StateGraph` with `PostgresSaver` checkpoints in Phase 5 |
| Database | Aurora PostgreSQL 17, RDS Data API, pgvector HNSW, Row-Level Security |
| Embeddings and rerank | Cohere Embed v4 (`cohere.embed-v4:0`) and Cohere Rerank 3.5 (`us.cohere.rerank-v3-5:0`) on Bedrock |
| LLM | Claude Sonnet 4.6 on Amazon Bedrock (`global.anthropic.claude-sonnet-4-6`) |
| MCP | `awslabs.postgres-mcp-server`, custom `meridian-concierge`, and `meridian-memory` MCP servers |
| Memory and identity | Bedrock AgentCore Memory and AgentCore Identity |

## Validation

```bash
cd meridian/frontend
npm run lint
npm run test:run
npm run build
```

```bash
cd meridian
source venv/bin/activate
python -m pytest
```

## Documentation

| Doc | Purpose |
| --- | ------- |
| [DEMO_SCRIPT.md](DEMO_SCRIPT.md) | Presenter flow and recommended live prompts |
| [docs/PRESENTER_GUIDE.md](docs/PRESENTER_GUIDE.md) | Narration, code references, FAQ, and dry-run checklist |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | AgentCore deployment and day-of operating guide |
| [STRUCTURE.md](STRUCTURE.md) | Live code vs reference-only layout |
