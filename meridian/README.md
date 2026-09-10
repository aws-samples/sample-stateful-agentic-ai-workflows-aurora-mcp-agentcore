# Meridian — Plan. Fly. Land.

> Agentic travel concierge built on Aurora PostgreSQL, MCP, Strands Agents, Bedrock AgentCore, and LangGraph.

Meridian is a live workshop demo for **SQL → MCP → Retrieval → Production → Workflow**. The four views take the audience from the traveler experience to the implementation: **Concierge → Capability ladder → Recovery desk → System evidence**. Domain-data operations use the RDS Data API. LangGraph persists workflow checkpoints in Aurora through `AuroraDataApiSaver` or a pooled `AsyncPostgresSaver`; AgentCore Memory adds managed context when configured.

> **Statefulness lives in durable stores, not database connections.**

The primary demo surface is:

```text
http://localhost:5173/showcase
```

The root route redirects to `/showcase`.

![The current Meridian Concierge with destination photography, trip recommendations, traveler context, and four-view navigation](docs/meridian-showcase.png)

Captured from the running app in fullscreen presentation mode. Catalog prices
and availability are sample package inventory, not airline reservations or tickets.

## Prerequisites

- Python 3.13 (the version CI builds and tests against)
- Node.js 22.12+ recommended (CI uses Node 22); Node 20.19+ is also supported
- AWS credentials with Amazon Bedrock and RDS Data API access
- Aurora PostgreSQL 18+ with pgvector enabled, or a cluster created through `scripts/create_cluster.sh`
- Bedrock model access for `global.anthropic.claude-sonnet-5`

## Quick Start

### Backend

```bash
cd meridian
python -m venv venv
source venv/bin/activate
python -m pip install --require-hashes -r requirements.txt

cp .env.example .env
# Fill in AURORA_CLUSTER_ARN, AURORA_SECRET_ARN, AURORA_DATABASE, and AWS region.

# Fresh or disposable database only: recreates the schema.
python scripts/init_aurora_schema.py
python scripts/apply_migrations.py
python scripts/seed_data.py  # also binds the current AWS workload to Alex

# In .env, enable durable checkpoints for the recovery demonstration:
# LANGGRAPH_CHECKPOINT_DATA_API=true
# LANGGRAPH_CHECKPOINT_REQUIRED=true

uvicorn backend.main:app --reload --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
```

Expected result: `{"status":"healthy", ...}`.

For the Data API recovery demonstration, also verify
`checkpoint_backend: "AuroraDataApiSaver"` and `checkpoint_durable: true`.
An explicit or resolved checkpoint DSN selects `AsyncPostgresSaver` instead.
`LANGGRAPH_CHECKPOINT_REQUIRED=true` prevents startup from silently falling
back to in-process checkpoints when the durable store is unavailable.

For an existing database, do not run `init_aurora_schema.py`: it rebuilds the
base schema. Apply the tracked, non-destructive upgrades instead:

```bash
python scripts/apply_migrations.py
```

`requirements.in` is the human-maintained dependency specification.
`requirements.txt` is the hash-pinned lock generated with:

```bash
PIP_CONFIG_FILE=/dev/null pip-compile requirements.in \
  --output-file requirements.txt --generate-hashes --strip-extras \
  --index-url https://pypi.org/simple
```

### Frontend

```bash
cd meridian/frontend
npm ci
npm run dev
```

Open:

```text
http://localhost:5173/showcase
```

The showcase requires the backend and Aurora. Memory facts, trace spans, RLS proof, and trip results come from live API calls.

### Publish behind CloudFront

`infra/` is a CDK app that publishes the same application to a password-protected
CloudFront URL: the Vite build in a private S3 bucket, the backend as a container
on AWS App Runner, one distribution in front of both, and a CloudFront Function
that enforces basic auth and injects the backend bearer token on `/api/*`. The
credentials live in a CloudFront KeyValueStore and the token in Secrets Manager;
neither is in code or in a template.

