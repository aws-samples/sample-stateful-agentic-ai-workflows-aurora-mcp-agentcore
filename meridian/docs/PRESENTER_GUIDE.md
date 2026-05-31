# Meridian — Presenter Guide

One guide for the whole talk. **Part 1** is the narration script (what to *say*,
on stage). **Part 2** is the code reference (what to *show* in the IDE — files,
snippets, env knobs, FAQ). The dry-run checklist is at the end.

> **Source of truth:** every tool name, signature, and SQL example matches
> `backend/agents/{sql_01, mcp_02, retrieval_03, production_04, orchestration_05}/`,
> and every demo prompt matches the live pills in
> `frontend/src/showcase/lib/showcaseAdapters.ts`. Model: Bedrock **Claude Opus 4.8**
> (`global.anthropic.claude-opus-4-8`), fallback chain Opus 4.8 → Sonnet 4.6 → Haiku 4.5.
> Retrieval ranks with **Cohere Embed v4** + **Cohere Rerank 3.5** (`us.cohere.rerank-v3-5:0`).

**Shape of the talk:** build the agent (Phases 1–2), make it intelligent and personal
(Phases 3–4), make it durable (Phase 5). The spine is one query — *"a romantic slow week
somewhere with great wine"* — that fails twice then lands; and a second beat — *"what did
we discuss last time?"* — that fails once then is remembered. ~60 minutes plus intro and
close.

---

# PART 1 — NARRATION SCRIPT

## The traveler

Everything personal in Phases 3–5 hangs on one seeded traveler. These facts live in
Aurora `traveler_preferences` / `trip_interactions` for `trv_meridian_demo` — never in
the prompt. They're what the "For you" panel shows.

- **Alex Morgan** — home airport **BOS**, **party of 2**
- **Shellfish allergy** (dining gets vetted)
- **No red-eyes** out of BOS
- **Boutique over chain** lodging
- **Marriott Bonvoy + Delta SkyMiles** loyalty
- **Recent trips:** Tuscany (booked, Feb 2026), Kyoto (held)
- **Tokyo culture trip** Oct 12–19 in motion

## The two recurring beats

**Beat 1 — the intent query (spine of Phases 1–3):**
> *"A romantic slow week somewhere with great wine."*
- **SQL:** nothing. `ILIKE '%romantic%'` matches no row.
- **MCP:** nothing. Same gap, different delivery.
- **Retrieval:** Tuscany Wine & Wellness / Amalfi / Douro. *The moment of release.*

**Beat 2 — the memory query (hinge of Phases 3–4):**
> *"What did we discuss last time? Pick up where we left off."*
- **Retrieval:** fails *honestly* — "I can't recall prior turns; that's the next phase." Zero products, a reasoning span naming the limitation. **Intentional, not a bug.**
- **Production:** lands — recalls the Tokyo thread from AgentCore Memory + Aurora.

Rehearse Beat 1's first failure: type it in SQL, hit enter, watch nothing come back.
Shrug. Say *"this is going to matter in fifteen minutes."* Don't promise the fix — the
audience holds the tension for you.

---

**Setup (before Phase 1).** Open the live concierge `/showcase`. Say: *"Same Aurora,
same Strands SDK, same `@tool` pattern across all five phases. The only thing changing
is **how** the agent reaches the data and **how much state** it carries."* By the end of
Phase 2 they should know what an agent is, what tools are, and that it all runs on Aurora
through RDS Data API — holding one question: *"fine for keyword filters — what about real
language?"*

## Phase 1 · SQL — the foundation (≈ 5 min) · `agents/sql_01/agent.py`

**Open with:** *"The smallest possible agent that talks to Aurora. A Strands `Agent`,
five `@tool` methods, and RDS Data API."* Show four boxes: User → Agent → Tools → Aurora.

Tools (all `@tool`, async, hit `trip_packages`): `_search_trip_packages`,
`_lookup_trip_package`, `_check_departure_availability`, `_calculate_booking_total`,
`_process_booking`.

**Demo (live pills, in order):**
1. **Works** — *"City breaks under $2000."* Trace shows `WHERE trip_type = :t AND price_per_person <= :p`.
2. **First failure** — *Beat 1.* Same ILIKE search; nothing matches. **Pause.** *"Hold this. We'll come back to it."*

**Talking points:** *"Strands gives the LLM the schema for each `@tool`. Bedrock picks
which to call. RDS Data API is how we reach Aurora — no pool, IAM-auth, the path every
later phase reuses. The tools are keyword-only, so the agent finds what we **name**, not
what we **mean**."*

**Bridge → Phase 2:** *"Three teams want this catalog. They shouldn't all hand-write SQL. Who owns the tools?"*

## Phase 2 · MCP — the contract layer (≈ 5 min) · `agents/mcp_02/agent.py`

**Open with:** *"Two MCP servers in one turn. `postgres-mcp-server` carries the SQL.
A custom `meridian-concierge` server adds domain tools SQL alone can't express."*
Custom tools: `compare_packages`, `currency_convert`, `seasonal_price_band`,
`region_inventory`, `loyalty_balance` — discovered at runtime, IAM-authed, portable.

**Demo:**
1. *"Compare our top trips and show prices in EUR."* → `compare_packages` + `currency_convert`. *Pause — a single SQL query doesn't do this cleanly.*
2. *"What is the cheapest month to visit Tokyo?"* → `seasonal_price_band` (low/median/high).
3. **Beat 1, second take.** Still nothing. *"Better tools, richer domain logic — intent gap untouched. The interface got better. The intelligence didn't."*

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

