# Meridian — Code Walkthrough Cue Sheet

Exact files + line ranges to keep open while presenting the live code, phase by
phase, in reveal order. Pair this with [`PRESENTER_GUIDE.md`](./PRESENTER_GUIDE.md)
(what to *say*) — this doc is what to *show*.

> All paths are relative to `meridian/`. Line numbers verified against the repo;
> if you edit a file, re-check with `grep -n`. Format per row: **lines** ·
> **what's on screen** · **the one line that sells it**.

**Open these as tabs/splits in reveal order so you move left → right as the talk advances:**

```
backend/agents/sql_01/agent.py:83
backend/agents/mcp_02/agent.py:82      backend/mcp/concierge_server.py:52
backend/agents/retrieval_03/search_agent.py:111   backend/agents/retrieval_03/supervisor.py:95
backend/agents/production_04/concierge.py:180      backend/agents/production_04/memory_agent.py:114
backend/agents/orchestration_05/workflow.py:390
```

**The three "land the point" lines to bookmark** (jump-to-line is `Cmd/Ctrl-G`):
`search_agent.py:264` (rerank fusion) · `concierge.py:265` (short RLS read unit) · `workflow.py:557` (the plan branch).

---

## Phase 1 — SQL · `backend/agents/sql_01/agent.py` (446 lines)

**Open to line 83.**

| Lines | Show | Say |
|---|---|---|
| **83–94** | `Agent(model=BedrockModel(...), tools=[...], system_prompt=...)` | "Five `@tool` methods, one agent. Bedrock reads the docstrings and picks which to call." |
| **180–228** | `@tool _search_trip_packages` — the keyword search | "This is the one that fails our wine query — it's `ILIKE` keyword matching." |

The five tools live at lines **142, 180, 230, 285, 342** if someone wants to see them all.

---

## Phase 2 — MCP · two files

### A. `backend/agents/mcp_02/agent.py` (265 lines) — open to line 82

| Lines | Show | Say |
|---|---|---|
| **82–90** | `MCPClient(... args=["awslabs.postgres-mcp-server@1.0.9"])` | "Same Aurora — now reached through a versioned, IAM-authed MCP server instead of hand-written SQL." |
| **112–113** | `connect()` + `list_tools()` | "Tools are discovered at runtime, not hard-coded." |

> The server is pinned to `@1.0.9` (matches the live runtime client at
> `backend/mcp/mcp_client.py:118`). `@latest` drifted to auto-discovering the
> Secrets Manager secret, which fails for a Serverless v2 secret with a random
> suffix — the pin avoids that on stage.

### B. `backend/mcp/concierge_server.py` — the custom domain server — open to line 52

| Lines | Show | Say |
|---|---|---|
| **52** | `mcp = FastMCP("meridian-concierge")` | "And a *custom* MCP server for domain logic SQL can't express." |
| **91 · 133 · 188 · 234 · 272** | `compare_packages` · `seasonal_price_band` · `region_inventory` · `currency_convert` · `loyalty_balance` | "Five domain tools — this is what the 'compare in EUR' and 'cheapest month' demos call." |

---

## Phase 3 — Retrieval · two files

### A. `backend/agents/retrieval_03/supervisor.py` (351 lines) — open to line 95

| Lines | Show | Say |
|---|---|---|
| **94–97** | `Agent(tools=[...])` — three delegation tools | "The supervisor sees three tools, each routing to a specialist." |
| **148 · 185 · 224** | `_delegate_to_search` / `_delegate_to_package` / `_delegate_to_booking` | "Bedrock picks the specialist — same `@tool` pattern, one level up." |

### B. `backend/agents/retrieval_03/search_agent.py` (320 lines) — **the money slide; open to line 111**

| Lines | Show | Say |
|---|---|---|
| **71** | `tools=[self._hybrid_search_tool]` | "From the agent's view it's **one** tool — `_hybrid_search_tool` (note the underscore)." |
| **111–125** | the `@tool` wrapper → calls `hybrid_search()` | "The whole pipeline hides inside it." |
| **139–156** | embed step (Cohere Embed v4, 1024d) | "Embed the query." |
| **161–187** | `candidate_limit = max(limit * multiplier, 25)` + `semantic_trip_search(%s::vector, %s::integer)` | "pgvector semantic arm — about 25 candidates." |
| **192–214** | `websearch_to_tsquery` + `ts_rank` lexical arm | "Full-text precision arm, in parallel." |
| **219–266** | merge/dedup → `rerank_documents(...)` (Cohere Rerank 3.5) | "Fuse both pools, then the cross-encoder reranks to top K." |

