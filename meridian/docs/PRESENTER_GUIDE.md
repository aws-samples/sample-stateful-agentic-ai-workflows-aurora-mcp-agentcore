# Meridian — Presenter Guide

One guide for the whole talk. **Part 1** is the narration script (what to *say*,
on stage). **Part 2** is the code reference (what to *show* in the IDE — files,
snippets, env knobs, FAQ). The dry-run checklist is at the end.

> **Source of truth:** every tool name, signature, and SQL example matches
> `backend/agents/{sql_01, mcp_02, retrieval_03, production_04, orchestration_05}/`,
> and every demo prompt matches the live pills in
> `frontend/src/showcase/lib/showcaseAdapters.ts`. Model: Bedrock **Claude Sonnet 5**
> (`global.anthropic.claude-sonnet-5`), fallback chain Sonnet 5 → Haiku 4.5 → Opus 4.8.
> Retrieval ranks with **Cohere Embed v4** + **Cohere Rerank 3.5** (`us.cohere.rerank-v3-5:0`).

**Shape of the talk:** climb the capability ladder: **Query → Tool → Intent → Trust →
Durable Workflow**. The technical modes are SQL, MCP, Retrieval, Production, and Workflow.
The spine is three clear failures that each motivate the next rung: SQL cannot compose
domain tools, MCP cannot infer intent, Retrieval cannot remember prior turns. ~60 minutes
plus intro and close.

---

# PART 1 — NARRATION SCRIPT

## The traveler

Everything personal in Phases 3–5 hangs on one seeded traveler. These facts live in
Aurora `traveler_preferences` / `trip_interactions` for `trv_meridian_demo` — never in
the prompt. They're what the "For you" panel shows.

- **Alex Morgan** — home airport **JFK**, **party of 2**
- **Shellfish allergy** (dining gets vetted)
- **No red-eyes** out of JFK
- **Boutique over chain** lodging
- **Marriott Bonvoy Platinum Elite + United MileagePlus Premier 1K** loyalty
- **Recent trips:** Tuscany (booked, Feb 2026), Kyoto (held)
- **Tokyo culture trip** Oct 12–19 in motion

## The three recurring beats

**Beat 1 — the tool-contract query (hinge of Phases 1–2):**
> *"Compare three trips from different categories and show their prices in euros."*
- **SQL:** awkward / not owned by the SQL filter path. It can return rows, but it has no
  reusable `compare_packages` + `currency_convert` contract.
- **MCP:** lands — the custom `meridian-concierge` MCP server exposes comparison and
  currency tools that a Bedrock turn can call.

**Beat 2 — the intent query (hinge of Phases 2–3):**
> *"Find a slow, romantic week in wine country with a villa stay."*
- **MCP:** nothing. Better tools, same intent gap.
- **Retrieval:** Tuscany Wine & Wellness / Amalfi / Douro. *The moment of release.*

**Beat 3 — the memory query (hinge of Phases 3–4):**
> *"What did we decide about my October Tokyo trip last time? Continue from there."*
- **Retrieval:** fails *honestly* — "I can't recall prior turns; that's the next phase." Zero products, a reasoning span naming the limitation. **Intentional, not a bug.**
- **Production:** lands — recalls the Tokyo thread from AgentCore Memory + Aurora.

Rehearse Beat 1's first failure: type it in SQL, hit enter, point at the missing tool
contract. Say *"this is why the next phase exists."* The audience should feel each rung
earn its place.

---

**Setup (before Phase 1).** Open the live concierge `/showcase`. Say: *"Same Aurora,
same Strands SDK, same `@tool` pattern across all five phases. The only thing changing
is the capability we add: Query, Tool, Intent, Trust, then Durable Workflow."* By the end
of Phase 2 they should know what an agent is, what tools are, and that it all runs on
Aurora through RDS Data API — holding one question: *"fine for keyword filters — what
about real language?"*

