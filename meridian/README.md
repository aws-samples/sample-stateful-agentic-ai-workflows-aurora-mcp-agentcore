# Meridian — Plan. Fly. Land.

> Agentic travel concierge built on Aurora PostgreSQL, MCP, Strands Agents, Bedrock AgentCore, and LangGraph.

Meridian is a working travel concierge and L300 chalk-talk application for
**SQL → MCP → Retrieval → Production → Workflow**. The main walkthrough follows
**Concierge → Capability ladder → Recovery desk → System evidence**.
The fifth view, **Solution briefing**, explains prepared data, architecture,
policy, and recovery through compact diagrams and expandable detail.
Domain-data operations use the RDS Data API. LangGraph persists workflow
checkpoints in Aurora through `AuroraDataApiSaver` or a pooled
`AsyncPostgresSaver`; AgentCore Memory supplies conversation context.

> **Statefulness lives in durable stores, not database connections.**

The primary demo surface is:

```text
http://127.0.0.1:5176/showcase
```

The root route redirects to `/showcase`.

![Meridian Concierge with five views, destination photography, sample trip prices, and the authorized traveler's saved preferences and budget](docs/meridian-showcase.png)

Captured September 12, 2026, from the local app in fullscreen presentation mode,
with catalog and traveler data read from Aurora. The screenshots illustrate
sample inventory and the interface; they do not establish a new booking,
policy decision, or recovery execution.

## Prerequisites

- Python 3.13 (the version CI builds and tests against)
- Node.js 22.12+ recommended (CI uses Node 22); Node 20.19+ is also supported
- AWS credentials with Amazon Bedrock and RDS Data API access
- Aurora PostgreSQL with pgvector and the RDS Data API enabled
- Access to the configured Bedrock models: the default is `global.anthropic.claude-sonnet-5`, with Cohere Embed v4 and Cohere Rerank 3.5 for retrieval
- A configured AgentCore Runtime, Gateway, Policy engine, and Memory for Phase 4, Phase 5 holds, and every clicked hold; see [Operations](docs/OPERATIONS.md#part-1--deploy-agentcore-day-before)

## Quick Start

### Backend

Use the existing prepared Aurora database. For a new database, complete
[data preparation](#prepare-a-new-demo-database) before starting the app.

```bash
cd meridian
python -m venv venv
source venv/bin/activate
python -m pip install --require-hashes -r requirements.txt

[ -f .env ] || cp .env.example .env
# Fill in AURORA_CLUSTER_ARN, AURORA_SECRET_ARN, AURORA_DATABASE, and AWS region.

LANGGRAPH_CHECKPOINT_DSN= LANGGRAPH_AUTO_CHECKPOINT_DSN=false \
LANGGRAPH_CHECKPOINT_DATA_API=true LANGGRAPH_CHECKPOINT_REQUIRED=true \
LANGGRAPH_CHECKPOINT_INIT_ON_STARTUP=true \
uvicorn backend.main:app --host 127.0.0.1 --port 8013
```

Health check:

```bash
curl --fail http://127.0.0.1:8013/health
```

Expected result: `{"status":"healthy", ...}`.

For the Data API recovery demonstration, also verify
`checkpoint_backend: "AuroraDataApiSaver"` and `checkpoint_durable: true`.
The command explicitly selects Data API checkpointing. A separately configured
checkpoint DSN can select `AsyncPostgresSaver` over an existing private connection.
`LANGGRAPH_CHECKPOINT_REQUIRED=true` prevents startup from silently falling
back to in-process checkpoints when the durable store is unavailable.

Keep Aurora private and certificate verification enabled. The HTTPS Data API
path requires no public PostgreSQL ingress or security-group change.
Health reports process configuration; also confirm catalog and traveler reads
succeed and the app shows **Meridian live**. **Reconnect** retries those reads.
Readiness checks allow up to 45 seconds; periodic polling waits for an active
check to finish.
After renewing an expired AWS session, restart the backend if its clients still
use the expired session.

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
VITE_API_ORIGIN=http://127.0.0.1:8013 npm run dev -- --host 127.0.0.1 --port 5176 --strictPort
```

Open:

```text
http://127.0.0.1:5176/showcase
```

If a port is occupied, choose a free port and update `VITE_API_ORIGIN` to match
the backend. The showcase requires the backend and Aurora. Memory facts, trace
spans, RLS proof, and trip results come from API calls. Solution briefing is an
implementation guide; System evidence reads the selected journey's records.

### Prepare a new demo database

Run this only against a new or disposable demo database. Initialization
rebuilds the base schema; seeding creates sample data and grants the current
AWS workload access to Alex.

```bash
# From meridian/, with the virtual environment and AWS configuration ready:
python scripts/init_aurora_schema.py
python scripts/apply_migrations.py
python scripts/seed_data.py
```

For an existing database, preserve its journeys and bookings. Review and apply
tracked upgrades with `python scripts/apply_migrations.py`; do not reinitialize
or reseed it as a connectivity check. Existing databases that predate traveler
bindings may need `python scripts/bind_current_identity.py`. The backend,
Runtime, and Gateway Lambda have separate workload grants; follow
[Operations](docs/OPERATIONS.md) for the deployed roles.

For the pause/resume demonstration, set
`LANGGRAPH_DEMO_INTERRUPT_AFTER=search` before starting the backend. Use the
[L300 runbook](DEMO_SCRIPT.md) for the exact sequence and expected evidence.

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
`--skip-frontend` to reuse `frontend/dist`.

The address and credentials are deployment-specific and are not committed.
The publisher's machine can open the site with the helper below. Prepare access
before screen sharing; the helper copies the password to the clipboard.

```bash
python scripts/published.py          # address, user, status; password copied
python scripts/published.py --open   # also open it in the browser
```

The App Runner instance role is a workload like the gateway Lambda, so it needs
its own grant before it can set a traveler scope. Run this once after the roles
stack exists, or every Phase 4 and Phase 5 request on the published site fails
with `aws_iam subject is not authorized for traveler`:

```bash
python scripts/bind_web_backend_role.py
```

A confirmed booking consumes catalog capacity. Use dedicated rehearsal inventory.
For deliberate cleanup, first inspect what the release script would change:

```bash
python scripts/release_demo_bookings.py --dry-run
```

Release only the intended rehearsal records; cleanup is not a startup step.
See [Operations](docs/OPERATIONS.md#publish-behind-cloudfront) for publication.
A Git push does not deploy the hosted app or AgentCore resources. Tear down with
`cd infra && npx cdk destroy MeridianWeb`, delete the App Runner service
`meridian-web`, then `npx cdk destroy MeridianWebBackend MeridianWebRoles`.
The container starts through `backend/launch.py`, which opens the port before
the application loads; see `docs/AGENTCORE_LEARNINGS.md` for why.

## Demo Surfaces

| Surface | Route | Use |
| ------- | ----- | --- |
| **Concierge** | `/showcase?view=concierge` | Personalized discovery, conversation, trip details, saved trips, and traveler brief |
| **Capability ladder** | `/showcase?view=ladder` | Five phases, boundary queries, architecture disclosure, and live evidence |
| **Recovery desk** | `/showcase?view=recovery` | Canceled-trip scenario, checkpointed shortlist, resume, package-hold receipt, and the handoff that carries the held package back to the concierge for the traveler's confirmation |
| **System evidence** | `/showcase?view=proof` | Readback of the selected journey's checkpoints, execution leases, authorization, and holds |
| **Solution briefing** | `/showcase?view=briefing` | Prepared-data flow, architecture, five phases, Cedar policies, recovery failure windows, and implementation detail |
| **Demo Stage** | `/demo-stage`, `/stage` | Kiosk loop and presenter playback surface |

`/showcase` opens Concierge; `/device-showcase` remains an alias. The selected
view and journey stay in the URL so refresh can restore the saved workflow.

### Screenshots

These captures use the reviewed local application, the light theme, and a
1600 × 1000 fullscreen viewport on September 12, 2026. No API fixtures or
generated mockups were used. The preparation image captures the actual
Prepared data section; the Recovery desk image shows its initial state before
starting a new workflow.

<details>
<summary>Solution briefing: architecture and boundaries</summary>

![Solution briefing with the shared Runtime and workflow paths through Gateway policy, Lambda targets, and Aurora](docs/meridian-solution-briefing.png)

The architecture uses seven original SVG service icons from the July 31, 2026
AWS architecture deck across its nine AWS nodes. The [asset provenance](frontend/public/brand/aws-2026-07-31/README.md)
records the source slides and files. The diagram preserves their official
colors and proportions in both themes.

</details>

<details>
<summary>Prepared data: source records, preparation, and stores</summary>

![The prepared-data section maps packages, descriptions, traveler facts, and tool rules to Aurora and AgentCore stores, followed by the retrieval sequence](docs/meridian-data-preparation.png)

</details>

<details>
<summary>Recovery desk: the next action and its hold policy</summary>

![Recovery desk before a run, with the traveler-reported disruption, the 15-minute courtesy-hold explanation, and the Start recovery action](docs/meridian-recovery.png)

</details>

### Presenting on a shared screen

The windowed **Presenter controls** bar contains **Preview audience layout**,
**Projector readability** and **Present fullscreen**. Preview
widens the workspace while leaving the preparation controls available. Projector
readability increases type size and secondary-text contrast in both themes.

Select **Present fullscreen** before sharing. The entire preparation bar,
and the service sidebar disappear. The
Meridian brand, surface navigation, conversation, and evidence remain available.
Press **Esc** to restore the windowed layout and your preparation choices.
Controls are visible on a shared windowed screen, so stop sharing first.

## Five-Phase Demo Ladder

| Phase | Capability | What the audience should see |
| ----- | ---------- | ---------------------------- |
| **1 · SQL** | Query | Direct Aurora rows returned through RDS Data API filters |
| **2 · MCP** | Tool | Aurora access through MCP plus custom domain tools such as package comparison, FX conversion, and seasonal pricing |
| **3 · Retrieval** | Intent | Hybrid pgvector + full-text candidates reranked by Cohere, with specialist-agent routing |
| **4 · Production** | Trust | AgentCore Runtime discovers four Gateway tools; Cedar decides whether the target may run; traveler grants and RLS scope the data; hold and booking receipts establish the business result |
| **5 · Workflow** | Durable Workflow | Aurora checkpoint, process restart, same-thread resume, and preserved hold identity and expiry |

### Where state lives

| State | Durable store | Access path |
| --- | --- | --- |
| Traveler profile, preferences, conversation history, and audit | Aurora PostgreSQL | RDS Data API |
| Managed session and semantic context across turns, when configured | Bedrock AgentCore Memory | AgentCore APIs |
| LangGraph execution position and pending writes | Aurora PostgreSQL | `AuroraDataApiSaver` over RDS Data API, or `AsyncPostgresSaver` over pooled psycopg |
| Journey binding, worker leases, and hold-request identities | Aurora PostgreSQL | Scoped RDS Data API transactions |
| Clicked holds in any phase and automatic Phase 5 holds | Aurora PostgreSQL | AgentCore Gateway tool, Cedar policy, then the `MeridianHolds` Lambda in one scoped Data API transaction |
| Booking confirmation | Aurora PostgreSQL | AgentCore Gateway tool, Cedar policy, then the `MeridianHolds` Lambda turning the held booking row into `confirmed`; catalog inventory only, no supplier, no payment |

MCP defines the tool contract. Gateway Policy supplies authorization for the
governed actions. A Data API
transaction keeps RLS role and traveler scope together for one unit of work; it
is not long-lived workflow state.

Phase 3's Booking Agent only calculates price estimates. Neither its tool
registry nor the reference SQL agent exposes a booking writer. Model-selected
write actions at the retrieval supervisor are refused and directed to the
governed confirmation flow.

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

Select **Continue at recovery desk**, then **Resume and request hold** to resume
the saved workflow and request its 15-minute hold. Booking confirmation is a
separate traveler action. **Take it back to Alex** carries a matching persisted
receipt into Concierge, preserving the recorded duration, party, unit price,
and total even if the catalog changes.

Refresh restores the saved thread, shortlist, traveler count, and resume action.
System evidence distinguishes observed records, expired leases, completed holds,
and unavailable evidence. Failed refreshes label retained observations; selecting
a different journey clears the old evidence. Full checkpoint readback has a
60-second limit, with bounded concurrent blob reads to reduce service round trips.

Use [DEMO_SCRIPT.md](DEMO_SCRIPT.md#4-recovery-spend-time-at-the-failure-windows)
to distinguish failure before a write, after the business commit but before
checkpointing, and after the hold checkpoint. The hard-kill script exercises
the last window; it does not inject a lost Gateway response. A checkpoint and
a business write are separate transactions, so this is retry-safe business
behavior, not exactly-once execution. See the dated
[release review](docs/RELEASE_REVIEW.md) for checks performed and remaining rehearsal.

### Rehearse recovery failures

From `meridian/`, use the existing configured Aurora database and Gateway.
These scripts create their own journey, checkpoint, and hold records, then
remove them. They do not reset the catalog or release other bookings.

```bash
source venv/bin/activate
export LANGGRAPH_CHECKPOINT_DSN=
export LANGGRAPH_AUTO_CHECKPOINT_DSN=false
export LANGGRAPH_CHECKPOINT_DATA_API=true
export LANGGRAPH_CHECKPOINT_REQUIRED=true
python scripts/kill_and_resume_demo.py
python scripts/lost_response_demo.py
```

The first script kills a worker after the hold checkpoint. The second discards
a real Gateway hold acknowledgement at the worker and injects a timeout before
the hold node can checkpoint it. Aurora and Gateway calls remain real; the
response loss is deliberately simulated.

Expected results: a replacement worker resumes the saved intent, one hold
remains, and its request ID, booking ID, and original expiry are unchanged.
The lost-response script also verifies Cedar denies unconfirmed and over-budget
calls using that same request identity. Unknown Gateway outcomes fail the node
and leave it resumable; they do not finish the graph as an unheld plan.

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
│   ├── app/MeridianConcierge/            # The Phase 4 agent: main.py, turn_trace.py, hold_execution.py, prompts.py
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
They also add `bookings.confirmed_at` and the two `SECURITY DEFINER` functions
the governed writes run through, `create_courtesy_hold` and `confirm_booking`.
Both re-check the traveler scope and the agent type inside the transaction, and
both replay rather than repeat: a retried hold returns the original booking and
a retried confirmation the original confirmation. Apply these migrations on both
fresh and existing databases.

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

The HTTP access boundary binds each request to a traveler before workload
authorization runs. Local loopback development and the hosted sample use a
shared demo principal; they do not authenticate Alex as a human user.
Traveler-scoped operations and Gateway actions use these controls:

1. AgentCore Identity or AWS STS authenticates the workload.
2. Aurora `traveler_identity_bindings` authorizes that subject for the requested traveler. Missing grants fail before the RLS scope is set.
3. Aurora RLS filters rows to the authorized traveler under the least-privilege `meridian_app` role.
4. AgentCore Gateway serves the agent's tools over MCP with SigV4, and its Cedar policy engine (`MeridianGovernance`, ENFORCE mode) decides every tool call on the arguments before any Lambda runs. Reads are permitted; a courtesy hold is permitted only when the traveler confirmed it, for at most 12 hours and 6 travelers, within the traveler's saved budget ceiling. A booking confirmation is permitted only when the traveler confirmed it and its total is within the same ceiling. Nothing else permits either write, so every other call is denied by default.
5. The `MeridianHolds` Lambda is itself a workload: its execution role holds its own grant in `traveler_identity_bindings`, sets the traveler scope, steps down to `meridian_app`, and calls the `create_courtesy_hold` or `confirm_booking` SQL function, so a retried tool call replays the same booking or the same confirmation. Both the Phase 4 concierge and the Phase 5 workflow place their holds through this one tool; nothing in the application writes a hold directly. The workflow passes its checkpointed request id, booking id and execution id, so the Lambda verifies the worker's lease inside the write transaction and a restarted worker replays the same booking with its original expiry.

The runtime pins the traveler id, the confirmation flag, the budget ceiling and
the journey reference onto every hold and booking call from the request the
backend authorized. The model proposes; it cannot confirm on the traveler's
behalf or move the ceiling. The ceiling itself is the traveler's saved
per-traveler cap multiplied by the party, and the travel brief and the
confirmation dialog show that same cap and party total, so what the traveler
reads is the basis Cedar judged. Both ALLOW and DENY decisions are written to `traveler_access_audit`;
completed turns link the authorization subject to the RLS scope in
`agent_iam_audit`. The showcase RLS tab proves the chain live by allowing Alex
and denying the same workload access to the decoy traveler, and the trace panel
shows each Cedar decision as the gateway returned it.

Selecting a ladder phase cannot bypass policy for a clicked hold. Missing
AgentCore configuration fails closed. IAM or target authorization failures are
reported at their actual boundary rather than being labeled as Cedar denials.

This is workload authorization. In a shared hosted application, authenticate
the end user separately and bind the verified user subject, such as a Cognito
`sub`, to the traveler instead of treating one workload as all users. The
sample does not authenticate Alex as a human user.

### Cedar and Dogwood

Cedar policies are configured in `meridian_agentcore/agentcore/agentcore.json`.
[Dogwood temporal policy](docs/DOGWOOD_POLICY_ASSESSMENT.md) is an assessed
extension, not enabled or deployed. The candidate requires a recent lookup of
the same package before a hold, persisted policy-session identity, explicit
caller boundaries, and replacement of any broader permit that would still
allow the call. Aurora remains responsible for capacity and idempotent writes.

## API

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/api/chat` | Chat by phase (`phase`: 1–5); Phase 5 carries the conversation, traveler count, and resume request into LangGraph |
| `GET` | `/api/memory/{traveler_id}` | Traveler profile and preference facts |
| `GET` | `/api/packages` | Trip catalog in native schema shape |
| `GET` | `/api/products` | Product-shaped catalog for UI compatibility |
| `POST` | `/api/chat/order` | Clicked courtesy hold in every phase: Runtime → Gateway/Cedar → `MeridianHolds` Lambda. The phase never selects a direct SQL fallback; `order` is null when the hold is refused |
| `POST` | `/api/chat/book` | Confirm a held trip. The backend reads the booking total under RLS so the policy judges what Aurora holds, then the runtime asks the gateway and the `MeridianHolds` Lambda flips the row from `held` to `confirmed`. Catalog inventory only: no supplier, no payment. `order` is null when the confirmation was refused |
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
| `MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS` | Whole-trip ceiling the Cedar policies compare against when the traveler has saved no budget fact. A saved fact is read per traveler and multiplied by the party instead. Default: `400000` |
| `LANGGRAPH_CHECKPOINT_DATA_API` | Opt into `AuroraDataApiSaver`; used when no checkpoint DSN resolves |
| `LANGGRAPH_CHECKPOINT_DSN` or discrete `LANGGRAPH_CHECKPOINT_*` connection settings | Select `AsyncPostgresSaver` over a bounded PostgreSQL pool |
| `LANGGRAPH_AUTO_CHECKPOINT_DSN` | Allow a DSN derived from discrete connection settings; set false for the explicit Data API startup above |
| `LANGGRAPH_CHECKPOINT_REQUIRED` | Fail closed when no durable checkpoint backend is available |
| `LANGGRAPH_CHECKPOINT_INIT_ON_STARTUP` | Initialize and probe the configured saver during application startup |
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
| Database | Aurora PostgreSQL, RDS Data API, pgvector HNSW, identity bindings, Row-Level Security |
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

```bash
cd meridian/infra
npm ci
npm test
```

The hosted infrastructure tests check the Recovery desk's Gateway permission
and reject invalid or unbounded Gateway endpoints.

Install `ruff` and `pip-audit` for the CI quality checks; run
`ruff check backend scripts tests` from `meridian/`. Non-database tests disable
live checkpoint configuration and reject unmocked network connections; the
command above additionally prevents loading the demo's `.env` configuration.
Run `python -m pytest -m database` separately for live Aurora checks. They load
`.env` and require a disposable, migrated Aurora test database with a seeded
catalog and AWS access; they write checkpoints, journeys, and holds. The root
[README](../README.md#validation) also lists the AgentCore CDK checks.

## Documentation

| Doc | Purpose |
| --- | ------- |
| [DEMO_SCRIPT.md](DEMO_SCRIPT.md) | L300 chalk talk, secure preflight, failure windows, and evidence |
| [docs/PRESENTER_GUIDE.md](docs/PRESENTER_GUIDE.md) | Narration, code references, FAQ, and dry-run checklist |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | AgentCore deployment and day-of operating guide |
| [docs/STATEFUL_ARCHITECTURE.md](docs/STATEFUL_ARCHITECTURE.md) | Source of truth for state, transport, and slide messaging |
| [docs/DOGWOOD_POLICY_ASSESSMENT.md](docs/DOGWOOD_POLICY_ASSESSMENT.md) | Temporal-policy proposal and required rehearsal; not enabled |
| [docs/RELEASE_REVIEW.md](docs/RELEASE_REVIEW.md) | Dated validation, live proof, and deployment boundaries |
| [STRUCTURE.md](STRUCTURE.md) | Live code vs reference-only layout |