The reranker model id isn't in this file — the call delegates to
`embedding_service.rerank_documents()` at line **264**; the id lives in config.

---

## Phase 4 — Production · two files

### A. `backend/agents/production_04/concierge.py` — open to line 180 (`process_turn`)

| Lines | Show | Say |
|---|---|---|
| **255–387** | prepared query vector + first `scoped_session(...)` | "Prepare the embedding first, then authorize and run a short RLS read unit." |
| **422–507** | Runtime, AgentCore Memory, and Gateway | "The read unit is already committed; no DB transaction waits on an external service." |
| **552–621** | second `scoped_session(...)` + `persist_turn` + audit | "Reauthorize, write, and commit in a separate short RLS unit." |
| **626–653** | `record_turn()` after commit | "Mirror to AgentCore Memory as a separate consistency domain." |

> The trace panel now shows **all 18 spans** for a Phase 4 turn (Identity → Runtime → Memory r/w → Aurora `@tools` → Gateway → persist → polish) — the AgentCore spans reach the UI via the `collect` callback wired in `process_turn`.

### C. `backend/db/rds_data_client.py` — `scoped_session()` (authorization + RLS)

| Lines | Show | Say |
|---|---|---|
| **check_traveler_authorization** | lookup in `traveler_identity_bindings` | "RLS trusts a traveler ID. This proves the authenticated subject may claim it first." |
| **381** | `set_config('app.current_traveler_id', …, true)` | "Pin the traveler into a transaction-local GUC — the RLS policy's input." |
| **425** | `SET LOCAL ROLE meridian_app` | "**The catch:** our Data API secret maps to the master role, which on this cluster isn't subject to RLS (row_security_active() = false — not superuser/BYPASSRLS, just the master). We step down to a least-privilege role so the policy bites. (Production: give the app its own non-master secret.)" |

### D. RLS probe — `backend/routers/diagnostics.py` (`/api/diagnostics/rls-probe`)

The Phase-4 **RLS tab** proves three layers: workload identity, `ALLOW Alex` /
`DENY Jordan` authorization, then the same `COUNT(*)` scoped vs unscoped plus
the live `pg_policies` USING clause.

### B. `backend/agents/production_04/memory_agent.py` — open to line 77

| Lines | Show | Say |
|---|---|---|
| **77–85** | `tools=[...]` | "Four memory tools." |
| **114 · 158 · 185 · 231** | `recall_session_context` · `recall_traveler_preferences` · `recall_similar_interactions` · `persist_turn` | "Three read; `persist_turn` is the only writer, and it writes **Aurora only**." |

---

## Phase 5 — Orchestration · `backend/agents/orchestration_05/workflow.py`

**Open to line 530** — the graph build fits on one screen.

| Lines | Show | Say |
|---|---|---|
| **532–537** | `StateGraph` + `add_node` ×5 | "Five named nodes: classify, search, availability, memory_recall, synthesize." |
| **542–551** | `add_conditional_edges("classify", ...)` | "Classify fans out by intent." |
| **557–564** | conditional edge **out of search** → `availability if intent=='plan' else synthesize` | "The plan path chains search → availability, two sequential steps." |
| **196–307** | shared pool + `AsyncPostgresSaver.setup()` | "Initialize one bounded saver for the process, not one connection per request." |
| **583–586** | `compile(checkpointer=self.checkpointer, interrupt_after=...)` | "Pause after a committed node, terminate the worker, then resume the same thread." |

`_classify_intent` (line **390**) is good backup if asked "how does it know
it's a plan?" The visible `INSERT INTO checkpoints` proof is emitted by
the worker nodes (search / availability / memory_recall), not classify/synthesize.

---

## Demo-then-code ordering (Slide 24)

Run the **live showcase demo first** so the result lands with the audience, then
switch to the editor to show it's real code, not staged. The single file to have
scrolled-and-ready when you switch is `search_agent.py` at line **111**.

## Demo traveler (for reference)

Seeded in Aurora (not the prompt): **Alex Morgan** · `trv_meridian_demo` · home
airport **JFK** · party of 2 · Tokyo culture trip Oct 12–19 · shellfish allergy ·
boutique-over-chain · Marriott Bonvoy Platinum Elite + United MileagePlus Premier 1K.
