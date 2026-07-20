# Meridian Demo Script

## Build stateful agentic AI workflows with Aurora, MCP, and AgentCore

**Duration:** 60 minutes (≈45 min content + ~15 min distributed Q&A)
**Format:** Live web demo + optional code walkthrough
**Tagline:** Plan. Fly. Land.

---

## The shape of the talk

One product, one Aurora cluster, a capability ladder climbed one rung at a time:

```
Query  →  Tool  →  Intent  →  Trust  →  Durable Workflow
 SQL      MCP     Retrieval  Production   Workflow
```

Each rung earns its place by fixing the exact thing the previous rung could not do.
There are **four failures** that drive the story, and rehearsing them is the whole game:

1. **SQL** can filter rows but cannot own a business operation (compare + FX).
2. **MCP** gives portable tools but cannot infer intent (mood/vibe).
3. **Retrieval** understands intent but has no memory and no row-level trust.
4. **Production** remembers and governs, but plans multi-step work "in its head" — it
   cannot pause, checkpoint, and resume. That is the **flight-disruption** hand-off to
   Workflow.

**Timing budget** (leave the rest as distributed Q&A slack — do not fill it):

| Segment | Target | Running |
| ------- | -----: | ------: |
| Setup — the ladder | 4 min | 4 |
| Phase 1 · SQL | 7 min | 11 |
| Phase 2 · MCP | 7 min | 18 |
| Phase 3 · Retrieval | 8 min | 26 |
| Phase 4 · Production (governance climax) | 12 min | 38 |
| Coda · Durable Workflow (flight disruption) | 6 min | 44 |
| Close | 1 min | 45 |

Rehearse hitting **minute 45**. The 15 minutes you did not spend is what turns a rushed
4.75 into a 5.0 — it is airtime for the questions each phase provokes.

---

## Key files

| File | Purpose |
| ---- | ------- |
| `backend/routers/chat.py` | Live chat for all five phases (`sql_search`, `mcp_search`, `retrieval_*`, `production_search`, `orchestration_workflow`) |
| `backend/db/schema.sql` | Travel-native Aurora schema (`trip_packages`, travelers, memory tables) |
| `backend/db/rds_data_client.py` | RDS Data API client + `scoped_session()` (authorization → RLS) |
| `backend/db/embedding_service.py` | Cohere Embed v4 on Bedrock (1024d) |
| `backend/mcp/mcp_client.py` | postgres-mcp client (Phase 2) |
| `backend/mcp/concierge_server.py` | Custom `meridian-concierge` MCP server (domain tools) |
| `backend/agents/retrieval_03/supervisor.py` | Strands supervisor + specialist agents |
| `backend/agents/production_04/concierge.py` | Production concierge (identity → authz → RLS → memory) |
| `backend/agents/orchestration_05/workflow.py` | LangGraph StateGraph + checkpointer |
| `frontend/src/showcase/` | Live demo surface (`/showcase`) |

---

## Pre-demo setup (~5 minutes before)

**Terminal 1 — backend**

