# Meridian repository layout

## What runs in production (the demo)

```
frontend/src/main.tsx
  → /showcase, /device-showcase → showcase/MeridianDeviceShowcase.tsx
  → /demo-stage, /stage         → stage/DemoStage.tsx
  → api/client.ts → backend :8000

backend/main.py
  → routers/chat.py        # Phases 1–5 (inline search + Phase 4 concierge + Phase 5 LangGraph)
  → routers/products.py    # GET /api/packages (+ legacy /api/products)
  → routers/memory.py      # GET /api/memory/{traveler_id} (authorized + RLS-scoped)
  → routers/diagnostics.py # POST /api/diagnostics/rls-probe (ALLOW/DENY + scoped counts)
```

The showcase is a three-step journey — Discovery (Experience), Capability
ladder (Architecture), Stateful recovery (Proof). The five phase pills and the
chat composer live inside step 2; the landing view has neither.

Production and Orchestration modes import agent / workflow modules at runtime:

- `backend/agents/production_04/concierge.py` — identity, traveler grant, RLS read and write around the managed runtime; `process_hold()` for the one-click hold
- `backend/agents/production_04/budget.py` — the hold budget ceiling the Cedar policy compares against
- `backend/agents/production_04/memory_agent.py` — `@tool` recall/persist methods
- `backend/agents/orchestration_05/workflow.py` — LangGraph `StateGraph` + shared pooled `PostgresSaver`/ephemeral `MemorySaver`
- `backend/agentcore/runtime.py`, `backend/agentcore/identity.py` — Bedrock AgentCore adapters (streaming runtime client, identity envelope)
- `meridian_agentcore/app/MeridianConcierge/` — the Phase 4 agent deployed to AgentCore Runtime: `main.py` (tool loop, memory session, SSE events), `turn_trace.py` (spans and the pinned hold contract), `prompts.py`, `gateway_auth.py`
- `meridian_agentcore/agentcore/gateway_targets/meridian_holds/` — the `MeridianHolds` gateway Lambda (`get_package_details`, `create_courtesy_hold`)
- `meridian_agentcore/agentcore/agentcore.json` — runtime, memory, gateway targets, and the `MeridianGovernance` Cedar policy engine

SQL/MCP/Retrieval modes execute inside `chat.py` (`sql_search`, `mcp_search`, `retrieval_search`). The matching files under `backend/agents/sql_01`, `backend/agents/mcp_02`, and `backend/agents/retrieval_03` are the imported mode implementations.

> `chat.py` carries the hybrid lexical/semantic candidate query a second time
> for the direct Phase 3 and Phase 5 paths. Keep it in step with
> `SearchAgent.hybrid_search` — `tests/test_hybrid_lexical_arm.py` guards the
> agent copy.

## Directory map

| Path | Role |
| ---- | ---- |
| `backend/authorization.py` | Shared workload-to-traveler authorization types |
| `backend/db/` | RDS Data API (identity grant + RLS-scoped session helpers), embeddings, `schema.sql` |
| `backend/mcp/` | Phase 2 client → public `awslabs.postgres-mcp-server`; **custom `memory_server.py`** + its stdio client |
| `backend/memory/` | Aurora traveler memory store + audit writer |
| `backend/agentcore/` | Bedrock AgentCore Runtime (streaming client), Gateway (laptop-side checks), Identity — real API calls only |
| `meridian_agentcore/` | AgentCore CLI project: the deployed agent, the gateway Lambda targets, and `agentcore.json` |
| `scripts/publish_gateway_parameters.py`, `scripts/bind_gateway_workload.py` | Publish the Aurora settings the holds Lambda reads from SSM; grant its execution role access to Alex |
| `scripts/verify_agentcore.py`, `scripts/smoke_gateway_tools.py`, `scripts/smoke_production_turn.py` | Pre-session checks: platform status, gateway tools, and the governed hold path end to end |
| `backend/agents/production_04/` | Live concierge + memory agents |
| `backend/agents/orchestration_05/` | LangGraph `OrchestrationAgent` (StateGraph + pooled PostgresSaver + restart/resume) |
| `backend/agents/sql_01,mcp_02,retrieval_03/` | SQL, MCP, and Retrieval mode agents |
| `backend/routers/` | FastAPI routes |
| `backend/demo_prompts.py` | The five-phase presenter prompt ladder (single source of truth) |
| `examples/rls_for_agents.sql` | Aurora RLS policies + authorization/RLS audit view |
| `examples/memory_mcp_demo.py` | Stand-alone smoke test for the custom memory MCP server |
| `scripts/sync_agentcore_env.py` | Sync `agentcore deploy` state → `.env` |
| `backend/catalog_compat.py` | Maps `trip_packages` rows → legacy API `Product` shape |
| `frontend/src/showcase/` | Primary `/showcase` surface (components, hooks, adapters) |
| `frontend/src/stage/` | Kiosk and presenter playback surface |
| `frontend/src/components/` | Shared UI (brand mark, route skeleton) |
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
