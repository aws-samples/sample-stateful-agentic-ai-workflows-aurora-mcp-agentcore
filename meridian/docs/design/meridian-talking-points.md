# Meridian — Chalk-talk script (three acts)

The story has a shape: **build the agent, make it intelligent and personal,
make it durable**. Five phases live inside three acts. Each act has a
turning point. The whole talk hangs on one query — *"a slow week somewhere
we can drink good wine, Jordan can't do red-eyes"* — that fails twice,
succeeds once, and gets remembered forever after.

> **Source-of-truth:** every tool name, signature, and SQL example below
> matches `backend/agents/{sql_01, mcp_02, retrieval_03, production_04,
> orchestration_05}/`. Updated for v3 (Cohere Rerank 3.5 added to Phase 3).

Total time: **~60 minutes**, paced 5 / 7 / 5 minute sub-phases inside
**12-minute / 14-minute / 8-minute acts**, plus 2 minutes of intro and
the close.

---

## The chorus query

This is the one query you're going to type three times. It's the spine.

> *"A slow week somewhere we can drink good wine — Jordan can't do red-eyes."*

- **Phase 1 (SQL):** returns nothing. `ILIKE '%slow week%'` matches no row.
- **Phase 2 (MCP):** returns nothing. Same gap, different delivery.
- **Phase 3 (Retrieval):** returns Tuscan Vineyards / Douro / Provence.
  *That's the moment of release.*

Rehearse the moment: type, hit enter, watch nothing come back. Shrug,
move on. Say *"this is going to matter in fifteen minutes."* Don't promise
the fix yet. The audience will hold the tension for you.

---

# ACT I — Build the agent shape (≈ 12 min)

**Goal of the act.** By the end, the audience knows what an agent is, what
tools are, and that the whole thing runs on Aurora through `RDS Data API`.
They should be holding *one* question: *"this is fine for keyword filters
— what about real language?"*

**The setup.** Open in the live concierge. Show the workspace. Say:
*"Same Aurora, same Strands SDK, same `@tool` pattern across all five
phases. The only thing changing is **how** the agent reaches the data and
**how much state** it carries."* That sentence is the trellis everything
else hangs on.

---

## Phase 1 · SQL — the foundation (≈ 5 min)

`agents/sql_01/agent.py`

**Open with.** *"This is the smallest possible agent that talks to Aurora.
A Strands `Agent`, five `@tool` methods, and `RDS Data API`."*

Show the four boxes: User → Agent → Tools → Aurora.

**Tools** (all `@tool`, all `async`, all hit `trip_packages`):

- `_search_trip_packages(query: str, trip_type: str | None, limit: int = 5)` — `ILIKE` keyword search across `name / description / operator / destination`
- `_lookup_trip_package(package_id: str)`
- `_check_departure_availability(package_id: str, duration: str | None)`
- `_calculate_booking_total(items: list[dict])`
- `_process_booking(traveler_id: str, items: list[dict])`

**Demo — works, then fails.** Type two queries.

1. **Works** — *"Show me Beach & Resort packages under $1500"*. Trace shows the SQL `WHERE trip_type = :t AND price_per_person <= :p`. Three results come back.
2. **First failure** — *the chorus query*. *"A slow week somewhere we can drink good wine — Jordan can't do red-eyes."* The trace shows the same `ILIKE` search; nothing matches. **Pause.** *"Hold this. We'll come back to it."*

**Talking points (3 sentences):**

> *"Strands gives the LLM the schema for each `@tool`. Bedrock picks
> which tool to call. RDS Data API is how we reach Aurora — no connection
> pool, IAM-auth, the same path every later phase reuses. Phase 1's tools
> are keyword-only, so the agent can find what we name and not what we
> mean."*

**Bridge to Phase 2** *(one sentence, said while clicking past the
divider)*:

> *"Three teams want this catalog. They shouldn't all hand-write SQL.
> Who owns the tools?"*

---

## Phase 2 · MCP — the contract layer (≈ 5 min)

`agents/mcp_02/agent.py`

**Open with.** *"Same SQL. Different delivery. The tools come from
`postgres-mcp-server` instead of methods we wrote."*

The change in one slide:

```python
self.mcp_client = MCPClient(
    server_name="postgres-mcp-server",
    args=["awslabs.postgres-mcp-server@latest"])
mcp_tools = self.mcp_client.list_tools_sync()  # → run_query, get_schema, …
self.agent = Agent(model=self.model, tools=mcp_tools)
```

