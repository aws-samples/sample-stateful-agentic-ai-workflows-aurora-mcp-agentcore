# Meridian agents (Phases 1–5)

Five orchestration phases, each teaching a different builder pattern on the **same Aurora travel schema**.

| Phase | Agent | Module | Pattern |
| ----- | ----- | ------ | ------- |
| 1 | **SQL Agent** | `phase1/agent.py` | Strands `@tool` + direct RDS Data API |
| 2 | **MCP Agent** | `phase2/agent.py` | Strands + `MCPClient` (postgres-mcp-server) |
| 3 | **Retrieval Agent** | `phase3/supervisor.py` | Strands supervisor delegating to specialists |
| 3 | Search Agent | `phase3/search_agent.py` | `@tool` semantic search (pgvector) |
| 3 | Package Agent | `phase3/package_agent.py` | `@tool` details + departure availability |
| 3 | Booking Agent | `phase3/booking_agent.py` | `@tool` totals + Aurora booking writes |
| 4 | **Production Agent** | `phase4/concierge.py` | Strands concierge + RLS + AgentCore |
| 4 | Traveler Memory Agent | `phase4/memory_agent.py` | `@tool` recall / persist for Aurora memory |
| 5 | **Orchestration Agent** | `phase5/workflow.py` | LangGraph `StateGraph` + checkpointer |

## Live API routing (`backend/routers/chat.py`)

| Phase | Live path | Strands module imported? |
| ----- | --------- | ------------------------- |
| 1 | `sql_search()` — procedural keyword SQL | No (reference only) |
| 2 | `mcp_search()` — MCP only (postgres-mcp-server) | No (reference only) |
| 3 | `retrieval_supervisor_search()` — Strands + Bedrock delegation | **Yes** (supervisor + SearchAgent) |
| 4 | `production_search()` → `ProductionAgent.process_turn()` + AgentCore Gateway | **Yes** (concierge + TravelerMemoryAgent) |
| 5 | `orchestration_workflow()` → `OrchestrationAgent` | LangGraph (not Strands) |

**Presenter note:** Phases 1–2 agent modules are the **canonical Strands structure** to show on screen; the live API uses the same SQL/MCP mechanics without the LLM loop so demos stay reliable. Phases 3–5 import agent modules at runtime.

See **`docs/PRESENTER_GUIDE.md`** (Part 2 — Code Reference) for annotated snippets and talk tracks.

## Environment

| Variable | Effect |
| -------- | ------ |
| `AGENTCORE_*` / CLI `@aws/agentcore` | **Required for Phase 4** — Runtime, Gateway, Memory via `agentcore deploy` |
| `LANGGRAPH_CHECKPOINT_DSN` | Phase 5 PostgresSaver against Aurora |

All SQL, prompts, and tools use the **travel schema** (`trip_packages`, `bookings`, `travelers`, `traveler_preferences`).