> **Theme:** `/showcase` ships dark and light ("Daylight"). It auto-picks from the OS,
> and the top-right toggle flips it live. **In a bright room or on a washed-out projector,
> switch to light** — every phase, panel, and the trace stay legible. Dark is the default
> for a dim stage.

## Phase 1 · SQL — the foundation (≈ 5 min) · `agents/sql_01/agent.py`

**Open with:** *"The smallest possible agent that talks to Aurora. A Strands `Agent`,
five `@tool` methods, and RDS Data API."* Show four boxes: User → Agent → Tools → Aurora.

Tools (all `@tool`, async, hit `trip_packages`): `_search_trip_packages`,
`_lookup_trip_package`, `_check_departure_availability`, `_calculate_booking_total`,
`_process_booking`.

**Demo (live pills, in order):**
1. **Works** — *"Show me city trips under $2,000 per traveler."* Trace shows `WHERE trip_type = :t AND price_per_person <= :p`.
2. **First failure** — *Beat 1.* *"Compare three trips from different categories and show their prices in euros."* **Pause.** *"This is not a better WHERE clause. It is a tool-contract problem."*

**Talking points:** *"Strands gives the LLM the schema for each `@tool`. Bedrock picks
which to call. RDS Data API is how we reach Aurora — no pool, IAM-auth, the path every
later phase reuses. The SQL tools are narrow filters; they do not own business operations
like compare, convert, seasonality, or loyalty lookups."*

**Bridge → Phase 2:** *"Three teams want this catalog. They shouldn't all hand-write SQL. Who owns the tools?"*

## Phase 2 · MCP — the contract layer (≈ 5 min) · `agents/mcp_02/agent.py`

**Open with:** *"Two MCP servers in one turn. `postgres-mcp-server` carries the SQL.
A custom `meridian-concierge` server adds domain tools SQL alone can't express."*
Custom tools: `compare_packages`, `currency_convert`, `seasonal_price_band`,
`region_inventory`, `loyalty_balance` — discovered at runtime, IAM-authed, portable.

**Demo:**
1. *"Compare three trips from different categories and show their prices in euros."* → `compare_packages` + one `currency_convert` call per package. *Pause — a single SQL filter does not own this reusable operation.*
2. *"Show me the off-season price range for Tokyo packages in November."* → `seasonal_price_band` (low/median/high).
3. **Beat 2.** *"Find a slow, romantic week in wine country with a villa stay."* Still nothing. *"Better tools, richer domain logic — intent gap untouched. The interface got better. The intelligence didn't."*

**Talking points:** *"MCP changes the **interface**, not the **intelligence**. Tools are
versioned and IAM-authed; teams share them without rebuilding. But matching a **mood**
needs embeddings, not tools."*

**Bridge → Phase 3:** *"The interface is portable. But every query is still keyword-based. We need the agent to understand what we **mean**, not what we type."*

---

## Phase 3 · Retrieval — the spine pays off (≈ 7 min) · `agents/retrieval_03/`

**Open by typing Beat 1 a third time. Say nothing. Wait.**
> → Tuscany Wine & Wellness, Amalfi Coast Villa Week, Douro / Tokyo Ryokan — each with a `semantic_match` score.

**Let it land.** Don't explain yet. Then:

```
query ──► embed (Cohere v4, 1024d)
trip_packages ──► hybrid candidates (pgvector + tsvector) ──► Cohere Rerank 3.5 → top K
```
1. **Embed** — Cohere v4, 1024d.
2. **Hybrid candidates** — pgvector cosine on `embedding` + tsvector `ts_rank` on `search_vector`; ~25 candidates.
3. **Rerank** — Cohere Rerank 3.5 re-scores against the original query → top K.

**Architecture — supervisor + specialists:**
- `RetrievalAgent` (supervisor) — `_delegate_to_search / _package / _booking`
- `SearchAgent` — `_hybrid_search_tool(query, limit)` (one tool; full hybrid pipeline inside)
- `PackageAgent` — `_get_details_tool`, `_check_availability_tool`
- `BookingAgent` — `_calculate_booking_total_tool`, `_process_booking_tool`

