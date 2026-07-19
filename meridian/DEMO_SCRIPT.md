# Meridian Demo Script

## Building agentic travel workflows with Aurora PostgreSQL, MCP, and Strands Agents

**Duration:** ~60 minutes  
**Format:** Live web demo + optional code walkthrough  
**Tagline:** Plan. Fly. Land.

---

## Key files

| File | Purpose |
| ---- | ------- |
| `backend/routers/chat.py` | Live chat for SQL/MCP/Retrieval/Production (`sql_search`, `mcp_search`, `retrieval_search`, `production_search`) |
| `backend/db/schema.sql` | Travel-native Aurora schema (`trip_packages`, travelers, memory tables) |
| `backend/db/rds_data_client.py` | RDS Data API client |
| `backend/db/embedding_service.py` | Cohere Embed v4 on Bedrock (1024d) |
| `backend/mcp/mcp_client.py` | MCP client (Phase 2) |
| `backend/agents/retrieval_03/supervisor.py` | Strands supervisor + specialist agents |
| `backend/agents/production_04/concierge.py` | `MemoryAgent` (Production mode) |
| `backend/agents/production_04/memory_agent.py` | Strands `@tool` memory recall/persist |
| `backend/memory/store.py` | Aurora memory CRUD |
| `frontend/src/showcase/MeridianDeviceShowcase.tsx` | Live demo surface (`/showcase`) |
| `frontend/src/showcase/components/TravelerContextPanel.tsx` | Alex Morgan persona and memory rail |
| `scripts/travel_catalog.py` | 30 trip packages + demo traveler seed |

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

**If Aurora was reset**

```bash
python scripts/init_aurora_schema.py
python scripts/seed_data.py
```

**Optional — provision AgentCore for Phase 4 (@aws/agentcore CLI, Node-based)**

```bash
npm install -g @aws/agentcore
cd meridian_agentcore/agentcore
agentcore add memory --name meridian-session --strategies SEMANTIC --expiry 30
agentcore add gateway --name meridian-aurora --authorizer-type AWS_IAM
# agentcore add gateway-target ...  # Aurora MCP / Lambda / OpenAPI target
agentcore deploy -y
cd ../.. && python scripts/sync_agentcore_env.py --write
```

See `meridian_agentcore/README.md`. Phase 4 requires deployed AgentCore Runtime,
Gateway, and Memory resources; without them, the app surfaces an explicit
"AgentCore platform not configured" message instead of pretending to run
Production mode.

---

## Part 1 — Introduction (5 min)

### What to say

> "Meridian is an agentic **travel concierge** — not a chatbot bolted onto a search box. We climb a deliberate ladder: **Query → Tool → Intent → Trust → Durable Workflow**. The technical phases are SQL, MCP, Retrieval, Production, and Workflow — each one adds one capability on the same Aurora catalog."

Point to the **Architecture** section (five phase cards):

| Phase | Capability | Technical mode | Proof point |
| ----- | ---------- | -------------- | ----------- |
| 1 | Query | SQL | SQL executed |
| 2 | Tool | MCP | MCP tool invoked |
| 3 | Intent | Retrieval | pgvector + rerank |
| 4 | Trust | Production | RLS scoped + audited |
| 5 | Durable Workflow | Workflow | Checkpoint written |

> "Phases 1–3 teach the retrieval stack. Phase 4 is the production story: the agent **remembers** Alex Morgan before it searches. Phase 5 is the workflow story: explicit, branchable, resumable orchestration."

---

## Part 2 — Phase 1 · SQL (12 min)

**Select:** `Phase 1 · SQL`

### What to say

> "Phase 1 is the lab. One Strands agent, hardcoded tools, direct RDS Data API. Fast, debuggable — but it only owns **exact filters**, not reusable business tools."

### Demo queries that work

| Query | What happens |
| ----- | ------------ |
| `Show me city trips under $2,000 per traveler.` | Trip type + per-traveler price filter on `trip_packages` |
| `Show me beach and resort trips under $2,500 per traveler.` | Trip type + per-traveler price filter |
| `Business travel under $1500` | Type + price filter |

**Point to the agent trace:** RDS connection → filter SQL → package rows returned.

### Demo query that breaks (on purpose)

| Query | What happens |
| ----- | ------------ |
| `Compare three trips from different categories and show their prices in euros.` | **Wrong abstraction** — SQL can return rows, but comparison + per-package currency conversion belongs in a tool contract |

> "The user didn't ask a bad SQL question — they asked for a business operation. That's the hook for MCP."

### Optional code walkthrough

