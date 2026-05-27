# Meridian — Presenter Code Walkthrough (300-level re:Invent)

Use this guide when **showing code on screen and talking through it** alongside the live demo.
Each phase has: **what to say**, **which file to open**, and **annotated snippets** that match the repo.

---

## Architecture decision (answer this first for builders)

### Do you need two separate full implementations?

**No.** You need **one product story** with **three orchestration layers**, not three copies of the same app.

| Layer | Technology | Phases | What the audience learns |
| ----- | ---------- | ------ | ------------------------ |
| **Data plane** | Aurora + RDS Data API + pgvector | 1–5 | Secure, shared source of truth |
| **Agent plane (implicit loops)** | **Strands Agents** + `@tool` | 1–4 | Bedrock chooses tools; fast multi-agent patterns |
| **Workflow plane (explicit graphs)** | **LangGraph** `StateGraph` | 5 | Branching, checkpointing, resumable workflows |
| **Managed services** | **AgentCore** Runtime + Gateway + Memory + Identity | 4 | Managed hosting, MCP tools, session memory, workload identity |

**Phase 5 should not replace Strands with AgentCore runtime.** AgentCore is already the hero of Phase 4 (identity envelope + session mirror). Phase 5’s differentiator is **LangGraph**: edges you can draw on a whiteboard, state you can checkpoint in Aurora, workflows you can resume after failure.

**Recommended narrative arc for a 300-level builder audience:**

1. **Phases 1–2** — “How do agents reach Aurora?” (direct SQL vs MCP transport)
2. **Phase 3** — “When NL search matters” (Strands supervisor + specialist `@tool`s)
3. **Phase 4** — “Production concierge” (Strands memory tools + Aurora RLS + AgentCore)
4. **Phase 5** — “When the workflow *is* the product” (LangGraph graph + PostgresSaver)

### Live demo vs IDE walkthrough

The UI always works. The **live API path** and the **agent modules you show in the IDE** intentionally differ in early phases:

| Phase | Live demo (`chat.py`) | Show in IDE (Strands pattern) |
| ----- | --------------------- | ------------------------------ |
| 1 | Procedural keyword SQL | `phase1/agent.py` — `@tool` + `Agent` |
| 2 | MCP `run_query` session | `phase2/agent.py` — `MCPClient` + auto-discovered tools |
| 3 | Hybrid search (+ Strands supervisor when `STRANDS_ORCHESTRATION=full`) | `phase3/supervisor.py` — delegation `@tool`s |
| 4 | Full `MemoryAgent.process_turn()` | `phase4/concierge.py` + `memory_agent.py` |
| 5 | Full `OrchestrationAgent` LangGraph | `phase5/workflow.py` — `StateGraph` |

**Talk track:** “The demo API keeps deterministic fallbacks so the room never stalls. The agent modules are the patterns you copy into production.”

---

## Phase 1 — SQL Agent (direct Aurora)

**Demo query:** `City breaks` or `Beach & Resort`  
**Beat:** Keyword filters break on “Romantic week in Europe.”

### File: `backend/agents/phase1/agent.py`

**Snippet A — Strands agent shell (show first):**

```python
from strands import Agent, tool
from strands.models import BedrockModel

self.model = BedrockModel(
    model_id=config.bedrock.model_id,
    region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
)

self.agent = Agent(
    model=self.model,
    tools=[
        self._lookup_product,
        self._search_products,
        self._check_inventory,
        self._calculate_total,
        self._process_order,
    ],
    system_prompt=self._get_system_prompt(),
)
```

**Say:** “Phase 1 is the MVP: one agent, five tools, direct RDS Data API. Bedrock decides which tool to call; each tool runs SQL and logs the query for the trace.”

**Snippet B — `@tool` + SQL (show second):**

```python
@tool
async def _search_products(
    self,
    query: str,
    trip_type: Optional[str] = None,
    limit: int = 5,
) -> List[dict]:
    """Search trip_packages with ILIKE filters — no embeddings yet."""
    sql = """
        SELECT package_id, name, operator, price_per_person, description,
               image_url, trip_type, destination, durations
        FROM trip_packages
        WHERE (name ILIKE %s OR description ILIKE %s OR operator ILIKE %s
               OR destination ILIKE %s)
    """
    ...
    results = await self.db.execute(sql, tuple(params))
```

**Say:** “Notice the decorator — Strands registers this as a Bedrock tool schema. The limitation is lexical: ‘romantic week in Europe’ doesn’t map to a column.”

### Live path pointer: `backend/routers/chat.py` → `phase1_search()`

Same SQL idea, no LLM loop — reliable for the demo UI.

---

## Phase 2 — MCP Agent (transport abstraction)