**Demo:**
1. **Beat 1** (third time, lands).
2. *"Family-friendly beach resort with snorkeling"* — show the rerank fixing order (Costa del Sol, Cancún, Maldives).
3. **Beat 2, first take — the honest failure.** Returns nothing, says *"I'm pure retrieval — no memory of prior turns. That's the next phase."* **Deliberate setup for Production. Don't apologize.**

**Talking points:** *"Three things change at once: a query embedding, a hybrid index, a
reranker. Hybrid search picks the **candidates**. The reranker picks the **order**. The
supervisor picks the **specialist**. They share Aurora, not code. But notice — it
understands the request, and still has no idea who **you** are."*

**Bridge → Phase 4:** *"It understands what you mean. It just failed to remember what we discussed — because it has nowhere to. And we can't ship this with the agent reading any traveler's data."*

## Phase 4 · Production — the trust + memory layer (≈ 7 min) · `agents/production_04/`

**Open with:** *"Retrieval was intelligence. Production is trust and memory.
We authenticate the workload, authorize it for Alex, then let Aurora RLS scope
every query."*

**The production envelope on every turn:**
1. **Identity** — live AgentCore workload identity, or authenticated IAM workload fallback
2. **Authorization** — `traveler_identity_bindings` must ALLOW that subject for Alex
3. **Runtime** — session envelope (`runtimeSessionId`, microVM isolation)
4. **Memory** — session recall + semantic recall + `create_event` mirror
5. **Aurora RLS tx** — authorized traveler scope + `SET LOCAL ROLE`; memory `@tools` run inside it
6. **Gateway** — managed MCP `tools/list` + `tools/call` for trip search
7. **persist_turn** — Aurora write + AgentCore Memory write-back

**The four memory `@tools`:** `recall_session_context`, `recall_traveler_preferences`,
`recall_similar_interactions`, `persist_turn`. All read/write Aurora; `persist_turn` is
the only writer (Aurora only — the AgentCore write-back is a separate orchestrator call).

**Memory-slide narration (two tiers):** *"Memory has two tiers. Short-term session memory
lives in AgentCore Memory, the managed layer — we mirror each turn with `create_event` and
read it back into the trace with `list_memory_records` and `retrieve_memory_records`.
Durable memory lives in Aurora: `trip_interactions` with embeddings for semantic recall
over pgvector HNSW, traveler preferences for facts like the shellfish allergy and loyalty
programs, and a durable copy of the session. Four MemoryAgent tools — three read, and
`persist_turn` is the only writer — but it writes Aurora only; the AgentCore write-back is
a separate call from the orchestrator. Every read, and that write, runs in one transaction
that first authorizes the workload for Alex, pins Alex with
`set_config('app.current_traveler_id', …)`, and then `SET LOCAL ROLE` into a
least-privilege role. The authorization lookup rejects arbitrary traveler IDs;
RLS denies rows outside the authorized scope. That role switch is the catch:
our connection is the Aurora master user,
which isn't subject to RLS otherwise — step down, and the policy applies. We'll watch it in
the demo — asking what was decided about Tokyo last time. This time it remembers the thread,
grounded in Alex's stored preferences. The grounding is all in Aurora — none of it's in the
prompt."*

**Memory Q&A (only if asked):**
- *AgentCore vs Aurora?* — *"AgentCore is the managed session layer; Aurora is the durable
  system of record where preferences and embeddings live, RLS-scoped — the mirror bridges
  them."*
- *What are the wrappers?* — *"Our **AgentCore Memory client** (`agentcore/memory.py`) has
  wrapper methods — `record_turn`, `list_recent_turns`, `semantic_recall` — each calling
  exactly one AgentCore Memory API: `create_event`, `list_memory_records`,
  `retrieve_memory_records`. The slide shows the AWS API names; the wrappers are our thin
  adapter. The MemoryAgent is separate — it owns the four Aurora `@tool`s above."*