**Open with:** *"Retrieval was intelligence. Production is trust and memory — AgentCore
Runtime, Gateway, Memory, Identity, with Aurora RLS scoping every query."*

**The AgentCore stack on every turn:**
1. **Runtime** — session envelope (`runtimeSessionId`, microVM isolation)
2. **Identity** — workload / IAM envelope (security span)
3. **Memory** — `list_recent_turns` (session recall) + semantic recall + `create_event` mirror
4. **Aurora RLS tx** — `scoped_session(traveler_id=…)` pins per-traveler scope; memory `@tools` run inside it
5. **Gateway** — managed MCP `tools/list` + `tools/call` for trip search
6. **persist_turn** — Aurora write + AgentCore Memory write-back

**The four memory `@tools`:** `recall_session_context`, `recall_traveler_preferences`,
`recall_similar_interactions`, `persist_turn`. All read/write Aurora; `persist_turn` is
the only writer and writes both Aurora and AgentCore.

**Demo — one Tokyo storyline, IN ORDER:**
1. **Seed the thread** — *"Tokyo culture trip for two — boutique stays, local food, walkable neighborhoods."* Prompt carries no allergy/airport/loyalty; the agent weaves in shellfish allergy, BOS no-red-eyes, boutique-over-chain — all from Aurora **before** answering.
2. **Beat 2, second take — it lands.** Same prompt that failed a phase ago; now `recall_session_context` + `recall_similar_interactions` return the Tokyo thread. *Point back to that failure.*
3. **Multi-intent** — *"Plan our October Tokyo trip — find open dates, pick a Marriott property, hold a Kyoto side trip."* Strands chains three jobs in one turn; honestly pivots to a real Bonvoy property. *Sets up Phase 5.*

**Talking points (trust pitch):** *"AgentCore Runtime isolates every session in a
microVM. AgentCore Memory mirrors every turn. Aurora RLS wraps every query in a tx with
`scoped_session(traveler_id=…)` — even a tool reading outside scope is denied by policy.
Every turn writes one audit row: IAM principal, RLS scope, rows returned. That's the
concrete answer to 'how do I securely connect LLM agents to my database.'"*

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

**Demo:**
1. *"What dates are open for Kyoto in November? Show the slots."* → classify → availability → synthesize, a `PostgresSaver.put` between every node.
2. *"Refine our last Iceland conversation with a winter focus."* → classify → memory_recall → search.
3. *"Compare Kyoto and Tokyo for a 10-day cultural trip."* → honest teaching moment: even LangGraph runs a single search node — *branching ≠ multi-step composition.* Name it; it's a credibility builder.

**Talking points:** *"PostgresSaver writes the entire `WorkflowState` to Aurora after
every node. Threads scope by `traveler_id`; checkpoints by `thread_id`. Pause Tuesday,
resume Thursday — same state. Together: AgentCore + LangGraph + Strands, all on one
Aurora cluster."*

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
4. *"Three things in one breath. Strands chained them, but the routing was invisible. What if we want it explicit and resumable?"* → **Phase 5**
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
SQL and logs the query for the trace. The limitation is lexical: 'romantic week in
Europe' doesn't map to a column."
**Live path:** `chat.py` → `phase1_search()` (same SQL, no LLM loop).

## Phase 2 — MCP Agent · `backend/agents/mcp_02/agent.py`

```python
from strands.tools.mcp import MCPClient

self.mcp_client = MCPClient(
    server_name="postgres-mcp-server",
    command="uvx",
    args=["awslabs.postgres-mcp-server@latest"],
)
await self.mcp_client.connect()
mcp_tools = await self.mcp_client.list_tools()   # discovered at runtime
```
**Say:** "Phase 2 swaps the wire protocol. The agent discovers MCP tools at runtime;
IAM + RDS Data API auth stays the same underneath. Plus a custom `meridian-concierge`
FastMCP server for domain tools SQL can't express."
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
# concierge.py — process_turn() runs the 6-step envelope
scope = self.identity.scope_for_turn()                    # Identity
async with self.db.scoped_session(traveler_id=...) as tx: # Aurora RLS pinned
    runtime_session = self.agentcore_runtime.session_for_turn(...)  # Runtime
    self.agentcore_memory.list_recent_turns(...)          # Memory (read)
    packages, _ = await self._search_packages(...)        # Gateway MCP
    await self.traveler_memory.persist_turn(...)          # Aurora write
    self.agentcore_memory.record_turn(...)                # AgentCore mirror
```
**Say:** "Strands owns the reasoning loop. AgentCore Runtime isolates the session,
Gateway exposes Aurora as managed MCP tools, Memory mirrors multi-turn context, Identity
scopes credentials. Aurora stays durable truth with per-traveler RLS."

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
- [ ] "What did we discuss last time?" fails honestly in Phase 3, recalls in Phase 4
- [ ] Phase 4 trace shows the RLS span + AgentCore Memory + per-turn audit row
- [ ] Phase 5 "plan" prompt shows search → availability with a checkpoint between nodes
- [ ] Tool name reads `hybrid_search_tool` everywhere (no stale `semantic_search_tool`)
- [ ] Seeded facts (Alex Morgan: allergy, BOS, boutique) appear from Aurora, not the prompt
