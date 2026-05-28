# Meridian — Chalk-talk script (three acts)

The story has a shape: **build the agent, make it intelligent and personal,
make it durable**. Five modes live inside three acts. Each act has a
turning point. The spine of the talk is one query — *"a romantic slow week
somewhere with great wine"* — that fails twice, then lands; and a second
beat — *"what did we discuss last time?"* — that fails once, then is
remembered forever after.

> **Source-of-truth:** every tool name, signature, and SQL example below
> matches `backend/agents/{sql_01, mcp_02, retrieval_03, production_04,
> orchestration_05}/`, and every demo prompt matches the live pills in
> `frontend/src/showcase/lib/showcaseAdapters.ts`. Model: Bedrock
> **Claude Opus 4.8** (`global.anthropic.claude-opus-4-8`), fallback chain
> Opus 4.8 → Sonnet 4.6 → Haiku 4.5. Retrieval ranks with **Cohere Embed
> v4** + **Cohere Rerank 3.5** (`us.cohere.rerank-v3-5:0`).

Total time: **~60 minutes**, paced 5 / 7 / 5 minute sub-modes inside
**12-minute / 14-minute / 8-minute acts**, plus 2 minutes of intro and
the close.

---

## The traveler

Everything personal in Act II + III hangs on one seeded traveler. These
facts live in Aurora `traveler_preferences` / `trip_interactions` for
`trv_meridian_demo` — never in the prompt. They're what the "For you"
panel shows on the right rail.

- **Alex Morgan** — home airport **BOS**, **party of 2**
- **Shellfish allergy** (dining gets vetted)
- **No red-eyes** out of BOS
- **Boutique over chain** lodging
- **Marriott Bonvoy + Delta SkyMiles** loyalty
- **Recent trips:** Tuscany (booked, Feb 2026), Kyoto (held)
- **Tokyo culture trip** Oct 12-19 in motion

---

## The two recurring beats

Two prompts carry the arc. Rehearse both.

**Beat 1 — the intent query (the spine of Act I → II):**

> *"A romantic slow week somewhere with great wine."*

- **SQL:** returns nothing. `ILIKE '%romantic%'` matches no row.
- **MCP:** returns nothing. Same gap, different delivery.
- **Retrieval:** returns Tuscany Wine & Wellness / Amalfi / Douro.
  *That's the moment of release.*

**Beat 2 — the memory query (the hinge of Act II):**

> *"What did we discuss last time? Pick up where we left off."*

- **Retrieval:** fails *honestly* — "I can't recall prior turns; that's
  the next mode." The trace shows zero products and a reasoning span
  naming the limitation. **This is intentional, not a bug.**
- **Production:** lands — recalls the Tokyo thread from AgentCore Memory
  + Aurora, grounded in the seeded preferences.

Rehearse Beat 1's first failure: type it in SQL, hit enter, watch nothing
come back. Shrug. Say *"this is going to matter in fifteen minutes."*
Don't promise the fix. The audience holds the tension for you.

---

# ACT I — Build the agent shape (≈ 12 min)

**Goal of the act.** By the end, the audience knows what an agent is, what
tools are, and that the whole thing runs on Aurora through `RDS Data API`.
They should be holding *one* question: *"this is fine for keyword filters
— what about real language?"*

**The setup.** Open the live concierge `/showcase`. Show the workspace.
Say: *"Same Aurora, same Strands SDK, same `@tool` pattern across all five
modes. The only thing changing is **how** the agent reaches the data and
**how much state** it carries."* That sentence is the trellis everything
else hangs on.

---

## Mode 1 · SQL — the foundation (≈ 5 min)

`agents/sql_01/agent.py`

**Open with.** *"This is the smallest possible agent that talks to Aurora.
A Strands `Agent`, five `@tool` methods, and `RDS Data API`."*

Show the four boxes: User → Agent → Tools → Aurora.

**Tools** (all `@tool`, all `async`, all hit `trip_packages`):

- `_search_trip_packages(query, trip_type, limit=5)` — `ILIKE` keyword search across `name / description / operator / destination`
- `_lookup_trip_package(package_id)`
- `_check_departure_availability(package_id, duration)`
- `_calculate_booking_total(items)`
- `_process_booking(traveler_id, items)`

**Demo — works, then fails. (Live pills, in order.)**