```bash
cd meridian
finch vm start                      # or Docker; the backend image is built locally
python scripts/publish.py           # writes the secret, builds, deploys, fills the KeyValueStore
```

The script prints the URL and writes the password and token to
`.local/published.json` (gitignored). Re-run it to redeploy; pass
`--skip-frontend` to reuse `frontend/dist`. Tear down with
`cd infra && npx cdk destroy MeridianWeb MeridianWebBackend MeridianWebRoles`.

For an existing database created before identity binding was added:

```bash
python scripts/bind_current_identity.py
```

## Demo Surfaces

| Surface | Route | Use |
| ------- | ----- | --- |
| **Concierge** | `/showcase?view=concierge` | Personalized discovery, conversation, trip details, saved trips, and traveler brief |
| **Capability ladder** | `/showcase?view=ladder` | Five phases, boundary queries, architecture disclosure, and live evidence |
| **Recovery desk** | `/showcase?view=recovery` | Canceled-trip scenario, checkpointed shortlist, resume, and package-hold receipt |
| **System evidence** | `/showcase?view=proof` | Readback of the selected journey's checkpoints, execution leases, authorization, and holds |
| **Demo Stage** | `/demo-stage`, `/stage` | Kiosk loop and presenter playback surface |

`/showcase` opens Concierge; `/device-showcase` remains an alias. The selected
view and journey stay in the URL so refresh can restore the saved workflow.

### Presenting on a shared screen

The windowed **Presenter controls** bar contains **Preview audience layout**,
**Projector readability**, **Room check**, and **Present fullscreen**. Preview
widens the workspace while leaving the preparation controls available. Projector
readability increases type size and secondary-text contrast in both themes.

Select **Present fullscreen** before sharing. The entire preparation bar,
including an open room-check panel, and the service sidebar disappear. The
Meridian brand, surface navigation, conversation, and evidence remain available.
Press **Esc** to restore the windowed layout and your preparation choices.
Controls are visible on a shared windowed screen, so stop sharing first.

## Five-Phase Demo Ladder

| Phase | Capability | What the audience should see |
| ----- | ---------- | ---------------------------- |
| **1 · SQL** | Query | Direct Aurora rows returned through RDS Data API filters |
| **2 · MCP** | Tool | Aurora access through MCP plus custom domain tools such as package comparison, FX conversion, and seasonal pricing |
| **3 · Retrieval** | Intent | Hybrid pgvector + full-text candidates reranked by Cohere, with specialist-agent routing |
| **4 · Production** | Trust | The agent in AgentCore Runtime discovers its tools from AgentCore Gateway, Cedar policy decides every call in ENFORCE mode, recalled preferences arrive under workload-to-traveler grants and Aurora RLS, and a one-click courtesy hold is either permitted or refused by policy before any code runs |
| **5 · Workflow** | Durable Workflow | Aurora checkpoint, process restart, same-thread resume, and preserved hold identity and expiry |

### Where state lives

| State | Durable store | Access path |
| --- | --- | --- |
| Traveler profile, preferences, conversation history, and audit | Aurora PostgreSQL | RDS Data API |
| Managed session and semantic context across turns, when configured | Bedrock AgentCore Memory | AgentCore APIs |
| LangGraph execution position and pending writes | Aurora PostgreSQL | `AuroraDataApiSaver` over RDS Data API, or `AsyncPostgresSaver` over pooled psycopg |
| Journey binding, worker leases, and hold-request identities | Aurora PostgreSQL | Scoped RDS Data API transactions |
| Phase 4 courtesy hold placed by the agent | Aurora PostgreSQL | AgentCore Gateway tool, Cedar policy, then the `MeridianHolds` Lambda in one scoped Data API transaction |

MCP defines the governed tool contract, not the database transport. A Data API
transaction keeps RLS role and traveler scope together for one unit of work; it
is not long-lived workflow state.

## Prompt Ladder