```bash
cd meridian
source venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

**Terminal 2 — frontend**

```bash
cd meridian/frontend
npm run dev
```

**Verify**

- http://localhost:5173/showcase loads the live concierge
- http://localhost:8000/health returns `"status":"healthy"`
- `GET /api/memory/trv_meridian_demo` returns Alex Morgan profile facts
- Phase pills, trace panel, and Alex Morgan context are visible
- **Warm the cluster:** run one Phase 1 query. Aurora Serverless v2 scales from 0.5 ACU;
  the first query after idle can take a few seconds or, if the cluster is mid-maintenance,
  error. Never let the *first* thing the room sees be a cold-start stall.

**If Aurora was reset**

```bash
python scripts/init_aurora_schema.py
python scripts/seed_data.py
```

**Optional — provision AgentCore for Phase 4 (@aws/agentcore CLI, Node-based)**

```bash
npm install -g @aws/agentcore
cd meridian_agentcore/agentcore
agentcore add memory --name meridian_session --strategies SEMANTIC --expiry 30
agentcore add gateway --name meridian-aurora --authorizer-type AWS_IAM
agentcore deploy -y
cd ../.. && python scripts/sync_agentcore_env.py --write
```

Without deployed AgentCore Runtime/Gateway/Memory, Phase 4 surfaces an explicit
"AgentCore platform not configured" message instead of pretending to run Production mode.

**Required for the Phase 5 durability proof — start the checkpoint tunnel first**

The "prove durable state" moment (Phase 5) needs Aurora-backed checkpoints, not
`MemorySaver`. This cannot be stood up mid-talk. Before going live, follow
`docs/OPERATIONS.md` §2 "Start the durable stack": run
`scripts/start_checkpoint_tunnel.sh` in its own terminal (forwards local
`15432` → Aurora `5432`), then launch the backend with
`LANGGRAPH_CHECKPOINT_REQUIRED=true` and `LANGGRAPH_DEMO_INTERRUPT_AFTER=search`.
Confirm `/health` reports `"checkpoint_backend": "PostgresSaver (Aurora · pooled)"`
and `"checkpoint_durable": true`. If it says `MemorySaver`, the resume finale is
not durable — fix it before the room sees Phase 5.

---

## Setup — the ladder (4 min)

**Select:** `Phase 1 · SQL` (start clean).

> "Meridian is an agentic **travel concierge** — not a chatbot bolted onto a search box.
> Same Aurora cluster, same Strands `@tool` pattern, the whole way up. We climb one ladder:
> **Query → Tool → Intent → Trust → Durable Workflow**. Watch each rung fail at exactly one
> thing, and watch the next rung fix it."

Point to the five phase pills (grouped as one ladder):

| Phase | Capability | Mode | Proof point |
| ----- | ---------- | ---- | ----------- |
| 1 | Query | SQL | SQL executed |
| 2 | Tool | MCP | MCP tool invoked |
| 3 | Intent | Retrieval | pgvector + rerank |
| 4 | Trust | Production | Workload grant + RLS |
| 5 | Durable Workflow | Workflow | Checkpoint written |

> "Phases 1–3 build the retrieval stack. Phase 4 makes it trustworthy — it remembers Alex
> Morgan and physically cannot read anyone else's data. Phase 5 makes multi-step work
> survivable: a flight gets cancelled mid-plan, and the workflow picks up where it left off."

---

## Phase 1 · SQL (7 min)

**Select:** `Phase 1 · SQL`

> "The smallest agent that talks to Aurora: one Strands `Agent`, a few `@tool` methods,
> direct RDS Data API. Fast and debuggable — it owns exact filters, nothing more."

**Works:**

| Query | What happens |
| ----- | ------------ |
| `Show me city trips under $2,000 per traveler.` | Trip-type + per-traveler price filter on `trip_packages` |
| `Show me beach and resort trips under $2,500 per traveler.` | Same filter shape |

Point to the trace: RDS connection → parameterized filter SQL → package rows.

**First failure (rehearse this):**

| Query | Why it fails |
| ----- | ------------ |
| `Compare three trips from different categories and show their prices in euros.` | SQL can return rows, but **comparison + per-package currency conversion is a business operation**, not a `WHERE` clause. |

> **Pause.** "The user didn't ask a bad SQL question. They asked for an *operation* SQL
> doesn't own. Who owns reusable tools? That's Phase 2."

### Optional code walkthrough
- `backend/routers/chat.py` → `sql_search`
- `backend/search_utils.py` → `parse_search_query`, `execute_keyword_search`
- IDE: `backend/agents/sql_01/agent.py` — `Agent(tools=[...])`, five `@tool` methods

---

## Phase 2 · MCP (7 min)

**Select:** `Phase 2 · MCP`

> "Phase 2 changes the **interface**, not the intelligence. The agent discovers and invokes
> reusable MCP tools: a generic `postgres-mcp-server` for SQL transport, plus our own
> `meridian-concierge` server for domain operations SQL can't express."

**Works:**

| Query | Notes |
| ----- | ----- |
| `Compare three trips from different categories and show their prices in euros.` | `compare_packages` + one `currency_convert` call per package — the exact operation SQL couldn't own |
| `Show me the off-season price range for Tokyo packages in November.` | `seasonal_price_band` (low/median/high) |

Point back to Phase 1: **same prompt, now it lands** — because it's a tool contract now.

**Second failure (rehearse this):**

| Query | Why it fails |
| ----- | ------------ |
| `Find a slow, romantic week in wine country with a villa stay.` | Mood/intent. Better tools, richer domain logic — the **intent gap is untouched**. |

> "The interface got portable and IAM-authed. The intelligence didn't. Matching a *mood*
> needs embeddings, not tools. That's Phase 3."

### Custom MCP memory server (sidebar, only if time — skip to protect minute 45)
The abstract calls out *"MCP servers for contextual memory."* We ship two servers:

| Server | Source | Tools |
| ------ | ------ | ----- |
| `awslabs.postgres-mcp-server` | public, via `uvx` | `connect_to_database`, `run_query` |
| `meridian-memory` | this repo | `recall_traveler_profile`, `recall_preferences`, `recall_recent_turns`, `semantic_recall_interactions`, `persist_turn`, `persist_preference` |

Every memory-server tool opens `db.scoped_session(traveler_id, agent_type='memory_agent')`:
it authorizes the workload against `traveler_identity_bindings` **before** RLS scope is set.

### Optional code walkthrough
- `backend/mcp/mcp_client.py` — pins `awslabs.postgres-mcp-server@1.0.9`
- `backend/mcp/concierge_server.py` — the custom FastMCP domain server
- IDE: `backend/agents/mcp_02/agent.py` — `MCPClient` + runtime tool discovery

---

## Phase 3 · Retrieval (8 min)

**Select:** `Phase 3 · Retrieval`

**Open by typing the Phase-2 failure a third time. Say nothing. Wait for the cards.**

> `Find a slow, romantic week in wine country with a villa stay.`
> → Tuscany Wine & Wellness, Amalfi Coast Villa Week, Douro / Tokyo Ryokan — each with a
> semantic-match score.

**Let it land.** Then explain what changed:

```
query ──► embed (Cohere Embed v4, 1024d)
trip_packages ──► pgvector cosine + tsvector ts_rank ──► Cohere Rerank 3.5 → top K
```

1. **Embed** — Cohere Embed v4, 1024 dimensions.
2. **Hybrid candidates** — pgvector cosine on `embedding` + tsvector on `search_vector`.
3. **Rerank** — Cohere Rerank 3.5 re-scores against the original query → top K.
4. **Supervisor + specialists** — a Strands `RetrievalAgent` delegates to `SearchAgent`,
   `PackageAgent`, `BookingAgent`. Different specialist, different tool, visible in the trace.

**Demo:**

| Query | Expected |
| ----- | -------- |
| `Find a slow, romantic week in wine country with a villa stay.` | The intent match MCP couldn't produce |
| `Family-friendly beach resort with snorkeling` | Rerank fixes order (Costa del Sol, Cancún, Maldives) |

**Third failure — the honest one (rehearse this, do not apologize):**

| Query | What happens |
| ----- | ------------ |
| `What did we decide about my October Tokyo trip last time? Continue from there.` | Zero products. A reasoning span states: *"I'm pure retrieval — no memory of prior turns. That's the next phase."* |

> "It understands what you *mean*. It has no idea who *you* are, and it can't remember a
> thing. And we can't ship this reading any traveler's data. That's Phase 4."

### Optional code walkthrough
- `backend/routers/chat.py` → `retrieval_supervisor_search`
- `backend/db/embedding_service.py` → `cohere.embed-v4:0`, `output_dimension: 1024`
- IDE: `backend/agents/retrieval_03/supervisor.py` + `search_agent.py`

---

## Phase 4 · Production — the governance climax (12 min)

**Select:** `Phase 4 · Production` (or **Chat as Alex Morgan → Phase 4** on the persona card)

> "Retrieval was intelligence. Production is **trust plus memory**. We authenticate the
> workload, authorize it for Alex, then let Aurora RLS scope every query. Alex Morgan flies
> from JFK, party of two, shellfish allergy, boutique over chain, Marriott Bonvoy Platinum.
> None of that is in the prompt — it's in Aurora."

### Beat 1 — memory lands (the payoff to Phase 3's honest failure)

1. **Seed the thread:** `Find a Tokyo culture trip for two with boutique stays, local food, and walkable neighborhoods.`
   The reply weaves in the shellfish allergy, JFK no-red-eyes, boutique preference — all
   pulled from Aurora **before** answering. None of it was typed.
2. **The recall that failed a phase ago now works:** `What did we decide about my October Tokyo trip last time? Continue from there.`
   `recall_session_context` + `recall_similar_interactions` return the Tokyo thread. *Point
   back to the Phase-3 failure.*

Point to the memory spans: `recall_session_context`, `recall_traveler_preferences`,
`recall_similar_interactions`, `persist_turn`.

> "Two memory tiers. **AgentCore Memory** is the managed session layer — we mirror each turn
> with `create_event` and read it back. **Aurora** is the durable system of record:
> preferences, interaction embeddings for semantic recall over pgvector, RLS-scoped per
> traveler. Reads run in one short transaction that authorizes the workload for Alex,
> pins Alex, and steps down to a least-privilege role. We commit before calling Runtime,
> AgentCore Memory, or Gateway. A separate short write transaction reauthorizes, persists,
> and audits; then `create_event` mirrors the committed turn into AgentCore Memory."

### Beat 2 — the governance probe (this is the climax)

Open the **RLS tab** and hit **Re-run probe**.

The probe proves the full chain, in order:

1. **Authenticated workload** — the AgentCore/STS subject.
2. **Traveler grant** — `ALLOW · Alex Morgan` from `traveler_identity_bindings`.
3. **Negative control** — the same workload gets `DENY · Jordan Lee` (no active binding).
4. **RLS collapse** — the same `COUNT(*)` runs scoped vs unscoped; `traveler_preferences`
   drops from **22 of 22** to **17 of 22**. The bar animates the rows disappearing.

> **20-sec narration:** "First, this workload is authenticated. Second, Aurora's binding
> table lets it claim Alex and *denies* the same workload when it claims Jordan — before any
> RLS scope is set, and both decisions are audited. Only then do we set the traveler scope.
> Now watch the same query collapse from all preference rows to Alex's rows."

**The teaching beat (point at 17 of 22):** *"Even if the LLM writes a query that forgets to
filter, it physically cannot leak another traveler's data."*

**State the distinction explicitly:** *"RLS does not authenticate Alex. It enforces the row
scope the binding made legitimate for this workload. This demo authorizes a workload — a
shared hosted app would also verify the end-user token and bind that subject. This demo does
not authenticate Alex as a human."*

**Why the step-down role matters (the reusable lesson):** *"The Data API connects as the
cluster master, which owns these tables and isn't subject to RLS. So inside the transaction
we `SET LOCAL ROLE` to a role that owns nothing and has no special attributes — subject to
the policy by construction. The best practice isn't juggling owner/FORCE/superuser flags;
it's: run your scoped queries as a role that's always covered."*

> See PRESENTER_GUIDE.md for the full Q&A handling (the `OR … = ''` seed branch, the
> `trip_interactions: 90 of 90` case, and the FORCE-proof framing).

### Beat 3 — the multi-step boundary (sets up the Coda)

Click the third Phase 4 pill (the disruption prompt):

> `My JFK flight to Tokyo just got cancelled. Rework the trip and check which departures are still open.`

Production recalls Alex, authorizes, and finds candidate trips — then **stops and refuses to
fake it.** The trace shows a **"Checkpointed workflow required"** span and the reply says it
won't collapse two dependent steps (rework the itinerary, then verify which departures are
still open) into one fluent paragraph. This is Phase 4's honest break: it *recognizes* the
multi-step boundary rather than pretending both steps completed atomically.

> "It recalled everything about Alex and found alternatives — then it stopped. A cancelled
> flight is two dependent steps: rework the trip, then verify which departures are still open.
> Production won't pretend it ran both inside one turn. When a booking pipeline hangs off step
> 1 finishing before step 2 runs, you want that explicit, checkpointed, and resumable. That's
> Phase 5."

The "Run this in Workflow" follow-up is the hand-off — the boundary is **detected in the
backend and rendered on screen**, not narrated. Click into Phase 5 and run the same prompt.

### Optional code walkthrough
- `backend/agents/production_04/concierge.py` → identity → `scoped_session(authorization=…)` → memory → Gateway → `persist_turn`
- `backend/db/rds_data_client.py` → `check_traveler_authorization`, `scoped_session`
- `examples/rls_for_agents.sql` + `examples/rls_app_role.sql` → the policies and the app role

---

## Coda · Durable Workflow — the flight-disruption replan (6 min)

**Select:** `Phase 5 · Workflow`

> "Strands picks tools when the LLM picks the call. LangGraph owns control flow when **we**
> want it explicit, branchable, and resumable. Same Aurora, same search and memory functions
> — now through named, checkpointed nodes."

```
            ┌─→ search ──────────┐   (intent == "plan": search → availability)
