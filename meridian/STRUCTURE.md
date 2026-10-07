# Meridian code layout

Paths are relative to `meridian/`.

## Request path

```text
frontend/src/main.tsx
  → /showcase, /device-showcase → showcase/MeridianDeviceShowcase.tsx
  → api/client.ts → backend origin (VITE_API_ORIGIN, local default 127.0.0.1:8013)

backend/main.py
  → routers/chat.py        # Phases 1-5: inline search, Phase 4 concierge, Phase 5 LangGraph, holds and confirmation
  → routers/products.py    # GET /api/packages[/{id}] and /api/products[/{id}]
  → routers/memory.py      # GET /api/memory/{traveler_id}, PATCH and DELETE on its facts, authorized and RLS-scoped
  → routers/journeys.py    # authorized journey list and persisted evidence
  → routers/diagnostics.py # POST /api/diagnostics/rls-probe and /session-receipt: allow and deny checks, durable row counts
```

The five views are Concierge, Capability ladder, Recovery desk, System evidence
and Solution briefing. The ladder exposes the five phases. Concierge has its
own conversation, which uses the Phase 4 runtime.

The SQL and MCP phases run `sql_search` and `mcp_search` in `chat.py`; the
Strands agents in `backend/agents/phase_01_sql/` and `backend/agents/phase_02_mcp/` are
reference implementations of the same steps. Retrieval, Production and Workflow
import their agent modules at runtime:

- `backend/agents/phase_03_retrieval/`: the Strands supervisor delegates catalog search, availability and read-only price estimates
- `backend/agents/phase_04_production/concierge.py`: identity, traveler grant, and RLS read and write around the managed runtime; `process_hold()` for a hold from the UI
- `backend/agents/phase_04_production/memory_agent.py`: `@tool` recall and persist methods
- `backend/agents/budget.py`: the budget ceiling Cedar compares against, shared by Phases 4 and 5
- `backend/agents/phase_05_workflow/`: `graph.py` builds the Strands graph, `nodes.py` holds its steps, `runner.py` claims the lease and runs or resumes it, and `snapshot_storage.py` saves snapshots to Aurora `workflow_snapshots`
- `backend/agentcore/runtime.py`, `backend/agentcore/identity.py`: AgentCore adapters (streaming runtime client, identity envelope)
- `meridian_agentcore/app/MeridianConcierge/`: the Phase 4 agent on AgentCore Runtime: `main.py` (tool loop, memory session, streamed events), `turn_trace.py` (spans and the pinned hold and booking arguments), `hold_execution.py` (confirmed holds and confirmations run by the platform), `prompts.py`, `gateway_auth.py`
- `meridian_agentcore/agentcore/gateway_targets/meridian_holds/`: the `MeridianHolds` gateway Lambda (`get_package_details`, `create_courtesy_hold`, `confirm_booking`)
- `meridian_agentcore/agentcore/gateway_targets/semantic_trip_search/`: the `semantic_trip_search` Lambda and its tool schema
- `meridian_agentcore/agentcore/agentcore.template.json`: runtime, memory, gateway targets and the `MeridianGovernance` Cedar policy engine, with placeholders that `scripts/render_agentcore_config.py` fills for your account

None of the catalog agents can write a booking; every hold and confirmation
uses the governed Gateway path.

> `chat.py` carries the hybrid lexical and semantic candidate query a second
> time for the direct Phase 3 and Phase 5 paths. Keep it in step with
> `SearchAgent.hybrid_search`; `tests/test_hybrid_lexical_arm.py` guards the
> agent copy.

## Directory map

### Backend

| Path | Role |
| --- | --- |
| `backend/routers/` | FastAPI routes |
| `backend/agents/phase_01_sql/`, `backend/agents/phase_02_mcp/` | Reference Strands agents for SQL and MCP |
| `backend/agents/phase_03_retrieval/` | Retrieval supervisor and read-only specialists |
| `backend/agents/phase_04_production/` | Concierge and traveler memory agents |
| `backend/agents/phase_05_workflow/` | LangGraph workflow: checkpoints, worker leases, hold intent and governed hold |
| `backend/agentcore/` | AgentCore Runtime client, Gateway checks, Identity, and the CLI config loader |
| `backend/db/` | RDS Data API client with grant and RLS-scoped sessions, `AuroraDataApiSaver`, embeddings, journey store, `schema.sql` |
| `backend/mcp/` | Phase 2 client for `awslabs.postgres-mcp-server`, and the custom `meridian-concierge` and `meridian-memory` MCP servers with their clients |
| `backend/memory/` | Aurora traveler memory store and audit writer |
| `backend/authorization.py` | Workload-to-traveler authorization types |
| `backend/http_auth.py` | HTTP principal and traveler binding |
| `backend/demo_prompts.py` | The five-phase prompt ladder |
| `backend/catalog_compat.py` | Maps `trip_packages` rows to the frontend's `Product` shape |
| `backend/launch.py` | Container entry point that opens the port before the app loads |