The visible pills pair a working query with a boundary that motivates the next
phase. **Continue in…** carries the question forward. These are boundaries of
the configured demo phases, not inherent limitations of SQL or MCP. Additional
working queries are in [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

| Phase | Works here | Next capability or proof |
| ----- | ------------------ | ------------- |
| SQL | `Show me city trips under $2,000 per traveler.` | `Compare three trip types and convert each price to euros.` → MCP |
| MCP | `Compare three trip types and convert each price to euros.` | `Find a quiet, romantic wine-country retreat with a private villa.` → Retrieval |
| Retrieval | `Find a quiet, romantic wine-country retreat with a private villa.` | `Recall my Tokyo plan and saved preferences: home airport, food needs, and budget.` → Production |
| Production | `Find Tokyo trips that fit my saved preferences.` | `My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options.` → Workflow |
| Workflow | `My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options.` | `Resume workflow from checkpoint` after the pause |

### What recovery proves

The canceled itinerary is a preview and is not valid for boarding. Saving a
shortlist does not hold inventory. Once the workflow creates a package hold,
its receipt displays the creation time, original 15-minute expiry, and remaining
time. Restart verification checks that the same request produces one booking
with the same expiry, and that a replacement execution successfully resumes the
saved checkpoint. A second worker attempt alone is not proof of success.

Refresh restores the saved thread, shortlist, traveler count, and resume action.
System evidence distinguishes observed records, expired leases, completed holds,
and unavailable evidence. See [AUDIT_FIXES.md](docs/AUDIT_FIXES.md) for validation
and the remaining live-rehearsal limits.

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
│   ├── agentcore/            # AgentCore Runtime (streaming), Gateway (checks), Identity adapters
│   ├── db/                   # RDS client, embeddings, schema
│   ├── memory/               # Aurora-backed memory store
│   └── mcp/                  # MCP clients and custom memory server
├── meridian_agentcore/       # AgentCore CLI project: agentcore.json is the source of truth
│   ├── app/MeridianConcierge/            # The Phase 4 agent: main.py, turn_trace.py, prompts.py
│   └── agentcore/gateway_targets/        # semantic_trip_search and meridian_holds Lambda targets
├── frontend/src/
│   ├── showcase/             # Primary /showcase experience
│   ├── stage/                # Presenter playback surface
│   └── lib/                  # Shared adapters and run config
├── frontend/public/
│   ├── travel/catalog/       # Commissioned artwork, one image per package
│   └── brand/                # Aurora, AgentCore, and Meridian marks
├── examples/                 # RLS and setup SQL
├── scripts/                  # Cluster, schema, seed, and sync helpers
└── tests/
```

## Aurora Schema

Core tables live in `backend/db/schema.sql`:

- `trip_packages` — catalog with `embedding vector(1024)` and generated `search_vector`
- `travelers`, `traveler_profiles`, `traveler_preferences` — traveler profile and long-term memory
- `traveler_identity_bindings`, `traveler_access_audit` — workload-to-traveler grants and ALLOW/DENY evidence
- `conversations`, `conversation_messages`, `trip_interactions` — session history and semantic recall
- `bookings`, `booking_lines`, `agent_traces` — demo booking and observability
- `agent_audit_log` and `agent_iam_audit` — Phase 4 IAM, RLS scope, and rows-returned audit trail

Tracked migrations in `scripts/migrations/` add the journey, execution, hold
request, and checkpoint tables, including `journeys`, `journey_executions`,
`hold_requests`, `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes`.
Apply these migrations on both fresh and existing databases.

Seed data is generated by `scripts/travel_catalog.py` and loaded by `scripts/seed_data.py`.
`seed_data.py` also writes the Cohere Embed v4 vectors, so re-running it after a
catalog change keeps semantic search in step.

Every package ships its own artwork under `frontend/public/travel/catalog/`, named
after its package id. `travel_catalog.py` derives each `image_url` from the package
id, so the path is never maintained by hand. Nothing is fetched from a stock photo
CDN, so the catalog reads as one photographic system and a demo does not depend on
venue Wi-Fi. To replace the images, drop new files in a folder and run:

```bash
python scripts/install_catalog_images.py ~/Downloads/meridian-art --backup ~/art-backup
```

A file is matched when its name starts with a package id, so `WEL-002.png` and
`WEL-002 - Amalfi Coast Villa Week.png` both work. The script crops to the hero's
aspect, resizes, sharpens anything it had to enlarge, and reports any package still
missing artwork.

## Governance Boundary

Stateful reads and writes use five independent controls:

1. AgentCore Identity or AWS STS authenticates the workload.
2. Aurora `traveler_identity_bindings` authorizes that subject for the requested traveler. Missing grants fail before the RLS scope is set.
3. Aurora RLS filters rows to the authorized traveler under the least-privilege `meridian_app` role.
4. AgentCore Gateway serves the agent's tools over MCP with SigV4, and its Cedar policy engine (`MeridianGovernance`, ENFORCE mode) decides every tool call on the arguments before any Lambda runs. Reads are permitted; a courtesy hold is permitted only when the traveler confirmed it, for at most 12 hours and 6 travelers, within the traveler's saved budget ceiling. Nothing else permits the hold, so every other call is denied by default.
5. The `MeridianHolds` Lambda is itself a workload: its execution role holds its own grant in `traveler_identity_bindings`, sets the traveler scope, steps down to `meridian_app`, and calls the `create_courtesy_hold` SQL function, so a retried tool call replays the same booking. Both the Phase 4 concierge and the Phase 5 workflow place their holds through this one tool; nothing in the application writes a hold directly. The workflow passes its checkpointed request id, booking id and execution id, so the Lambda verifies the worker's lease inside the write transaction and a restarted worker replays the same booking with its original expiry.

The runtime pins the traveler id, the confirmation flag, the budget ceiling and
the journey reference onto every hold call from the request the backend
authorized. The model proposes the hold; it cannot confirm it or move the
ceiling. Both ALLOW and DENY decisions are written to `traveler_access_audit`;
completed turns link the authorization subject to the RLS scope in
`agent_iam_audit`. The showcase RLS tab proves the chain live by allowing Alex
and denying the same workload access to the decoy traveler, and the trace panel
shows each Cedar decision as the gateway returned it.

This is workload authorization. In a shared hosted application, authenticate
the end user separately and bind the verified user subject, such as a Cognito
`sub`, to the traveler instead of treating one workload as all users. The
sample does not authenticate Alex as a human user.

## API

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/api/chat` | Chat by phase (`phase`: 1–5); Phase 5 carries the conversation, traveler count, and resume request into LangGraph |
| `GET` | `/api/memory/{traveler_id}` | Traveler profile and preference facts |
| `GET` | `/api/packages` | Trip catalog in native schema shape |
| `GET` | `/api/products` | Product-shaped catalog for UI compatibility |
| `POST` | `/api/chat/order` | Courtesy hold. In Phase 4 the click is the confirmation: the runtime asks the gateway, Cedar decides, and the `MeridianHolds` Lambda writes; `order` is null when the hold was refused and the activities carry the decision |
| `GET` | `/api/journeys` | List the authorized traveler's journeys |
| `GET` | `/api/journeys/{journey_id}` | Read the saved workflow, checkpoint, executions, authorization, and hold evidence |
| `GET` | `/health`, `/api/health` | Backend health, checkpoint backend, and actual durability |

Trace spans are returned inline on each `POST /api/chat` response as `ChatResponse.activities`.

## Configuration

Key environment variables are documented in `.env.example`.

| Variable | Purpose |
| -------- | ------- |
| `BEDROCK_MODEL_ID` | LLM used by all Strands agents. Default: `global.anthropic.claude-sonnet-5` |
| `BEDROCK_REGION` / `AWS_DEFAULT_REGION` | Bedrock and AWS SDK region |
| `EMBEDDING_MODEL` | Default: `cohere.embed-v4:0` |
| `EMBEDDING_DIMENSION` | Default: `1024` |
| `AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`, `AURORA_DATABASE` | RDS Data API connection |
| `RLS_APP_ROLE` | Least-privilege role used for scoped Aurora RLS sessions |
| `AGENTCORE_*` | Phase 4 Runtime, Gateway, Memory, and Identity configuration, synced from the CLI deployment state |
| `MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS` | Hold budget ceiling the Cedar policy compares against when the traveler has no saved budget fact. Default: `400000` |
| `LANGGRAPH_CHECKPOINT_DATA_API` | Opt into `AuroraDataApiSaver`; used when no checkpoint DSN resolves |
| `LANGGRAPH_CHECKPOINT_DSN` or discrete `LANGGRAPH_CHECKPOINT_*` connection settings | Select `AsyncPostgresSaver` over a bounded PostgreSQL pool |
| `LANGGRAPH_CHECKPOINT_REQUIRED` | Fail closed when no durable checkpoint backend is available |
| `LANGGRAPH_DEMO_INTERRUPT_AFTER` | Pause after a named node for the restart/resume proof |

## Tech Stack

| Layer | Technology |
| ----- | ---------- |
| Frontend | React 18, Vite, TypeScript |
| Backend | FastAPI, Python 3.13 |
| Agents | Strands Agents for Phases 1–4; the Phase 4 agent runs inside Bedrock AgentCore Runtime with tools from AgentCore Gateway over MCP |
| Governance | Bedrock AgentCore Policy (Cedar, ENFORCE) on the gateway, plus the identity chain and Aurora RLS below |
| Observability | AWS Distro for OpenTelemetry on the runtime; spans and logs land in the runtime's CloudWatch log group with the trace id shown in the UI |
| Workflow | LangGraph `StateGraph`, Aurora checkpoints, worker leases, and courtesy holds placed through the governed gateway tool |
| Database | Aurora PostgreSQL 18+, RDS Data API, pgvector HNSW, identity bindings, Row-Level Security |
| Embeddings and rerank | Cohere Embed v4 (`cohere.embed-v4:0`) and Cohere Rerank 3.5 (`us.cohere.rerank-v3-5:0`) on Bedrock |
| LLM | Claude Sonnet 5 on Amazon Bedrock (`global.anthropic.claude-sonnet-5`) |
| MCP | `awslabs.postgres-mcp-server`, custom `meridian-concierge`, and `meridian-memory` MCP servers |
| Memory and identity | Bedrock AgentCore Memory, AgentCore Identity, AWS IAM workload authorization |

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
python -m pip install --require-hashes -r requirements.txt
PYTHON_DOTENV_DISABLED=1 python -m pytest -m "not database"
python -m pip_audit -r requirements.txt
```

Install `ruff` and `pip-audit` for the CI quality checks; run
`ruff check backend scripts tests` from `meridian/`. CI runs the offline tests;
the command above also prevents loading the demo's local `.env` configuration.
Run `python -m pytest -m database` separately for live Aurora checks. They load
`.env` and require a disposable, migrated Aurora test database with a seeded
catalog and AWS access; they write checkpoints, journeys, and holds. The root
[README](../README.md#validation) also lists the AgentCore CDK checks.

## Documentation

| Doc | Purpose |
| --- | ------- |
| [DEMO_SCRIPT.md](DEMO_SCRIPT.md) | Presenter flow and recommended live prompts |
| [docs/PRESENTER_GUIDE.md](docs/PRESENTER_GUIDE.md) | Narration, code references, FAQ, and dry-run checklist |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | AgentCore deployment and day-of operating guide |
| [docs/STATEFUL_ARCHITECTURE.md](docs/STATEFUL_ARCHITECTURE.md) | Source of truth for state, transport, and slide messaging |
| [STRUCTURE.md](STRUCTURE.md) | Live code vs reference-only layout |