- `backend/routers/chat.py` → `phase1_search`
- `backend/search_utils.py` → `parse_search_query`, `execute_keyword_search`

---

## Part 3 — Phase 2 · MCP (12 min)

**Select:** `Phase 2 · MCP`

### What to say

> "Phase 2 changes the **interface**. The agent can now discover and invoke reusable MCP tools — generic SQL transport plus our custom travel-domain server."

### Demo queries

| Query | Notes |
| ----- | ----- |
| `Compare three trips from different categories and show their prices in euros.` | `compare_packages` + one `currency_convert` call per package |
| `Show me the off-season price range for Tokyo packages in November.` | `seasonal_price_band` |

### Demo query that still breaks

| Query | Notes |
| ----- | ----- |
| `Find a slow, romantic week in wine country with a villa stay.` | Mood/intent query — Phase 3 needed |

### Optional code walkthrough

- `backend/mcp/mcp_client.py` — connects to the public `awslabs.postgres-mcp-server`
- `backend/agents/mcp_02/agent.py`
- `backend/mcp/memory_server.py` — **our own MCP server** for traveler memory
- `backend/mcp/memory_mcp_client.py` — the symmetric stdio client

> "MCP gives you portability and a standard tool surface. It does not magically add embeddings."

### Custom MCP memory server (sidebar, ~3 min)

The abstract specifically calls out *"MCP servers for contextual memory."*
We ship two MCP servers in this repo:

| Server | Source | Tools |
| ------ | ------ | ----- |
| `awslabs.postgres-mcp-server` | public, run via `uvx` | `connect_to_database`, `run_query` |
| `meridian-memory` | this repo, `backend/mcp/memory_server.py` | `recall_traveler_profile`, `recall_preferences`, `recall_recent_turns`, `semantic_recall_interactions`, `persist_turn`, `persist_preference` |

Every tool on the memory server opens a `db.scoped_session(traveler_id, agent_type='memory_agent')`
transaction first, so Aurora RLS enforces per-traveler isolation regardless
of what SQL the MCP client sends.

Show it live:

```bash
PYTHONPATH=. python examples/memory_mcp_demo.py \
    --traveler trv_meridian_demo \
    --conversation conv_meridian_demo
```

You'll see the server boot over stdio, list its 7 tools, then exercise
each one against Aurora.  The last test asks for a non-existent traveler
and gets back `{}` — the RLS policy refuses to leak rows.

---

## Part 4 — Phase 3 · Retrieval (15 min)

**Select:** `Phase 3 · Retrieval`

### What to say

> "Phase 3 is where natural language works. **Cohere Embed v4** (1024 dimensions) plus PostgreSQL **pgvector** and **tsvector** — hybrid ranking. A **Strands supervisor** delegates to specialist agents in the trace."

### How search works (say while trace runs)

1. Supervisor receives query  
2. SearchAgent generates query embedding (Bedrock)  
3. Hybrid SQL: ~70% semantic + ~30% lexical  
4. Ranked `trip_packages` returned  

### Demo queries

| Query | Expected |
| ----- | -------- |
| `Find a slow, romantic week in wine country with a villa stay.` | Tuscany / Amalfi / Douro-style matches (MCP could not infer intent) |
| `Weekend in Paris under $2k` | Price-aware semantic match |
| `Family-friendly beach resort` | Intent-based matches |
| `Is the Maldives package available?` | Routes to PackageAgent availability path |

### The money shot — cross-phase comparison

1. Phase 2: `Find a slow, romantic week in wine country with a villa stay.` → no intent match
2. Phase 3: same query → ranked trips  

> "Same database. Same catalog. Different retrieval architecture."

### Optional code walkthrough

- `backend/routers/chat.py` → `phase3_search`
- `backend/db/embedding_service.py` → `cohere.embed-v4:0`, `output_dimension: 1024`
- `backend/db/schema.sql` → `semantic_trip_search`, HNSW index

---

## Part 5 — Phase 4 · Production trust + memory (15 min)

**Select:** `Phase 4 · Production` (or click **Chat as Alex Morgan → Phase 4** on the persona card)

### What to say

> "Phase 4 is the returning traveler. Meet **Alex Morgan** from JFK — traveling as a party of two, Tokyo culture trip Oct 12–19, shellfish allergy. None of that is in the prompt; it's in **Aurora** (`traveler_profiles`, `traveler_preferences`, `conversation_messages`, `trip_interactions`)."

Point to the **persona card** and trace memory spans:

- `recall_traveler_preferences`  
- `recall_session_context`  
- `recall_similar_interactions`  
- `persist_turn`  

### Demo queries