classify ──┼─→ availability ────┤
            └─→ memory_recall ───┤
                                synthesize → END
```

**Run the exact prompt Production handed off:**

> `My JFK flight to Tokyo just got cancelled. Rework the trip and check which departures are still open.`

Classify routes it to **plan**: `search` runs (re-find matching Tokyo trips), a checkpoint is
written, then the conditional edge continues to `availability` (verify open departures), a
second checkpoint, then `synthesize`. **Two sequential worker nodes, a checkpoint between
each** — the multi-step composition a single tool call can't make visible.

In the trace, point to:
- `Workflow node: classify → plan`
- `Workflow node: search` → `Checkpoint · …put`
- `Workflow node: availability` (step 2 of 2) → `Checkpoint · …put`
- `Workflow node: synthesize`

### Prove durable state (this protects the 5.0)

Run the backend through `scripts/start_checkpoint_tunnel.sh` with
`LANGGRAPH_CHECKPOINT_REQUIRED=true` and
`LANGGRAPH_DEMO_INTERRUPT_AFTER=search`.

1. Run the disruption prompt. The graph pauses after `search`; point to
   `PostgresSaver (Aurora · pooled)` and `next=availability`.
2. Stop and restart the backend. Do not clear the browser.
3. Click **Resume workflow from checkpoint**.
4. Point to `Workflow resumed from checkpoint`: the same `thread_id` continues
   at `availability`, backed by Aurora's `checkpoints`, `checkpoint_blobs`, and
   `checkpoint_writes` tables.

> "The Data API remains connectionless, but every turn reads and writes durable state in
> Aurora. AgentCore Memory carries conversational context across turns. When execution
> becomes multi-step, LangGraph externalizes workflow state through PostgresSaver into
> Aurora. We terminated the worker, restarted it, and resumed from the last committed node."

> **Transport, one sentence:** "Domain SQL uses the IAM-authorized Data API with database
> credentials in Secrets Manager. Durable, high-frequency checkpoints use a bounded
> PostgreSQL pool. MCP defines the tool contract, not the database transport."

**If asked "so did it rebook the flight?"** Be candid: *"No — it composes the durable
workflow a production system hangs the rebooking step on. The agent can plan it in one turn;
the graph makes that plan survivable and auditable."*

### Optional code walkthrough
- `backend/agents/orchestration_05/workflow.py` → `StateGraph`, conditional edges, `_checkpoint_activity` (names the real store)
- **Env:** `LANGGRAPH_CHECKPOINT_HOST=127.0.0.1`, `LANGGRAPH_CHECKPOINT_PORT=15432`,
  `LANGGRAPH_CHECKPOINT_REQUIRED=true` → shared pooled PostgresSaver. `MemorySaver` is
  local-only degraded mode.

---

## Close (1 min)

> "Five phases, one Aurora cluster. SQL set the agent shape. MCP made the tools portable.
> Retrieval closed the intent gap with pgvector, tsvector, and Cohere rerank. Production made
> it trustworthy — AgentCore identity, a workload-to-traveler grant, Aurora RLS, audited
> memory. Workflow made multi-step work durable — an explicit LangGraph StateGraph that
> checkpoints between nodes so a cancelled flight doesn't lose the plan. What changes each
> phase is **how much state the agent carries** and **how much governance sits between it and
> the database.** Everything else stayed the same."

### When teams use each pattern

| Phase | Good for |
| ----- | -------- |
| 1 · SQL | MVPs, internal tools, deterministic reporting |
| 2 · MCP | Standardizing DB/tool access across agents and frameworks |
| 3 · Retrieval | Customer-facing natural-language search at scale |
| 4 · Production | Returning users, preferences, compliance, per-turn audit |
| 5 · Workflow | Long-running, branching, checkpointed, resumable work |

---

## Query cheat sheet

### Phase 1 — works / breaks
| Works | Breaks |
| ----- | ------ |
| Show me city trips under $2,000 per traveler. | Compare three trips from different categories and show their prices in euros. |
| Show me beach and resort trips under $2,500 per traveler. | |

### Phase 2 — works / breaks
| Works | Breaks |
| ----- | ------ |
| Compare three trips from different categories and show their prices in euros. | Find a slow, romantic week in wine country with a villa stay. |
| Show me the off-season price range for Tokyo packages in November. | |

### Phase 3 — works / breaks
| Works | Breaks (honest, on purpose) |
| ----- | --------------------------- |
| Find a slow, romantic week in wine country with a villa stay. | What did we decide about my October Tokyo trip last time? Continue from there. |
| Family-friendly beach resort with snorkeling | |

### Phase 4 — as Alex Morgan
- Find a Tokyo culture trip for two with boutique stays, local food, and walkable neighborhoods.
- What did we decide about my October Tokyo trip last time? Continue from there.
- **Governance probe:** RLS tab → Re-run (ALLOW Alex · DENY Jordan · 17 of 22).
- **Hand-off:** My JFK flight to Tokyo just got cancelled. Rework the trip and check which departures are still open.

### Phase 5 — Workflow
- My JFK flight to Tokyo just got cancelled. Rework the trip and check which departures are still open. *(plan → search → availability)*
- Which duration options are available for Amalfi Coast Villa Week? *(availability)*
- Using what we decided about my October Tokyo trip last time, what should I do next? *(memory_recall)*

---

## curl quick tests

```bash
# Health
curl -s http://localhost:8000/health | jq .