- *Does the AgentCore read drive the answer?* — *"No — the reply is grounded in the Aurora
  recall tools; that's where preferences and past interactions come from. AgentCore Memory
  is the managed session record we write and read back; Aurora is the retrieval source this
  turn. Two real layers, clear division of labor."*

**Demo — one Tokyo storyline, IN ORDER:**
1. **Seed the thread** — *"Find a Tokyo culture trip for two with boutique stays, local food, and walkable neighborhoods."* Prompt carries no allergy/airport/loyalty; the agent weaves in shellfish allergy, JFK no-red-eyes, boutique-over-chain — all from Aurora **before** answering.
2. **Beat 2, second take — it lands.** Same prompt that failed a phase ago; now `recall_session_context` + `recall_similar_interactions` return the Tokyo thread. *Point back to that failure.*
3. **Multi-step boundary** — *"Plan the Kyoto extension: find matching packages, then verify available duration options."* Production can reason about the request, but hands it to Workflow so the dependent steps are explicit and resumable. *Sets up Phase 5.*

**Talking points (trust pitch):** *"Authentication, authorization, and row
filtering are different controls. AgentCore Identity or STS tells us which
workload called. Aurora maps that subject to Alex. Only then does RLS scope the
transaction. We audit both the authorization decision and rows returned."*

### The governance probe — proving identity, authorization, and RLS live

Open the **RLS tab** and hit **Re-run probe**. It shows the authenticated
subject, `ALLOW · Alex Morgan`, and a live `DENY · Jordan Lee` negative
control before running the same `COUNT(*)` scoped vs unscoped.

- **20-sec narration:** *"First, this AWS or AgentCore subject is authenticated.
  Second, Aurora's binding table allows it to claim Alex and denies the same
  subject when it claims Jordan. Only then do we set the RLS scope. Now watch
  the same query collapse from all preference rows to Alex's rows. The binding
  prevents this workload from expanding to an ungranted traveler; RLS prevents
  cross-traveler rows."*
- **The teaching beat (point at 17 of 22):** *"Even if the LLM writes a query that forgets
  to filter, it physically cannot leak another traveler's data."*
- **The distinction to state explicitly:** *"RLS does not authenticate Alex.
  It trusts the traveler scope supplied by the app. The binding makes that
  scope legitimate for this workload, not for a human caller."*
- **Handle `trip_interactions: 90 of 90` before they ask:** *"No hidden rows there because
  every interaction in this dataset is Alex's — RLS is still active, there's just nothing
  to filter out. The preferences table is where you see the cut, because that's where the
  decoy lives."*