**Demo query:** `Adventure & Outdoors`  
**Beat:** MCP changes the *interface*, not the intelligence — same keyword gap.

### File: `backend/agents/phase2/agent.py`

**Snippet A — MCPClient + auto-discovery:**

```python
from strands.tools.mcp import MCPClient

self.mcp_client = MCPClient(
    server_name="postgres-mcp-server",
    command="uvx",
    args=["awslabs.postgres-mcp-server@latest"],
)

await self.mcp_client.connect()
mcp_tools = await self.mcp_client.list_tools()
# Agent receives MCP tools — run_query, connect_to_database, etc.
```

**Say:** “Phase 2 swaps the wire protocol. Instead of embedding SQL in Python tools, the agent discovers MCP tools at runtime. IAM + RDS Data API auth stays the same underneath.”

### Live path pointer: `backend/mcp/mcp_client.py` + `chat.py` → `phase2_search()`

---

## Phase 3 — Retrieval Agent (Strands multi-agent)

**Demo query:** `Romantic week in Europe`  
**Beat:** Hybrid pgvector + tsvector — NL finally works.

### File: `backend/agents/phase3/supervisor.py`

**Snippet A — Supervisor registers delegation tools:**

```python
self.agent = Agent(
    model=self.model,
    tools=[
        self._delegate_to_search,
        self._delegate_to_package,
        self._delegate_to_booking,
    ],
    system_prompt=self._get_system_prompt(),
)
```

**Snippet B — `@tool` delegation (the pattern to emphasize):**

```python
@tool
async def _delegate_to_search(self, query: str) -> dict:
    """Bedrock calls this when the traveler wants trip discovery."""
    result = await self.search_agent.semantic_search(query)
    self.last_search_packages = result.get("packages", [])
    return result
```

**Say:** “The supervisor’s tools are *delegation* tools — thin wrappers around specialist agents. Bedrock picks search vs package vs booking; specialists own Aurora access.”

### File: `backend/agents/phase3/search_agent.py`

**Snippet C — Specialist `@tool` + pgvector:**

```python
@tool
async def _semantic_search_tool(self, query: str, limit: int = 5) -> List[dict]:
    return await self.semantic_search(query, limit)

async def semantic_search(self, query: str, limit: int = 5) -> dict:
    query_embedding = self.embedding_service.generate_text_embedding(
        query, input_type="search_query"
    )
    results = await self.db.execute(
        "SELECT * FROM semantic_trip_search(%s::vector, %s)",
        (embedding_str, limit),
    )
```

**Say:** “1024-dim Cohere Embed v4, HNSW index, cosine similarity — this is the retrieval upgrade over Phase 1/2.”

### Env knob: `STRANDS_ORCHESTRATION=full` (default) uses live Strands supervisor; any other value uses procedural hybrid search in `chat.py`.

---

## Phase 4 — Memory Agent (Strands + full AgentCore stack + Aurora RLS)

**Demo query:** `Tokyo trip for two in October`  
**Beat:** Alex & Jordan's context is in Aurora; AgentCore is the managed platform around Strands.

### AgentCore story (one turn, four services)

| Service | File | Trace span |
| ------- | ---- | ---------- |
| **Runtime** | `agentcore/runtime.py` | `AgentCore Runtime · session envelope` |
| **Identity** | `agentcore/identity.py` | `AgentCore Identity resolved` |
| **Memory** | `agentcore/memory.py` | `list` + `semantic retrieve` + `create_event` |
| **Gateway** | `agentcore/gateway.py` | `tools/list` + `tools/call` (managed MCP search) |

### File: `backend/agents/phase4/memory_agent.py`

**Snippet A — Memory specialist `@tool`s (show this file first):**

```python
from strands import Agent, tool

class TravelerMemoryAgent:
    def __init__(self, ...):
        self.agent = Agent(
            model=self.model,
            tools=[
                self.recall_session_context,
                self.recall_traveler_preferences,
                self.recall_similar_interactions,
                self.persist_turn,
            ],
        )

    @tool
    async def recall_traveler_preferences(self, traveler_id: str, limit: int = 8):
        """Long-term facts from traveler_preferences (Aurora)."""
        facts = await self.store.recall_preferences(traveler_id, ...)
        return {"facts": facts}
```

**Say:** “Each `@tool` is a memory operation you’d expose to any orchestrator — Strands today, LangGraph node tomorrow.”

### File: `backend/agents/phase4/concierge.py`

**Snippet B — Full AgentCore turn in concierge:**

```python
scope = self.identity.scope_for_turn()
runtime_session = self.agentcore_runtime.session_for_turn(conv_id, traveler_id)
agentcore_turns = self.agentcore_memory.list_recent_turns(...)
agentcore_semantic = self.agentcore_memory.semantic_recall(...)
await self.agent.invoke_async(prompt)  # Strands @tool memory recall
packages, _ = await self._search_packages(message, limit, search_fn)  # Gateway or fallback
await self.traveler_memory.persist_turn(...)
self.agentcore_memory.record_turn(...)
```