# Memory profile
curl -s http://localhost:8000/api/memory/trv_meridian_demo | jq .

# Phase 1 — filter
curl -s -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Show me city trips under $2,000 per traveler.","phase":1}' | jq '.message, (.products | length)'

# Phase 3 — semantic
curl -s -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Find a slow, romantic week in wine country with a villa stay.","phase":3}' | jq '.message, (.products | length)'

# Phase 4 — memory + search
curl -s -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Tokyo trip for two in October","phase":4,"customer_id":"trv_meridian_demo"}' \
  | jq '.message, .conversation_id, (.products | length), (.memory_facts | length)'

# Phase 5 — flight-disruption replan (plan → search → availability)
curl -s -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"My JFK flight to Tokyo just got cancelled. Rework the trip and check which departures are still open.","phase":5,"customer_id":"trv_meridian_demo"}' \
  | jq '.message, (.activities[].title)'
```

---

## Troubleshooting

**Backend not responding**

```bash
curl -s http://localhost:8000/health
uvicorn backend.main:app --reload --port 8000
```

**`InternalServerErrorException` from the Data API on every query**

- The Serverless v2 cluster is scaling from idle or is mid-maintenance (`status: upgrading`).
  Check with `aws rds describe-db-clusters --db-cluster-identifier meridian-demo --query 'DBClusters[0].Status'`.
  Wait for `available`; warm it with one Phase 1 query before the talk.

**Phase 3/4 slow on first query**

- First embedding call to Bedrock adds ~1–3s. Normal for cold path.

**`ValidationException: invalid model identifier` (embeddings)**

- Set `EMBEDDING_MODEL=cohere.embed-v4:0` and `EMBEDDING_DIMENSION=1024` in `.env`.

**Phase 5 checkpoint span reads MemorySaver**

- Do not present this as the durable proof. Start `scripts/start_checkpoint_tunnel.sh`,
  restart the backend with `LANGGRAPH_CHECKPOINT_REQUIRED=true`, and verify `/health`
  reports `"checkpoint_durable": true`.

**Phase 4: "error loading memory"**

- `python scripts/seed_data.py`; verify `travelers` row `trv_meridian_demo` exists.

**Frontend shows Offline**

- Backend must be on port 8000; CORS allows localhost:5173.

---

## Demo traveler reference

| Field | Value |
| ----- | ----- |
| ID | `trv_meridian_demo` |
| Name | Alex Morgan |
| Home | JFK |
| Party | 2 |
| Goal | Tokyo culture trip — Oct 12–19 |
| Dietary | Shellfish allergy |
| Budget | ~$2k–3.5k per person |
| Negative control | Jordan Lee (no binding → DENY) |

---

## Resources

- [README.md](README.md) — setup and architecture
- [docs/PRESENTER_GUIDE.md](docs/PRESENTER_GUIDE.md) — full narration + code reference + Q&A
- [backend/db/schema.sql](backend/db/schema.sql) — full DDL
- [Strands Agents](https://github.com/strands-agents/sdk-python)
- [Amazon Bedrock — Cohere Embed v4](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html)
- [Model Context Protocol](https://modelcontextprotocol.io/)