That's it. The agent **discovers** tools at runtime from a versioned MCP
server. IAM-authed, schema-typed, portable.

**Demo — discovery, then the same gap.**

1. *"Show me what tables Aurora has."* Triggers `get_schema`. Tools the
   agent didn't even know existed at startup. *Pause on this — the
   audience hasn't seen runtime tool discovery before.*
2. **The chorus query, second take.** *"A slow week somewhere we can
   drink good wine — Jordan can't do red-eyes."* Still nothing.
   *"Changing how the tools are wired didn't change what they can find.
   The interface got better. The intelligence didn't."*

**Talking points:**

> *"MCP changes the **interface**, not the **intelligence**. Tools are
> versioned and IAM-authed; three teams can share `postgres-mcp-server`
> without rebuilding it. But the agent still issues SQL, and the SQL is
> still keyword-based. To get past the keyword wall, we need
> embeddings."*

**Bridge to Act II:**

> *"The interface is portable. But every query is still keyword-based.
> We need the agent to understand what we **mean**, not what we type."*

---

# ACT II — Make it intelligent and personal (≈ 14 min)

**Goal of the act.** By the end, the audience believes the agent
understands them and that production-shape security is a solved problem.
They should be holding the next question: *"this works for one turn —
what about a trip that lives for weeks?"*

---

## Phase 3 · Retrieval — the chorus pays off (≈ 7 min)

`agents/retrieval_03/`

**Open by typing the chorus query a third time.** Say nothing. Wait for
results.

> *"Romantic week in Europe under $3k"* → Tuscany, Provence, Lake Como.
>
> Or, the demo prompt: *"A slow week somewhere we can drink good wine
> — Jordan can't do red-eyes."* → wine country trips, refundable.

**Let it land.** Don't explain yet. Let the audience feel the gap close.

**Then explain — but only after the win.**

```text
query  ──► embed (Cohere v4, 1024d)
                   │
trip_packages ───► hybrid candidates (pgvector + tsvector)
                   │
                   ▼
               Cohere Rerank 3.5 → top K
```

Three layers, in order:
1. **Embed** the query with Cohere v4 (1024 dims).
2. **Hybrid candidates** from pgvector (cosine on `embedding`) +
   tsvector (`ts_rank` on `search_vector`). Pull ~25 candidates.
3. **Rerank** with Cohere Rerank 3.5 — re-scores the candidates against
   the original natural-language query and returns the top K.

**Architecture: a supervisor + 3 specialists.**

- `RetrievalAgent` (supervisor) — three delegation `@tool` methods:
  `_delegate_to_search`, `_delegate_to_package`, `_delegate_to_booking`
- `SearchAgent` — `_semantic_search_tool(query, limit)`
- `PackageAgent` — `_get_details_tool`, `_check_availability_tool`
- `BookingAgent` — `_calculate_booking_total_tool`, `_process_booking_tool`

The supervisor isn't routing keywords. It's a Strands `@tool` that
delegates to whichever sub-agent owns the work.

**Demo queries that *only* work here:**

1. **The chorus query** *(third time, lands)*. The hinge of the talk.
2. *"Family-friendly beach resort with snorkeling"* — multi-concept; show
   both pgvector and the rerank fixing the order.
3. *"Tokyo culture trip"* — works on both keyword *and* semantic, useful
   to show *embeddings are additive, not a replacement*.

**Talking points:**

> *"Three things change at once: a query embedding, a hybrid index, and
> a reranker. Hybrid search picks the **candidates**. The reranker
> picks the **order**. The supervisor picks the **specialist**. None of
> the specialists share code with each other; they share Aurora."*

**What to point at in the trace:**

- `data` span — `cohere.embed_v4` · 1024d
- `tool` span — hybrid candidates with `ORDER BY embedding <=> :q`
- `tool` span — `Cohere rerank applied · top K hybrid candidates reranked`
- `orchestration` span — `_delegate_to_search` → `SearchAgent`

**Bridge to Phase 4:**

> *"It understands. But it doesn't know **you**. And we can't ship this
> to production with the agent reading any traveler's data."*

---

## Phase 4 · Production — the trust layer (≈ 7 min)

`agents/production_04/`

