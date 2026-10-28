# Meridian code layout

Paths are relative to `meridian/`.

## Request path

```text
frontend/src/main.tsx
  → /showcase, /device-showcase → showcase/MeridianDeviceShowcase.tsx
  → auth/AuthGate.tsx     # Amazon Cognito sign-in (PKCE) when the build has it; otherwise the app renders ungated
  → api/client.ts → backend origin (VITE_API_ORIGIN, local default 127.0.0.1:8013)

backend/main.py
  → routers/chat.py        # Phases 1-5: inline search, Phase 4 concierge, Phase 5 invokes MeridianWorkflow, holds and confirmation
  → routers/session.py     # GET /api/me: the traveler and authentication method of the verified credential
  → routers/products.py    # GET /api/packages[/{id}] and /api/products[/{id}]
  → routers/memory.py      # GET /api/memory/{traveler_id}, PATCH and DELETE on its facts, authorized and RLS-scoped
  → routers/journeys.py    # authorized journey list, persisted evidence, and POST /{id}/stop-session for the workflow Runtime session
  → routers/diagnostics.py # POST /api/diagnostics/rls-probe and /session-receipt: allow and deny checks, durable row counts
```

The five views are Concierge, Capability ladder, Recovery desk, System evidence
and Solution briefing. The ladder exposes the five phases. Concierge has its
own conversation, which uses the Phase 4 runtime.

The SQL and MCP phases run `sql_search` and `mcp_search` in `chat.py`, in code
and without a model loop: `search_trips` directly, then over the `meridian-concierge`
MCP server. The Strands agents in `backend/agents/phase_01_sql/` and
`backend/agents/phase_02_mcp/` are reference implementations of those patterns.
Retrieval, Production and Workflow import their agent modules at runtime:

- `backend/agents/phase_03_retrieval/`: one Strands agent built from `backend/agents/concierge_definition.py`; Phase 2's tools over one stdio MCP client per worker, `hybrid_trip_search` and `get_package_details` in process
- `backend/agents/concierge_definition.py`: the tool contract, prompt core, traveler pin and `build_agent` shared by Phase 3 and the Concierge Runtime; copied into the Runtime by `scripts/stage_concierge_runtime.py`
- `backend/agents/phase_04_production/concierge.py`: identity, traveler grant, the RLS read, the turn's audit row and the memory write around the managed runtime; `process_hold()` and `process_booking()` for a hold or confirmation from the UI
- `backend/agents/phase_04_production/memory_agent.py`: `@tool` recall and persist methods
- `backend/agents/budget.py`: the budget ceiling Cedar compares against, shared by Phases 4 and 5
- `backend/agents/phase_05_workflow/`: `graph.py` builds the Strands graph, `nodes.py` holds its steps, `runner.py` claims the lease and runs or resumes it, `snapshot_storage.py` saves snapshots to Aurora `workflow_snapshots`, and `runtime_entry.py` is the one event the `MeridianWorkflow` Runtime handles; `governed_tools.py` calls Gateway tools (search) and `governed_hold.py` the hold and release tools, with the caller's token; `run_audit.py` writes one audit row per run
- `backend/agentcore/runtime.py`, `backend/agentcore/workflow_runtime.py`, `backend/agentcore/identity.py`: AgentCore adapters (streaming Concierge client, workflow Runtime client with session stop) and the backend's IAM identity from AWS STS
- `backend/agentcore/auth_mode.py`, `caller_credential.py`, `caller_claims.py`, `runtime_https.py`: the `MERIDIAN_AGENTCORE_AUTH` switch, the per-request caller token, the traveler read from a forwarded token, and the bearer-token Runtime invocation
- `meridian_agentcore/app/MeridianConcierge/`: the Phase 4 agent on AgentCore Runtime: `main.py` (tool loop, memory session, streamed events), `turn_trace.py` (spans and the pinned hold and booking arguments), `hold_execution.py` (confirmed holds and confirmations run by the platform), `tool_errors.py` (ends the turn on a tool setup error), `turn_watchdog.py` (the 35 second idle watchdog, the 15 second keepalive, the 300 second cap and the history repair), `turn_work.py` (the worker threads for Memory, Gateway start and close, and the repair order), `model/load.py` (the Claude Sonnet 5.5 model), `prompts.py`, `gateway_auth.py`, `caller_identity.py`
- `meridian_agentcore/app/MeridianWorkflow/`: the Phase 5 Runtime; its `backend/` directory is a generated copy of the backend modules the workflow imports
- `meridian_agentcore/agentcore/gateway_targets/meridian_holds/`: the `MeridianHolds` gateway Lambda (`get_package_details`, `loyalty_balance`, `create_courtesy_hold`, `confirm_booking`, `release_hold`)
- `meridian_agentcore/agentcore/gateway_targets/meridian_catalog/`: the `MeridianCatalog` Lambda and its generated tool schema
- `meridian_agentcore/agentcore/interceptors/traveler_pin/`: the Gateway request interceptor that pins `travelerId` to the signed-in traveler (attached to the Gateway, never a target)
- `meridian_agentcore/agentcore/agentcore.template.json`: runtime, memory, gateway targets and the `MeridianGovernance` Cedar policy engine, with placeholders that `scripts/render_agentcore_config.py` fills for your account

