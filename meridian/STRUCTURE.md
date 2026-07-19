# Meridian repository layout

## What runs in production (the demo)

```
frontend/src/App.tsx
  → sections: Hero, Products (trips grid), HowItWorks, Vision, Agent (live chat)
  → api/client.ts → backend :8000

backend/main.py
  → routers/chat.py      # Phases 1–5 (inline search + Phase 4 concierge + Phase 5 LangGraph)
  → routers/products.py  # GET /api/packages (+ legacy /api/products)
  → routers/memory.py    # GET /api/memory/{traveler_id} (authorized + RLS-scoped)
```

Production and Orchestration modes import agent / workflow modules at runtime:

- `backend/agents/production_04/concierge.py` — Strands concierge + Aurora memory + AgentCore mirror
- `backend/agents/production_04/memory_agent.py` — `@tool` recall/persist methods
- `backend/agents/orchestration_05/workflow.py` — LangGraph `StateGraph` + `PostgresSaver`/`MemorySaver`
- `backend/agentcore/memory.py`, `backend/agentcore/identity.py` — Bedrock AgentCore adapters

SQL/MCP/Retrieval modes execute inside `chat.py` (`sql_search`, `mcp_search`, `retrieval_search`). The matching files under `backend/agents/sql_01`, `backend/agents/mcp_02`, and `backend/agents/retrieval_03` are the imported mode implementations.

## Directory map

| Path | Role |
| ---- | ---- |
| `backend/authorization.py` | Shared workload-to-traveler authorization types |
| `backend/db/` | RDS Data API (identity grant + RLS-scoped session helpers), embeddings, `schema.sql` |
| `backend/mcp/` | Phase 2 client → public `awslabs.postgres-mcp-server`; **custom `memory_server.py`** + its stdio client |
| `backend/memory/` | Aurora traveler memory store + audit writer |
| `backend/agentcore/` | Bedrock AgentCore Runtime, Gateway, Memory, Identity — real API calls only |
| `backend/agents/production_04/` | Live concierge + memory agents |
| `backend/agents/orchestration_05/` | LangGraph `OrchestrationAgent` (StateGraph + PostgresSaver) |
| `backend/agents/sql_01,mcp_02,retrieval_03/` | SQL, MCP, and Retrieval mode agents |
| `backend/routers/` | FastAPI routes |
| `examples/rls_for_agents.sql` | Aurora RLS policies + authorization/RLS audit view |
| `examples/memory_mcp_demo.py` | Stand-alone smoke test for the custom memory MCP server |
| `scripts/sync_agentcore_env.py` | Sync `agentcore deploy` state → `.env` |
| `backend/catalog_compat.py` | Maps `trip_packages` rows → legacy API `Product` shape |
| `frontend/src/sections/` | Live SPA sections |
| `frontend/src/components/` | Shared UI (nav, trace, persona, thumbs) |
| `scripts/travel_catalog.py` | Trip + traveler seed source |
| `scripts/seed_data.py` | Seeds Aurora and binds the current workload to Alex |
| `scripts/bind_current_identity.py` | Migrates an existing DB and grants the current IAM/AgentCore workload access to Alex |
| `docs/design/` | Static HTML design explorations (not served by the app) |
| `tests/` | Pytest |

## Naming debt (intentional compat)

The travel pivot kept some e-commerce names in the API/UI layer:

- `Product` / `product_id` in TypeScript and `/api/products` — trips from `trip_packages`
- `ProductsSection`, `ProductThumb`, `handleAddToCart` — display trips, not SKUs

A future rename to `Package` / `Trip` would be cosmetic only if the compat layer stays.

## Cleanup history

Removed dead code: duplicate `partner_runtime.py`, unused `ShopWithAI` stack, `mockData`, legacy `lib/aurora_db.py`, `data/products.json`, unused `backend/tools/`, unused WebSocket router, stub `/api/chat/image` endpoint, and the duplicate `agentstride/` tree at the repo root. Design HTML lives in `docs/design/`. Reference agents are documented in `backend/agents/README.md`.