**Open with.** *"Phase 3 was about intelligence. Phase 4 is about trust.
This is what production looks like — AgentCore Runtime, Gateway, Memory,
Identity, with Aurora RLS scoping every query."*

**Architecture — the AgentCore stack on every turn:**

1. **AgentCore Runtime** — session envelope (`runtimeSessionId`, microVM isolation)
2. **AgentCore Identity** — workload / IAM envelope (security span)
3. **AgentCore Memory** — `list_events` + semantic recall + `create_event` mirror
4. **Aurora RLS tx** — `scoped_session(traveler_id=…)` pins per-traveler scope; `MemoryAgent` `@tools` run inside the tx
5. **AgentCore Gateway** — managed MCP `tools/list` + `tools/call` for trip search
6. `persist_turn` — Aurora write + AgentCore Memory write-back

**Tools (the four memory `@tool` methods Strands binds in):**

- `recall_session_context(conversation_id, limit)` — recent turns from `conversation_messages`
- `recall_traveler_preferences(traveler_id, limit)` — long-term facts from `traveler_preferences`
- `recall_similar_interactions(traveler_id, query, limit)` — pgvector recall over `interaction_embeddings`
- `persist_turn(conversation_id, role, text, embedding)`

**Demo — the persona is loaded from Aurora, not the prompt.**

1. **Headline query** — *"Plan us a Tokyo trip for two in October."* The
   prompt has no party size, no allergies, no budget. Watch the agent
   recall Alex & Jordan, party=2, shellfish allergy, budget cap **before**
   it searches.
2. *"Beach escape under $2500 — remember our food allergies."* The agent
   never asks; it already knows.
3. *"What did we discuss last time about Iceland?"* —
   `recall_similar_interactions`, pgvector across past sessions.

**Talking points (the trust pitch):**

> *"Three things move at once. **AgentCore Runtime** isolates every
> session in a microVM. **AgentCore Memory** mirrors every turn so the
> next session can find it. **Aurora RLS** wraps every query in a tx
> with `scoped_session(traveler_id=…)` — even if a tool tried to read
> outside scope, the policy denies. Every turn writes one row to
> `agent_audit_log` with the IAM principal, the RLS scope, and the rows
> returned. That's the concrete answer to 'how do I securely connect
> LLM agents to my database.'"*

**What to point at in the trace:**

- `orchestration` — `AgentCore Runtime · session start`
- `tool · MCP` — `AgentCore Gateway · tools/call`
- `memory_long` — `recall_traveler_preferences`, with the RLS-scoped
  SQL `WHERE traveler_id = current_setting('app.traveler_id')`
- `data` — `INSERT INTO agent_audit_log` (the receipt)

**Bridge to Act III:**

> *"One turn is solved. But a trip lives for weeks. What happens when we
> want to pause and resume?"*

---

# ACT III — Make it durable (≈ 8 min)

**Goal of the act.** Show that workflows live longer than turns, and
Aurora makes that durable.

---

## Phase 5 · Orchestration — the durability layer (≈ 8 min)

`agents/orchestration_05/workflow.py`

**Open with.** *"Strands picks tools when the LLM picks the call.
LangGraph owns control flow when **we** want it explicit, branchable,
and resumable."*

**The graph (literal, on a slide):**

```text
            ┌─→ search ──────────┐
classify ──┼─→ availability ────┤
            ├─→ memory_recall ──┤
            └─→ plan ────────────┤
                                synthesize → END
```

```python
builder = StateGraph(WorkflowState)
builder.add_node("classify",      self._node_classify)
builder.add_node("search",        self._node_search)
builder.add_node("availability",  self._node_availability)
builder.add_node("memory_recall", self._node_memory_recall)
builder.add_node("synthesize",    self._node_synthesize)
builder.add_conditional_edges("classify", route_intent, {…})
return builder.compile(
    checkpointer=PostgresSaver.from_conn_string(dsn),
)
```

**Demo — multi-step, checkpointed, resumable:**

1. **Headline query** — *"Watch our Tokyo dates and rebook the hotel if
   we slip a week."* Trace: classify=plan → memory_recall → search →
   synthesize, with a PostgresSaver write between every node.
2. *"Plan and hold our anniversary Tuscany trip end-to-end"* — exercises
   the whole graph in one turn.
3. *"Resume the Iceland workflow we paused last month"* — load a thread
   from `langgraph_checkpoints`, advance state. Best demo of the
   *durability* word.