None of the catalog agents can write a booking; every hold and confirmation
uses the governed Gateway path.

> The hybrid search lives once, in `backend/retrieval/hybrid.py`. Phase 3's
> `hybrid_trip_search` tool and Phase 5's search step both call it, and its vector arm is the
> SQL function `semantic_trip_search`. The `MeridianCatalog` Lambda runs the same statements,
> held equal by `tests/test_catalog_lambda_parity.py`; `tests/test_hybrid_retrieval.py` and
> `tests/test_hybrid_lexical_arm.py` guard the module.

## Directory map

### Backend

| Path | Role |
| --- | --- |
| `backend/routers/` | FastAPI routes |
| `backend/agents/phase_01_sql/`, `backend/agents/phase_02_mcp/` | Reference Strands agents for SQL and MCP |
| `backend/agents/phase_03_retrieval/` | The Phase 3 retrieval agent: its MCP session, in-process tools, trace hooks and turn runner |
| `backend/agents/concierge_definition.py` | The tool contract, prompt core, traveler pin and `build_agent` shared by Phase 3 and the Concierge Runtime; copied into the Runtime by `scripts/stage_concierge_runtime.py` |
| `backend/agents/phase_04_production/` | Concierge and traveler memory agents |
| `backend/agents/phase_05_workflow/` | Strands Graph workflow: snapshots, worker leases, hold intent and governed hold |
| `backend/agentcore/` | AgentCore Runtime client, Gateway checks, the backend's IAM identity, and the CLI config loader |
| `backend/db/` | RDS Data API client with grant and RLS-scoped sessions, embeddings, journey store, `schema.sql` |
| `backend/mcp/` | The custom `meridian-concierge` server (typed `search_trips` and domain tools) that Phase 2 calls, the stand-alone `meridian-memory` server for external MCP hosts, and their clients. The reference MCP agent starts `awslabs.postgres-mcp-server` itself |
| `backend/tools/` | The catalog tools the MCP server, Phase 3 and the `MeridianCatalog` Lambda share: `search_trips`, `hybrid_trip_search`, `get_package_details`, and the tool-name and schema helpers |
| `backend/retrieval/` | The one hybrid search (`hybrid.py`) and the availability check |
| `backend/security/` | Masking of 12-digit account ids: error text before it is stored or shown, identity fields when they leave the backend |
| `backend/bedrock_model.py` | The one factory for the Claude Sonnet 5.5 Strands model: the 16,000-token output cap and `REQUEST_FIELDS` (adaptive thinking, effort medium) |
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
| `frontend/src/auth/` | Sign-in: configuration, PKCE, token client, in-memory session, `AuthGate` and `SignInScreen` |
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
| `scripts/bind_current_identity.py` | Grant the current IAM identity access to Jordan on an existing database |
| `scripts/render_agentcore_config.py` | Render the AgentCore configuration templates for your account and deployed IDs |
| `scripts/sync_agentcore_env.py` | Copy deployed AgentCore IDs from the CLI state into `.env` |
| `scripts/publish_gateway_parameters.py` | Publish the Aurora settings the holds Lambda reads from SSM |
| `scripts/bind_gateway_workload.py`, `scripts/bind_web_backend_role.py` | Grant the holds Lambda role (Jordan Morgan only) and the App Runner instance role (`--traveler`, `--apply`) access to a seeded traveler |
| `scripts/verify_agentcore.py`, `scripts/smoke_gateway_tools.py`, `scripts/smoke_production_turn.py` | Check the deployed platform, the gateway tools and the governed hold path end to end |
| `scripts/run_gateway_harness.py`, `scripts/gateway_harness/` | Create a separate throwaway Gateway, answer the interceptor and Cedar questions with real tokens, print a verdict table and delete everything; dry run by default, live only with `--apply --i-understand-this-creates-aws-resources` |
| `scripts/agentcore_caller.py` | Bind a seeded user's real access token for a smoke script in `jwt` mode; does nothing in `iam` mode |
| `scripts/kill_and_resume_proof.py`, `scripts/lost_response_proof.py`, `scripts/stop_and_resume_proof.py` | Recovery exercises: kill a worker after its hold, discard a committed hold response, or stop the Runtime session and resume |
| `scripts/stage_concierge_runtime.py`, `scripts/generate_catalog_tool_schema.py` | Copy the shared concierge definition into the Concierge Runtime; generate the `MeridianCatalog` tool schema from the tool functions |
| `scripts/provision_workflow_login.py`, `scripts/bind_workflow_runtime.py`, `scripts/stage_workflow_runtime.py` | Create or rotate the `meridian_workflow` login, bind the Runtime's workload (`--traveler`, `--apply`), and stage the Runtime's backend copy |
| `scripts/provision_service_logins.py` | Create or rotate the `meridian_backend`, `meridian_gateway`, `meridian_identity` and `meridian_catalog` logins, their secrets and managed policies |
| `scripts/seed_cognito_users.py`, `scripts/sync_cognito_env.py`, `scripts/cognito_tokens.py` | Create the two sign-in users and their traveler bindings, copy the identity stack outputs into `.env`, and mint real tokens for tests |
| `scripts/release_identity.py`, `scripts/identity_release/` | Operate the coordinated identity release: read every hop back (`check`), deploy the Gateway stages, attach the interceptor, move the Lambdas, save a snapshot and roll back; dry run unless given `--apply --i-understand-this-changes-aws` |
| `scripts/prove_backend_login.py` | Run the real backend on this machine as the `meridian_backend` login and drive it through the phase paths before the cutover |
| `scripts/identity_proof.py`, `scripts/identity_probes/` | Sign in as Jordan and the decoy and send the 19 probes that show the decoy is refused at the backend, the Runtimes, the Gateway and Aurora |
| `scripts/identity_capture/` | Capture the signed-in app for the deck, with tokens held in memory and passed through a pipe |
| `scripts/smoke_workflow_runtime.py` | Ping the deployed `MeridianWorkflow` Runtime without touching any journey or Aurora row |
| `scripts/apply_rls_force_and_decoy.py` | One-shot: enable FORCE row level security on the Phase 4 tables and seed a decoy traveler's preferences |
| `scripts/provision_preflight.py` | Read-only checks before provisioning Aurora in an account |
| `scripts/publish.py`, `scripts/published.py` | Publish the hosted web app to an existing App Runner service; read back its local release record |
| `scripts/validate_demo.py`, `scripts/release_demo_bookings.py` | End-to-end check of a running deployment; release the sample traveler's bookings |
| `scripts/warm_demo.py` | Warm health, catalog, traveler profile and one read-only turn per phase before presenting |
| `scripts/install_catalog_images.py` | Install catalog artwork by package ID |

### Other

| Path | Role |
| --- | --- |
| `examples/rls_app_role.sql`, `examples/rls_for_agents.sql` | The restricted RLS role, RLS policies and the authorization audit view |
| `examples/memory_mcp_demo.py` | Stand-alone client for the custom memory MCP server |
| `tests/` | Pytest suite |
| `examples/langgraph/` | A maintained second implementation of the workflow: a Data API saver, a pause and resume graph, and its tests, including the saver conformance suite in `tests/conformance/`. The application does not import it |
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