| Query | What to highlight |
| ----- | ----------------- |
| `Tokyo trip for two in October` | Tokyo packages + memory-aware greeting |
| `Beach escape under $2500 — remember my food allergies` | Budget + dietary context from profile |
| `What did we decide about my October Tokyo trip last time? Continue from there.` | Session / interaction recall grounded in the seeded Tokyo thread |

### Follow-up in same session

Run a second query without clearing chat — show `conversation_id` continuity and growing session memory.

### Optional code walkthrough

- `backend/agents/production_04/concierge.py` → `MemoryAgent.process_turn`
- `backend/agents/production_04/memory_agent.py` → `@tool` methods
- `backend/memory/store.py` → Aurora reads/writes
- `backend/agentcore/memory.py` → AgentCore Memory `create_event` / `list_memory_records`
- `backend/agentcore/identity.py` → `sts:GetCallerIdentity` + `get_resource_api_key`

> "Memory in Phase 4 has two layers. **AgentCore Memory** is the managed
> session store — it gives us the multi-turn working set without us
> running a Redis. **Aurora** is the durable preference and interaction
> store, RLS-scoped per traveler. The concierge writes every turn to
> AgentCore Memory and persists distilled signals to Aurora."

> "Orchestration here is **Strands Agents** + procedural routing in `chat.py` — not LangGraph."

---

## Part 6 — Booking demo (optional, 5 min)

From a trip result card, click **Book now** or send an order intent.

Trace shows booking flow spans; `bookings` / `booking_lines` tables persist the demo reservation.

```bash
curl -s -X POST http://localhost:8000/api/chat/order \
  -H 'Content-Type: application/json' \
  -d '{"product_id":"CTY-002","phase":3,"quantity":1}'
```

---

## Part 7 — Security: how agents talk to Aurora (5 min)

> "When the abstract says 'securely connect LLM agents to Aurora,' here's what that actually means in this demo. Three concrete controls — all enforced by the database, not the agent."

### 1. RLS pinned per turn

`backend/agents/production_04/concierge.py` opens an RDS Data API transaction at the
start of every Phase 4 turn and pins the session variables Aurora will
enforce:

```python
async with self.db.scoped_session(
    traveler_id=traveler_id, agent_type="concierge_agent"
) as tx:
    # every read/write inside the block runs under this transaction id
```

Internally that runs:

```sql
SELECT set_config('app.current_traveler_id', :tid,        true);
SELECT set_config('app.agent_type',          'concierge_agent', true);
```

…and `examples/rls_for_agents.sql` has `ENABLE ROW LEVEL SECURITY` and a
`USING (traveler_id = current_setting('app.current_traveler_id', true))`
policy on `traveler_preferences`, `conversations`, `conversation_messages`,
and `trip_interactions`.  Even if the agent forgot the `WHERE` clause, Aurora
would return zero foreign rows.

**Show in trace:** the new **Security · RLS scope set on Aurora session**
span, with the IAM principal ARN, traveler id, agent type, and policy list.

### 2. Agent-type scoping on bookings

A second policy on `bookings` enforces that only `booking_agent`,
`supervisor_agent`, or `concierge_agent` can read or mutate confirmed
reservations.  A search-only agent that calls the same DB role gets nothing
back.

### 3. Audit trail

Every Phase 4 turn writes one row to `agent_audit_log` from inside the same
transaction:

```sql
SELECT * FROM agent_iam_audit ORDER BY ran_at DESC LIMIT 5;
```

Each row records the IAM principal (`sts:GetCallerIdentity`), the agent
name, the operation, the RLS variables that were set, and how many rows the
agent saw.  This is the answer to "prove that agent A could not read
traveler B's data."

### Demo

```bash
# The Phase 4 turn writes its own audit row.  Show the most recent one:
aws rds-data execute-statement \
    --resource-arn "$AURORA_CLUSTER_ARN" \
    --secret-arn   "$AURORA_SECRET_ARN" \
    --database     meridian \
    --sql 'SELECT * FROM agent_iam_audit LIMIT 5'
```

> "This is the loop. RLS makes the database, not the agent, the source of
> isolation truth.  The audit table makes it auditable.  The Strands agent
> doesn't get to opt out."

---

## Part 7b — Phase 5 · Workflow with LangGraph (8 min)

> "Phases 3 and 4 use Strands for tool routing.  Phase 5 shows the *workflow*
> pattern — an explicit StateGraph with conditional branches and a checkpointed
> state that survives interruption."

### What changed