1. **Works** — *"City breaks under $2000"* (or *"Beach & Resort trips
   under $2500"*). Trace shows `WHERE trip_type = :t AND price_per_person
   <= :p`. Results come back.
2. **First failure** — *Beat 1: "A romantic slow week somewhere with great
   wine."* Same `ILIKE` search; nothing matches. The no-results reply is
   polished through the concierge LLM so even the failure reads human.
   **Pause.** *"Hold this. We'll come back to it."*

**Talking points (3 sentences):**

> *"Strands gives the LLM the schema for each `@tool`. Bedrock picks
> which tool to call. RDS Data API is how we reach Aurora — no connection
> pool, IAM-auth, the same path every later mode reuses. The tools are
> keyword-only, so the agent finds what we **name**, not what we **mean**."*

**Bridge to Mode 2** *(said while clicking past the divider)*:

> *"Three teams want this catalog. They shouldn't all hand-write SQL.
> Who owns the tools?"*

---

## Mode 2 · MCP — the contract layer (≈ 5 min)

`agents/mcp_02/agent.py`

**Open with.** *"Two MCP servers in one agent turn. `postgres-mcp-server`
carries the SQL. A custom `meridian-concierge` server adds domain tools
that SQL alone can't express."*

The custom server's tools: `compare_packages`, `currency_convert`,
`seasonal_price_band`, `region_inventory`, `loyalty_balance`. The agent
**discovers** all of them at runtime from versioned MCP servers — IAM-
authed, schema-typed, portable.

**Demo — domain tools, then the same intent gap. (Live pills, in order.)**

1. *"Compare our top trips and show prices in EUR."* Routes to
   `compare_packages` + `currency_convert` — a stratified one-per-trip-type
   comparison, FX-converted. *Pause here — this is something a single SQL
   query doesn't do cleanly.*
2. *"What is the cheapest month to visit Tokyo?"* Triggers
   `seasonal_price_band` — real low / median / high spread across the
   Tokyo packages. Pure domain logic, no catalog search.
3. **Beat 1, second take.** *"A romantic slow week somewhere with great
   wine."* Still nothing. *"Better tools, richer domain logic — but the
   intent gap is untouched. The interface got better. The intelligence
   didn't."*

**Talking points:**

> *"MCP changes the **interface**, not the **intelligence**. Tools are
> versioned and IAM-authed; teams share `postgres-mcp` and a custom
> domain server without rebuilding them. But matching a **mood** —
> 'romantic', 'slow', 'great wine' — needs embeddings, not tools."*

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

## Mode 3 · Retrieval — the spine pays off (≈ 7 min)

`agents/retrieval_03/`

**Open by typing Beat 1 a third time.** Say nothing. Wait for results.

> *"A romantic slow week somewhere with great wine"* → Tuscany Wine &
> Wellness, Amalfi Coast Villa Week, Douro / Tokyo Ryokan as close
> alternatives — each with a `semantic_match` score.

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
3. **Rerank** with Cohere Rerank 3.5 (`us.cohere.rerank-v3-5:0`,
   cross-region inference profile) — re-scores candidates against the
   original natural-language query and returns the top K.

**Architecture: a supervisor + specialists.**

- `RetrievalAgent` (supervisor) — Strands `@tool` delegation:
  `_delegate_to_search`, `_delegate_to_package`, `_delegate_to_booking`
- `SearchAgent` — `_semantic_search_tool(query, limit)`
- `PackageAgent` — `_get_details_tool`, `_check_availability_tool`
- `BookingAgent` — `_calculate_booking_total_tool`, `_process_booking_tool`

**Demo — the live pills, in order:**

1. **Beat 1** *(third time, lands)*. The hinge of Act I → II.
2. *"Family-friendly beach resort with snorkeling"* — multi-concept; show
   both pgvector and the rerank fixing the order (Costa del Sol, Cancún,
   Maldives).
3. **Beat 2, first take — the honest failure.** *"What did we discuss
   last time? Pick up where we left off."* Retrieval returns **nothing**
   and says so plainly: *"I'm pure retrieval — I have no memory of prior
   turns. That's the next mode."* The trace shows a reasoning span naming
   the limitation, zero product cards. **This is the deliberate setup for
   Production.** Don't apologize for it — it's the whole point.

**Talking points:**

> *"Three things change at once: a query embedding, a hybrid index, and
> a reranker. Hybrid search picks the **candidates**. The reranker picks
> the **order**. The supervisor picks the **specialist**. None of the
> specialists share code; they share Aurora. But notice — it understands
> the request, and it still has no idea who **you** are."*

**What to point at in the trace:**

- `data` span — `cohere.embed_v4` · 1024d
- `tool` span — hybrid candidates with `ORDER BY embedding <=> :q`
- `tool` span — `Cohere rerank applied · top K hybrid candidates reranked`
- `orchestration` span — `_delegate_to_search` → `SearchAgent`
- On Beat 2: `reasoning` span — *"Memory-recall prompt detected — no
  conversation store in this mode"*

**Bridge to Mode 4:**

> *"It understands what you mean. It just failed to remember what we
> discussed — because it has nowhere to remember it. And we can't ship
> this to production with the agent reading any traveler's data."*

---

## Mode 4 · Production — the trust + memory layer (≈ 7 min)

`agents/production_04/`

**Open with.** *"Retrieval was about intelligence. Production is about
trust and memory. This is what production looks like — AgentCore Runtime,
Gateway, Memory, Identity, with Aurora RLS scoping every query."*

**Architecture — the AgentCore stack on every turn:**

1. **AgentCore Runtime** — session envelope (`runtimeSessionId`, microVM isolation)
2. **AgentCore Identity** — workload / IAM envelope (security span)
3. **AgentCore Memory** — `list_events` (SHORT_TERM session recall) + `create_event` mirror
4. **Aurora RLS tx** — `scoped_session(traveler_id=…)` pins per-traveler scope; `MemoryAgent` `@tools` run inside the tx
5. **AgentCore Gateway** — managed MCP `tools/list` + `tools/call` for trip search
6. `persist_turn` — Aurora write + AgentCore Memory write-back

**Tools (the four memory `@tool` methods Strands binds in):**

- `recall_session_context(conversation_id, limit)` — recent turns from `conversation_messages`
- `recall_traveler_preferences(traveler_id, limit)` — long-term facts from `traveler_preferences`
- `recall_similar_interactions(traveler_id, query, limit)` — pgvector recall over `trip_interactions`
- `persist_turn(conversation_id, role, text, embedding)`

**Demo — a single Tokyo storyline. Run the pills IN ORDER; the recall
beat only lands because the first turn seeded the thread.**

1. **Seed the thread** — *"Tokyo culture trip for two — boutique stays,
   local food, walkable neighborhoods."* The prompt carries no allergy,
   no airport, no loyalty. Watch the agent weave in shellfish allergy,
   BOS no-red-eyes, boutique-over-chain, party of 2 — all recalled from
   Aurora **before** it answers. `persist_turn` writes this turn into
   `conversation_messages` + `trip_interactions`.
2. **Beat 2, second take — it lands.** *"What did we discuss last time?
   Pick up where we left off."* Now `recall_session_context` +
   `recall_similar_interactions` return the Tokyo thread you just seeded,
   and the reply picks it up by name. *This is the exact prompt that
   failed honestly one mode ago.* Point back to that moment.
3. **Multi-intent stretch** — *"Plan our October Tokyo trip — find open
   dates, pick a Marriott property, and hold a Kyoto side trip."* Strands
   chains three jobs implicitly in one Bedrock turn. The reply honestly
   notes none of the catalog packages are Marriott-branded and pivots to
   a real Bonvoy property — grounded, not hallucinated. *This is the
   setup for Act III.*

**Talking points (the trust pitch):**

> *"Three things move at once. **AgentCore Runtime** isolates every
> session in a microVM. **AgentCore Memory** mirrors every turn so the
> next session can find it. **Aurora RLS** wraps every query in a tx with
> `scoped_session(traveler_id=…)` — even if a tool tried to read outside
> scope, the policy denies. Every turn writes one audit row with the IAM
> principal, the RLS scope, and the rows returned. That's the concrete
> answer to 'how do I securely connect LLM agents to my database.'"*

**What to point at in the trace:**

- `orchestration` — `AgentCore Runtime · session envelope`
- `tool · MCP` — `AgentCore Gateway · tools/call → semantic_trip_search`
- `memory_long` — `recall_traveler_preferences`, RLS-scoped
  `WHERE traveler_id = current_setting('app.traveler_id')`
- `memory_short` — `AgentCore Memory · create_event` (the mirror write)

**Bridge to Act III:**

> *"That last prompt asked for three things in one breath — find dates,
> pick a hotel, hold a side trip. Strands chained them in a single turn,
> but the routing was invisible. What if we want each step explicit,
> branchable, and resumable weeks later?"*

---

# ACT III — Make it durable (≈ 8 min)

**Goal of the act.** Show that workflows live longer than turns, and
Aurora makes that durable.

---

## Mode 5 · Workflow — the durability layer (≈ 8 min)

`agents/orchestration_05/workflow.py`

**Open with.** *"Strands picks tools when the LLM picks the call.
LangGraph owns control flow when **we** want it explicit, branchable,
and resumable. Same multi-intent Tokyo plan from a moment ago — now watch
it route through named, checkpointed nodes."*

**The graph (literal, on a slide):**

```text
            ┌─→ search ──────────┐
classify ──┼─→ availability ────┤
            ├─→ memory_recall ──┤
            └─→ (plan) ──────────┤
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

**Demo — the live pills, in order:**

1. *"What dates are open for Kyoto in November? Show the slots."* Trace:
   classify → availability → synthesize, a `PostgresSaver.put` to
   `langgraph_checkpoints` between every node.
2. *"Refine our last Iceland conversation with a winter focus."* Exercises
   classify → memory_recall → search, loading prior thread state before
   re-searching.
3. *"Compare Kyoto and Tokyo for a 10-day cultural trip."* A two-
   destination prompt — and the honest teaching moment: even LangGraph
   runs a **single** search node here, so it shows *branching ≠ multi-step
   tool composition*. Name that out loud; it's a credibility builder.

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

> *"Five modes, one Aurora cluster.
>
> SQL established the agent shape — Strands, RDS Data API.
> MCP swapped the tools to a portable contract — postgres-mcp plus a
> custom domain server, same Aurora underneath.
> Retrieval closed the keyword gap — pgvector + tsvector + Cohere Rerank.
> Production made it real — AgentCore + Aurora RLS + per-turn audit + the
> memory that turned 'what did we discuss?' from a failure into a recall.
> Workflow wrapped it in LangGraph — explicit StateGraph, conditional
> edges, PostgresSaver checkpoints in Aurora.
>
> Same Aurora throughout. Same Strands `@tool` pattern. What changes each
> mode is **how much state** the agent carries and **how much governance**
> sits between it and the database. That's the message."*

---

## Cheat sheet — what to type into the demo

| Mode | Sample prompt | Why it matters |
| ----- | ------------- | -------------- |
| 1 SQL | *"City breaks under $2000"* | Filter works. Sets the baseline. |
| 1 SQL | *"A romantic slow week somewhere with great wine"* | **Beat 1, first failure.** Returns nothing. |
| 2 MCP | *"Compare our top trips and show prices in EUR"* | `compare_packages` + `currency_convert`. Domain tools beyond SQL. |
| 2 MCP | *"What is the cheapest month to visit Tokyo?"* | `seasonal_price_band` — real low/median/high spread. |
| 2 MCP | *"A romantic slow week somewhere with great wine"* | **Beat 1, second failure.** Better tools didn't fix intent. |
| 3 Retrieval | *"A romantic slow week somewhere with great wine"* | **Beat 1 lands.** Tuscany / Amalfi / Douro. The hinge. |
| 3 Retrieval | *"Family-friendly beach resort with snorkeling"* | Show the rerank fixing the order. |
| 3 Retrieval | *"What did we discuss last time? Pick up where we left off."* | **Beat 2 fails honestly.** No memory in this mode → motivates Production. |
| 4 Production | *"Tokyo culture trip for two — boutique stays, local food, walkable neighborhoods"* | Persona loads from Aurora; seeds the thread. |
| 4 Production | *"What did we discuss last time? Pick up where we left off."* | **Beat 2 lands.** Same prompt that just failed — now recalled. |
| 4 Production | *"Plan our October Tokyo trip — find open dates, pick a Marriott property, and hold a Kyoto side trip"* | Multi-intent in one turn. Sets up Workflow. |
| 5 Workflow | *"What dates are open for Kyoto in November? Show the slots."* | Single-branch, checkpointed availability node. |
| 5 Workflow | *"Refine our last Iceland conversation with a winter focus"* | memory_recall → search; loads prior thread state. |
| 5 Workflow | *"Compare Kyoto and Tokyo for a 10-day cultural trip"* | Honest: branching ≠ multi-step composition. |

---

## Five things the audience leaves with

1. **Aurora is one thing across all five modes** — the catalog, the
   memory store, the embedding home, the checkpoint store, the audit log.
2. **MCP is the contract layer** — MCP carries SQL *and* a custom domain
   server in Mode 2; Production carries trip search through AgentCore
   Gateway the same way.
3. **Embeddings unlock language; reranking fixes the order** — Retrieval
   is hybrid candidates plus a reranker, not just "vector search."
4. **Memory + RLS is the production answer** — AgentCore + per-traveler
   RLS scoping + per-turn audit. The same prompt that failed in Retrieval
   ("what did we discuss?") succeeds in Production. That's the concrete
   answer to "how do I securely connect LLM agents to my database."
5. **LangGraph is the durability story** — Workflow makes control flow a
   first-class artifact: branchable, checkpointed in Aurora, resumable
   weeks later.

---

## Five bridge sentences (memorize these)

These are the seams of the talk. Rehearse them more than anything else.

1. *"Three teams want this catalog. They shouldn't all hand-write SQL.
   Who owns the tools?"* → **Mode 2**
2. *"The interface is portable. But every query is still keyword-based.
   We need the agent to understand what we **mean**."* → **Act II / Mode 3**
3. *"It understands what you mean — it just failed to remember what we
   discussed, because it has nowhere to. And we can't ship this reading
   any traveler's data."* → **Mode 4**
4. *"Three things in one breath — find dates, pick a hotel, hold a side
   trip. Strands chained them, but the routing was invisible. What if we
   want it explicit and resumable?"* → **Act III / Mode 5**
5. *"Same Aurora throughout. Five modes, one substrate."* → **Close**