- **If asked "how does the count actually drop?" (ties to the RLS slide):** *"The Data API
  uses whatever DB user its secret maps to — ours is the cluster master role, and on this
  Aurora cluster that role isn't subject to RLS (Postgres's own row_security_active()
  returns false for it — it's not superuser or BYPASSRLS, just the master role). So inside
  the transaction we `SET LOCAL ROLE` to a least-privilege role and set the traveler GUC —
  now the policy bites."*
  (If pushed on the exact mechanism: *"I don't over-claim the Aurora internal — the
  observable fact is master sees all rows, the non-privileged role sees only the
  traveler's. The lesson is run least-privilege, which production would do with its own
  secret anyway."*)
- **The general principle (the safe, owner/FORCE-proof framing — use this on the RLS
  slide):** *"RLS gives privileged roles special treatment — superusers and BYPASSRLS roles
  are never subject to it, and a table's owner can sit outside it too. The Data API connects
  as whatever user its secret maps to, and the simplest setup uses the cluster's master user
  — which owns these tables. Rather than reason about exactly **when** a privileged, owning
  role is covered, the best practice is simpler: don't run your scoped queries as it. Two
  ways there — step down per-transaction to a least-privilege role, like we do; or create
  the tables as a least-privilege role up front and point the app's secret at that role.
  Either way you rely on the one rule that's never in doubt: a role that owns nothing, with
  no special attributes, is always subject to the policy."*
  (If someone invokes FORCE: *"Right — FORCE is meant to cover the owner. Which is exactly
  why I don't want security depending on getting owner, superuser, and FORCE flags all
  perfectly right. I run as a role that owns nothing and has no special attributes — subject
  to the policy by construction, no flag-juggling."*)
- **If asked about the `OR … = ''` branch (only if asked):** *"That empty-string branch is
  the admin/seed path; the app always sets the GUC, so it never hits it."* Don't volunteer
  this unprompted — it invites a "so it's not secure?" tangent.
- **If asked about end users:** *"This sample binds an authenticated workload.
  A shared hosted app also verifies the end-user token and stores that user
  subject, for example Cognito `sub`, in the same traveler binding boundary.
  This demo does not authenticate Alex as a human user."*

> Keep the claim precise: the live proof is workload authentication and
> workload-to-traveler authorization. End-user authentication is the production
> extension, not something this demo silently claims.

**Bridge → Phase 5:** *"That last prompt asked for three things in one breath. Strands chained them, but the routing was invisible. What if we want each step explicit, branchable, and resumable weeks later?"*

---

## Phase 5 · Workflow — the durability layer (≈ 8 min) · `agents/orchestration_05/workflow.py`

**Open with:** *"Strands picks tools when the LLM picks the call. LangGraph owns control
flow when **we** want it explicit, branchable, resumable. Same multi-intent Tokyo plan —
now through named, checkpointed nodes."*

```
            ┌─→ search ──────────┐  (if intent=="plan": search → availability)
classify ──┼─→ availability ────┤
            └─→ memory_recall ───┤
                                synthesize → END
```

**Callback — the limitation you planted in Phase 4.** The exact Kyoto extension
prompt asks for two dependent operations: find matching packages, then verify their
available duration options. Production recognizes the boundary and hands it off rather
than implying both steps completed inside one response. Phase 5 can (1) **run** each
operation as an inspectable node, (2) **checkpoint** between them, and (3) **resume**
if execution is interrupted.

**Demo (live pills, in order):**
1. *"Which duration options are available for Amalfi Coast Villa Week?"* → classify → **availability** → synthesize; a `PostgresSaver.put` after the PackageAgent node.
2. *"Using what we decided about my October Tokyo trip last time, what should I do next?"* → classify → **memory_recall** → synthesize; same RLS-scoped memory, now as an explicit graph node.
3. *"Plan the Kyoto extension: find matching packages, then verify available duration options."* → the payoff: classify → **search → availability** → synthesize. **Two sequential worker nodes, a checkpoint between each.** It is the exact prompt Production handed off, so the upgrade is visible rather than theoretical.

**Talking points:** *"Same Aurora, same tools — what changed is the **orchestration**. An
explicit StateGraph: classify fans out by intent, and the edge out of `search`
conditionally continues to `availability` for a 'plan'. PostgresSaver writes the entire
`WorkflowState` to Aurora after every node — threads scope by `traveler_id`, checkpoints
by `thread_id`. Pause Tuesday, resume Thursday, same state. Phase 4 planned the trip in
its head and asked to proceed; Phase 5 writes the plan down as steps it can run, branch
on, and resume."*

**If asked "so did it book the Marriott?"** Be candid: *"No — it composes the durable
workflow a production system hangs that booking step on. The agent can plan it in one
turn; the graph makes it survivable and auditable."* That honesty is on-brand.

---

## Closing (≈ 1 min)

> *"Five phases, one Aurora cluster. SQL established the agent shape. MCP swapped the
> tools to a portable contract. Retrieval closed the keyword gap — pgvector + tsvector +
> Cohere Rerank. Production made it real — AgentCore + Aurora RLS + per-turn audit + the
> memory that turned 'what did we discuss?' from a failure into a recall. Workflow
> wrapped it in LangGraph — explicit StateGraph, conditional edges, PostgresSaver
> checkpoints. Same Aurora throughout. Same Strands `@tool` pattern. What changes each
> phase is **how much state** the agent carries and **how much governance** sits between
> it and the database."*

## Five bridge sentences (memorize)

1. *"Three teams want this catalog. They shouldn't all hand-write SQL. Who owns the tools?"* → **Phase 2**
2. *"The interface is portable. But every query is still keyword-based. We need the agent to understand what we **mean**."* → **Phase 3**
3. *"It understands what you mean — it just failed to remember, because it has nowhere to. And we can't ship this reading any traveler's data."* → **Phase 4**
4. *"Production planned all three in its head, then asked permission to proceed. What if we want each step run, branchable, and resumable — not just described?"* → **Phase 5**
5. *"Same Aurora throughout. Five phases, one substrate."* → **Close**

---

# PART 2 — CODE REFERENCE

Use this when **showing code on screen**. Each phase: what to say, which file, snippets
that match the repo.

## Architecture decision (answer this first for builders)

**You don't need three separate apps — one product story, three orchestration layers:**

| Layer | Technology | Phases | What the audience learns |
| ----- | ---------- | ------ | ------------------------ |
| **Data plane** | Aurora + RDS Data API + pgvector | 1–5 | Secure, shared source of truth |
| **Agent plane (implicit loops)** | Strands Agents + `@tool` | 1–4 | Bedrock chooses tools; multi-agent patterns |
| **Workflow plane (explicit graphs)** | LangGraph `StateGraph` | 5 | Branching, checkpointing, resumable |
| **Managed services** | AgentCore Runtime + Gateway + Memory + Identity | 4 | Managed hosting, MCP tools, session memory, identity |

Phase 5 does **not** replace Strands with AgentCore runtime — AgentCore is the hero of
Phase 4. Phase 5's differentiator is **LangGraph**: edges you can whiteboard, state you
can checkpoint in Aurora, workflows you can resume after failure.

### Live demo vs IDE walkthrough

The UI always works. The live API path and the modules you show in the IDE intentionally
differ in early phases — the demo keeps deterministic fallbacks so the room never stalls:

| Phase | Live demo (`chat.py`) | Show in IDE |
| ----- | --------------------- | ----------- |
| 1 | Procedural keyword SQL | `sql_01/agent.py` — `@tool` + `Agent` |
| 2 | MCP `run_query` session | `mcp_02/agent.py` — `MCPClient` + auto-discovered tools |
| 3 | Hybrid search (+ Strands supervisor when `STRANDS_ORCHESTRATION=full`) | `retrieval_03/supervisor.py` |
| 4 | Full `ProductionAgent.process_turn()` | `production_04/concierge.py` + `memory_agent.py` |
| 5 | Full LangGraph `OrchestrationAgent` | `orchestration_05/workflow.py` |

## Phase 1 — SQL Agent · `backend/agents/sql_01/agent.py`

```python
from strands import Agent, tool
from strands.models import BedrockModel

self.agent = Agent(
    model=BedrockModel(model_id=config.bedrock.model_id, region_name="us-east-1"),
    tools=[
        self._search_trip_packages,        # ILIKE keyword search — the one that fails
        self._lookup_trip_package,
        self._check_departure_availability,
        self._calculate_booking_total,
        self._process_booking,
    ],
    system_prompt=self._get_system_prompt(),
)
```
**Say:** "One agent, five tools, direct RDS Data API. Bedrock picks the tool; each runs
SQL and logs the query for the trace. The limitation is ownership: comparing packages
across categories and converting each price is not a better WHERE clause; it is a
reusable tool-contract problem."
**Live path:** `chat.py` → `phase1_search()` (same SQL, no LLM loop).

## Phase 2 — MCP Agent · `backend/agents/mcp_02/agent.py`

```python
from strands.tools.mcp import MCPClient

self.mcp_client = MCPClient(
    server_name="postgres-mcp-server",
    command="uvx",
    args=["awslabs.postgres-mcp-server@1.0.9"],   # pinned: takes conn config via CLI flags
)
await self.mcp_client.connect()
mcp_tools = await self.mcp_client.list_tools()   # discovered at runtime
```
**Say:** "Phase 2 swaps the wire protocol. The agent discovers MCP tools at runtime;
IAM + RDS Data API auth stays the same underneath. Plus a custom `meridian-concierge`
FastMCP server for domain tools SQL can't express."
**Pin note:** the live client (`backend/mcp/mcp_client.py`) pins `@1.0.9` and passes the
cluster/secret ARNs as **server-start CLI flags** (`--resource_arn` / `--secret_arn`).
`@latest` drifted to auto-discovering the secret, which resolves to `None` for a
Serverless v2 secret with a random suffix — pinning avoids that on stage.
**Live path:** `backend/mcp/mcp_client.py` + `chat.py` → `phase2_search()`.

## Phase 3 — Retrieval · `retrieval_03/supervisor.py` + `search_agent.py`

```python
# supervisor.py — delegation tools
self.agent = Agent(model=self.model, tools=[
    self._delegate_to_search, self._delegate_to_package, self._delegate_to_booking,
])

@tool
async def _delegate_to_search(self, query: str) -> dict:
    """Bedrock calls this when the traveler wants trip discovery."""
    result = await self.search_agent.hybrid_search(query)
    self.last_search_packages = result.get("packages", [])
    return result
```
```python
# search_agent.py — one tool, full hybrid pipeline inside
@tool
async def _hybrid_search_tool(self, query: str, limit: int = 5) -> List[dict]:
    return await self.hybrid_search(query, limit)

async def hybrid_search(self, query, limit=5):
    # 1. embed (Cohere v4, 1024d)  2. pgvector candidates (semantic_trip_search)
    # 3. tsvector candidates       4. merge+dedup  5. Cohere Rerank 3.5 → top K
    ...
```
**Say:** "The supervisor's tools are *delegation* wrappers; Bedrock picks the specialist.
Inside the search tool: 1024-dim Cohere Embed v4, hybrid pgvector + tsvector, then Cohere
rerank. `semantic_trip_search` is the pgvector arm only — the hybrid is assembled in Python."
**Env:** `STRANDS_ORCHESTRATION=full` (default) = live supervisor; else procedural hybrid in `chat.py`.

## Phase 4 — Production · `production_04/concierge.py` + `memory_agent.py`

| Service | File | Trace span |
| ------- | ---- | ---------- |
| Runtime | `agentcore/runtime.py` | `AgentCore Runtime · session envelope` |
| Identity | `agentcore/identity.py` | `AgentCore Identity resolved` |
| Memory | `agentcore/memory.py` | `list` + `semantic retrieve` + `create_event` |
| Gateway | `agentcore/gateway.py` | `tools/list` + `tools/call` |

```python
# memory_agent.py — the four memory @tools (all RLS-scoped Aurora)
class MemoryAgent:
    self.agent = Agent(model=self.model, tools=[
        self.recall_session_context, self.recall_traveler_preferences,
        self.recall_similar_interactions, self.persist_turn,
    ])

    @tool
    async def recall_traveler_preferences(self, traveler_id: str, limit: int = 8):
        """Long-term facts from traveler_preferences (Aurora)."""
        facts = await self.store.recall_preferences(traveler_id, limit)
        return {"facts": facts}
```
```python
# concierge.py — authorization precedes RLS
scope = self.identity.scope_for_turn()                    # Identity
async with self.db.scoped_session(
    traveler_id=...,
    authorization=scope.authorization,
) as tx:                                                   # Grant, then RLS
    runtime_session = self.agentcore_runtime.session_for_turn(...)  # Runtime
    self.agentcore_memory.list_recent_turns(...)          # Memory (read)
    packages, _ = await self._search_packages(...)        # Gateway MCP
    await self.traveler_memory.persist_turn(...)          # Aurora write
    self.agentcore_memory.record_turn(...)                # AgentCore mirror
```
**Say:** "Strands owns the reasoning loop. AgentCore isolates, remembers, and
exposes tools. Aurora authorizes the workload-to-traveler claim, stores durable
truth, applies RLS, and keeps the audit trail."

**Optional live AgentCore provisioning (@aws/agentcore CLI):**
```bash
npm install -g @aws/agentcore
cd meridian/meridian_agentcore
agentcore add memory --name meridian-session --strategies SEMANTIC
agentcore add gateway --name meridian-aurora --authorizer-type AWS_IAM
agentcore deploy -y && cd .. && python scripts/sync_agentcore_env.py --write
```

## Phase 5 — Orchestration · `orchestration_05/workflow.py`

```python
class WorkflowState(TypedDict, total=False):
    query: str; traveler_id: str; intent: str
    packages: List[Any]; response: str; activities: List[Dict[str, Any]]

builder = StateGraph(WorkflowState)
builder.add_node("classify", self._node_classify)
builder.add_node("search", self._node_search)
builder.add_node("availability", self._node_availability)
builder.add_node("memory_recall", self._node_memory_recall)
builder.add_node("synthesize", self._node_synthesize)
builder.add_conditional_edges("classify", lambda s: s["intent"], {...})
# edge OUT of search is conditional: intent=="plan" → availability, else → synthesize
return builder.compile(checkpointer=PostgresSaver.from_conn_string(dsn))  # Aurora
```
**Say:** "LangGraph doesn't replace Strands — it orchestrates *functions* that reuse
Phase 3 search and Phase 4 memory. The win is explicit edges and PostgresSaver
checkpoints in Aurora."
**Env:** `LANGGRAPH_CHECKPOINT_DSN` → PostgresSaver; unset → in-process MemorySaver.

## Files to have open (in order)

1. `backend/agent_catalog.py` · 2. `backend/routers/chat.py` · 3. `sql_01/agent.py` ·
4. `mcp_02/agent.py` · 5. `retrieval_03/supervisor.py` · 6. `retrieval_03/search_agent.py` ·
7. `production_04/memory_agent.py` · 8. `production_04/concierge.py` ·
9–12. `agentcore/{runtime,gateway,memory,identity}.py` · 13. `orchestration_05/workflow.py`

## FAQ for the room

**Q: Why not Strands for everything?** Strands excels at LLM-driven tool loops. For
auditable branching, approval gates, or durable workflow state, LangGraph gives you a
graph you can inspect and resume.

**Q: Why not AgentCore for Phase 5?** You'd repeat Phase 4. Better story: AgentCore in 4
(identity + memory), LangGraph in 5 (workflow control plane).

**Q: Are the `@tool` decorators real?** Yes — `from strands import tool`. I/O tools are
async; the agent binds the methods via `Agent(tools=[...])`.

**Q: Why does the trace show duplicate memory calls?** Phase 4 runs LLM-driven recall,
then materializes the same Aurora reads for structured search context. Cost negligible;
the trace shows both paths honestly.

**Q: AgentCore Memory *and* Aurora — redundant?** No. AgentCore is the managed session
layer; Aurora is the durable system of record (preferences + embeddings, RLS-scoped).
`persist_turn` bridges them.

---

# DRY-RUN CHECKLIST

- [ ] Spine query fails in Phase 1 & 2, lands in Phase 3 (Tuscany / Amalfi / Douro + scores)
- [ ] The October Tokyo recall prompt fails honestly in Phase 3, then lands in Phase 4
- [ ] Phase 4 shows ALLOW Alex, DENY Jordan, RLS filtering, AgentCore Memory, and audit rows
- [ ] Phase 5 "plan" prompt shows search → availability with a checkpoint between nodes
- [ ] Tool name reads `hybrid_search_tool` everywhere (no stale `semantic_search_tool`)
- [ ] Seeded facts (Alex Morgan: allergy, JFK, boutique) appear from Aurora, not the prompt