- Same Aurora data, same SearchAgent / PackageAgent / MemoryAgent.
- New orchestrator: `agents/orchestration_05/workflow.py` builds a `StateGraph`:

  ```
  classify ─┬─→ search ─────────┐
            ├─→ availability ───┤
            └─→ memory_recall ──┤
                                ▼
                            synthesize → END
  ```
- `_classify_intent()` is rule-based today; swap in Claude on Bedrock for
  an LLM router when needed.
- Checkpointing: if `LANGGRAPH_CHECKPOINT_DSN` is set we use `PostgresSaver`
  (durable, multi-process — state lives in Aurora).  Otherwise the workshop
  runs with `MemorySaver` so it works without Aurora connectivity.

### Demo

In the UI, switch the phase pill to **Workflow** and try the same prompts:

- "Which duration options are available for Amalfi Coast Villa Week?" → classify routes to
  `availability`
- "Using what we decided about my October Tokyo trip last time, what should I do next?" → classify
  routes to `memory_recall`
- "Plan the Kyoto extension: find matching packages, then verify available
  duration options." → classify routes to `plan`

In the trace, point out:

- The `Workflow node: classify → <intent>` span — the StateGraph branch.
- The `Workflow node: <branch>` delegation span calling the underlying agent.
- The `Workflow node: synthesize` span composing the response.
- The `checkpointer` field on the classify span — `PostgresSaver (Aurora)`
  if wired, otherwise `MemorySaver (in-process)`.

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What dates are available for Tokyo in October?","phase":5}' | jq '.activities[].title'
```

> "Same trip data, different orchestration shape.  Strands is great when an
> agent decides *which tool* to call.  LangGraph is great when *you* want to
> own the control flow and have it be inspectable, branchable, and resumable."

---

## Part 8 — Architecture summary (5 min)

### Ladder recap

```
Phase 1   Query              SQL executed against trip_packages
Phase 2   Tool               MCP tool invoked against Aurora
Phase 3   Intent             pgvector + tsvector + rerank
Phase 4   Trust              RLS scoped + audited memory
Phase 5   Durable Workflow   checkpoint written between graph nodes
```

### When teams use each pattern

| Phase | Good for |
| ----- | -------- |
| 1 | MVPs, internal tools, deterministic reporting |
| 2 | Standardizing DB access across agents and frameworks |
| 3 | Customer-facing natural language search at scale |
| 4 | Returning users, preferences, compliance, multi-turn planning |
| 5 | Long-running workflows with branching, checkpoints, and resumability |

### Key takeaways

1. **Aurora is the system of record** — catalog, vectors, and memory in one database  
2. **MCP standardizes tools** — it doesn't replace good retrieval  
3. **Embeddings change the UX** — intent queries work without keyword luck  
4. **Memory changes the product** — Phase 4 feels like a concierge, not a search box  

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

### Phase 3 — suggested

- Weekend in Paris under $2k  
- Family-friendly beach resort  
- Is the Maldives package available?  
- Find a slow, romantic week in wine country with a villa stay. *(compare to Phase 2)*

### Phase 4 — suggested (as Alex Morgan)

- Tokyo trip for two in October  
- Beach escape under $2500 — remember my food allergies  
- What did we decide about my October Tokyo trip last time? Continue from there.

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
```

---

## Troubleshooting

**Backend not responding**

```bash
curl -s http://localhost:8000/health
# restart
uvicorn backend.main:app --reload --port 8000
```

**Phase 3/4 slow on first query**

- First embedding call to Bedrock adds ~1–3s. Normal for cold path.

**`ValidationException: invalid model identifier` (embeddings)**

- Set `EMBEDDING_MODEL=cohere.embed-v4:0` and `EMBEDDING_DIMENSION=1024` in `.env`
- Ensure model access is enabled in Bedrock console (us-east-1)

**`expected 1024 dimensions, not 1536`**

- Cohere v4 defaults to 1536d; embedding service must pass `output_dimension: 1024` to match `vector(1024)` in schema.

**Phase 4: "error loading memory"**

- Check Aurora has seed data: `python scripts/seed_data.py`
- Verify `travelers` row `trv_meridian_demo` exists

**No trip results**

- Confirm seed ran (30 packages in `trip_packages`)
- Try Phase 3 with a simpler query: `Tokyo culture`

**Frontend shows Offline**

- Backend must be on port 8000; CORS allows localhost:5173

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

---

## Resources

- [README.md](README.md) — setup and architecture  
- [backend/db/schema.sql](backend/db/schema.sql) — full DDL  
- [Strands Agents](https://github.com/strands-agents/sdk-python)  
- [Amazon Bedrock — Cohere Embed v4](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html)  
- [Model Context Protocol](https://modelcontextprotocol.io/)