**Talking points:**

> *"PostgresSaver writes the entire `WorkflowState` to Aurora after every
> node. Threads scope by `traveler_id`. Checkpoints scope by `thread_id`.
> Pause a workflow on Tuesday. Resume it Thursday. Same state. Aurora
> makes the workflow as durable as the catalog. Together: AgentCore +
> LangGraph + Strands, all reading and writing the same Aurora cluster."*

**What to point at in the trace:**

- `orchestration` — `_node_classify` → routed intent
- `data` — `PostgresSaver.put` writing to `langgraph_checkpoints`
- The fact that the same `thread_id` would append spans next session.

---

# Closing (≈ 1 min)

> *"Five phases, one Aurora cluster.
>
> Phase 1 established the agent shape — Strands, RDS Data API.
> Phase 2 swapped the tools to MCP — same SQL, portable interface.
> Phase 3 closed the keyword gap — pgvector + tsvector + Cohere Rerank.
> Phase 4 made it production — AgentCore + Aurora RLS + per-turn audit.
> Phase 5 wrapped it in LangGraph — explicit StateGraph, conditional
> edges, PostgresSaver checkpoints in Aurora.
>
> Same Aurora throughout. Same Strands `@tool` pattern. What's changing
> each phase is **how much state** the agent carries and **how much
> governance** sits between it and the database. That's the message."*

---

## Cheat sheet — what to type into the demo

| Phase | Sample prompt | Why it matters |
| ----- | ------------- | -------------- |
| 1 SQL | *"Show me Beach & Resort packages under $1500"* | Filter works. Then try the chorus query — fails. Sets the gap. |
| 1 SQL | *"A slow week somewhere we can drink good wine — Jordan can't do red-eyes."* | **First chorus.** Returns nothing. |
| 2 MCP | *"Show me what tables Aurora has"* | Triggers `get_schema`. Proves runtime tool discovery. |
| 2 MCP | *"A slow week somewhere we can drink good wine…"* | **Second chorus.** Same nothing. The interface didn't fix it. |
| 3 Retrieval | *"A slow week somewhere we can drink good wine…"* | **Third chorus.** Lands. Tuscany / Douro / Provence. The hinge of the talk. |
| 3 Retrieval | *"Family-friendly beach resort with snorkeling"* | Show the rerank fixing the order. |
| 4 Production | *"Plan us a Tokyo trip for two in October"* | Persona loads from Aurora, not the prompt. |
| 4 Production | *"What did we discuss last time about Iceland?"* | `recall_similar_interactions` — pgvector across sessions. |
| 5 Orchestration | *"Watch our Tokyo dates and rebook if we slip a week"* | Multi-step, checkpointed, resumable. |
| 5 Orchestration | *"Resume the Iceland workflow we paused last month"* | The durability proof. |

---

## Five things the audience leaves with

1. **Aurora is one thing across all five phases** — the catalog, the
   memory store, the embedding home, the checkpoint store, the audit
   log.
2. **MCP is the contract layer** — Phase 2 carries SQL through it;
   Phase 4 carries trip search through AgentCore Gateway the same way.
3. **Embeddings unlock language; reranking fixes the order** — Phase 3
   is hybrid candidates plus a reranker, not just "vector search."
4. **Memory + RLS is the production answer** — AgentCore + per-traveler
   RLS scoping + per-turn audit. That's the concrete answer to "how do
   I securely connect LLM agents to my database."
5. **LangGraph is the durability story** — Phase 5 makes control flow a
   first-class artifact: branchable, checkpointed in Aurora, resumable
   weeks later.

---

## Five bridge sentences (memorize these)

These are the seams of the talk. Every transition. Rehearse them more
than anything else.

1. *"Three teams want this catalog. They shouldn't all hand-write SQL.
   Who owns the tools?"* → **Phase 2**
2. *"The interface is portable. But every query is still keyword-based.
   We need the agent to understand what we **mean**."* → **Act II / Phase 3**
3. *"It understands. But it doesn't know **you**. And we can't ship this
   to production with the agent reading any traveler's data."* → **Phase 4**
4. *"One turn is solved. But a trip lives for weeks. What happens when we
   want to pause and resume?"* → **Act III / Phase 5**
5. *"Same Aurora throughout. Five phases, one substrate."* → **Close**