### Frontend

| Path | Role |
| --- | --- |
| `frontend/src/showcase/` | The `/showcase` surface: views, components, hooks and adapters |
| `frontend/src/api/` | API client with bounded waits |
| `frontend/src/components/` | Shared UI (brand mark, route skeleton) |
| `frontend/public/` | Brand marks, service icons and catalog images |
| `frontend/e2e/` | Playwright accessibility and behavior tests |

### AgentCore and infrastructure

| Path | Role |
| --- | --- |
| `meridian_agentcore/` | AgentCore CLI project: runtime code, gateway Lambda targets, configuration templates, CDK app |
| `infra/bin/meridian-aurora.ts` | CDK app for an encrypted Aurora PostgreSQL cluster with the Data API |
| `infra/bin/meridian-web.ts` | CDK app for the hosted web app: S3 and CloudFront site, App Runner image and roles |
| `Dockerfile` | Backend container with the locked PostgreSQL MCP server |

### Scripts

| Path | Role |
| --- | --- |
| `scripts/init_aurora_schema.py`, `scripts/apply_migrations.py`, `scripts/migrations/` | Create the schema on a new database and apply tracked migrations |
| `scripts/travel_catalog.py`, `scripts/seed_data.py` | Seed data, and the loader that embeds it and grants the current workload access to Jordan |
| `scripts/bind_current_identity.py` | Grant the current IAM or AgentCore workload access to Jordan on an existing database |
| `scripts/render_agentcore_config.py` | Render the AgentCore configuration templates for your account and deployed IDs |
| `scripts/sync_agentcore_env.py` | Copy deployed AgentCore IDs from the CLI state into `.env` |
| `scripts/publish_gateway_parameters.py` | Publish the Aurora settings the holds Lambda reads from SSM |
| `scripts/bind_gateway_workload.py`, `scripts/bind_web_backend_role.py` | Grant the holds Lambda role and the App Runner instance role access to Jordan |
| `scripts/verify_agentcore.py`, `scripts/smoke_gateway_tools.py`, `scripts/smoke_production_turn.py` | Check the deployed platform, the gateway tools and the governed hold path end to end |
| `scripts/kill_and_resume_proof.py`, `scripts/lost_response_proof.py` | Recovery exercises: kill a worker after its hold, or discard a committed hold response |
| `scripts/provision_preflight.py` | Read-only checks before provisioning Aurora in an account |
| `scripts/publish.py`, `scripts/published.py` | Publish the hosted web app to an existing App Runner service; read back its local release record |
| `scripts/validate_demo.py`, `scripts/release_demo_bookings.py` | End-to-end check of a running deployment; release demo bookings |
| `scripts/warm_demo.py` | Warm health, catalog, traveler profile and one read-only turn per phase before presenting |
| `scripts/install_catalog_images.py` | Install catalog artwork by package ID |

### Other

| Path | Role |
| --- | --- |
| `examples/rls_app_role.sql`, `examples/rls_for_agents.sql` | The restricted RLS role, RLS policies and the authorization audit view |
| `examples/memory_mcp_demo.py` | Stand-alone client for the custom memory MCP server |
| `tests/` | Pytest suite |
| `examples/langgraph/` | Maintained LangGraph example: `AuroraDataApiSaver`, a pause and resume graph, and its tests, including the checkpointer conformance suite |
| `docs/` | Architecture, operations, deployment runbook, code walkthrough and design notes |

## Naming

The API and UI keep some e-commerce names from an earlier version of the
sample:

- `Product` and `product_id` in TypeScript and `/api/products` are trips from `trip_packages`
- `fetchProducts` and `fetchProduct` in `frontend/src/api/client.ts` call `/api/products`,
  which `backend/routers/products.py` serves as an alias of `/api/packages`
- The `Product` type's `brand`, `price` and `category` fields carry a trip's operator,
  price per person and trip type

The trip display components already use trip names (`TripCard`, `TripRow`).

The agent modules are described in `backend/agents/README.md`.