**Snippet C — AgentCore Gateway MCP (`agentcore/gateway.py`):**

```python
self._mcp_request("tools/list")
self._mcp_request("tools/call", params={"name": tool_name, "arguments": {...}})
```

**Snippet D — AgentCore Runtime (`agentcore/runtime.py`):**

```python
client.invoke_agent_runtime(
    agentRuntimeArn=self.runtime_arn,
    runtimeSessionId=session_id,
    payload=payload,
    qualifier=self.qualifier,
)
```

**Say:** "Strands owns the reasoning loop. AgentCore Runtime isolates the session, Gateway exposes Aurora as managed MCP tools, Memory mirrors multi-turn context, Identity scopes credentials — Aurora stays durable truth with RLS."

**Provision (optional live AgentCore — @aws/agentcore CLI):**

```bash
npm install -g @aws/agentcore
cd meridian/meridian_agentcore
agentcore add memory --name meridian-session --strategies SEMANTIC
agentcore add gateway --name meridian-aurora --authorizer-type AWS_IAM
agentcore deploy -y
cd .. && python scripts/sync_agentcore_env.py --write
agentcore status --json
```

---

## Phase 5 — Orchestration Agent (LangGraph)

**Demo query:** `What dates are available for Tokyo in October?` (→ availability branch)  
**Alt query:** `What did we discuss last time about Iceland?` (→ memory_recall branch)

### File: `backend/agents/phase5/workflow.py`

**Snippet A — State + graph (draw this on the whiteboard):**

```python
class WorkflowState(TypedDict, total=False):
    query: str
    traveler_id: str
    intent: str  # 'search' | 'availability' | 'memory_recall'
    packages: List[Any]
    response: str
    activities: List[Dict[str, Any]]

builder = StateGraph(WorkflowState)
builder.add_node("classify", self._node_classify)
builder.add_node("search", self._node_search)
builder.add_node("availability", self._node_availability)
builder.add_node("memory_recall", self._node_memory_recall)
builder.add_node("synthesize", self._node_synthesize)

builder.add_conditional_edges("classify", lambda s: s["intent"], {...})
return builder.compile(checkpointer=self.checkpointer)
```

**Snippet B — Checkpointed execution:**

```python
config = {"configurable": {"thread_id": thread_id}}
result = await self.graph.ainvoke(initial, config=config)
```

**Say:** “LangGraph doesn’t replace Strands — it orchestrates *functions* that reuse Phase 3 search and Phase 4 memory. The win is explicit edges and PostgresSaver checkpoints in Aurora.”

### Checkpointer env: `LANGGRAPH_CHECKPOINT_DSN` → PostgresSaver; unset → in-process MemorySaver.

---

## Quick reference — files to have open

| Order | File | Phase |
| ----- | ---- | ----- |
| 1 | `backend/agent_catalog.py` | All — startup catalog |
| 2 | `backend/routers/chat.py` | Routing |
| 3 | `backend/agents/phase1/agent.py` | 1 |
| 4 | `backend/agents/phase2/agent.py` | 2 |
| 5 | `backend/agents/phase3/supervisor.py` | 3 |
| 6 | `backend/agents/phase3/search_agent.py` | 3 |
| 7 | `backend/agents/phase4/memory_agent.py` | 4 |
| 8 | `backend/agents/phase4/concierge.py` | 4 |
| 9 | `backend/agentcore/runtime.py` | 4 |
| 10 | `backend/agentcore/gateway.py` | 4 |
| 11 | `backend/agentcore/memory.py` | 4 |
| 12 | `backend/agentcore/identity.py` | 4 |
| 13 | `backend/agents/phase5/workflow.py` | 5 |

---

## FAQ for the room

**Q: Why not Strands for everything?**  
A: Strands excels at LLM-driven tool loops. When you need auditable branching, human approval gates, or durable workflow state, LangGraph gives you a graph you can inspect and resume.

**Q: Why not AgentCore for Phase 5?**  
A: You’d repeat Phase 4. Better story: AgentCore in Phase 4 (identity + memory), LangGraph in Phase 5 (workflow control plane).

**Q: Are the `@tool` decorators real?**  
A: Yes — `from strands import tool`. Methods must be async for I/O tools. The concierge passes bound methods into `Agent(tools=[...])`.

**Q: Why duplicate memory tool calls in the trace?**  
A: Phase 4 runs LLM-driven recall, then materializes the same Aurora reads for structured search context. Cost is negligible; trace shows both paths honestly.
