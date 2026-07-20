# Meridian agents (Phases 1–5)

Five orchestration phases, each teaching a different builder pattern on the **same Aurora travel schema**.

| Phase | Agent | Module | Pattern |
| ----- | ----- | ------ | ------- |
| 1 | **SQL Agent** | `sql_01/agent.py` | Strands `@tool` + direct RDS Data API |
| 2 | **MCP Agent** | `mcp_02/agent.py` | Strands + `MCPClient` (postgres-mcp-server) |
| 3 | **Retrieval Agent** | `retrieval_03/supervisor.py` | Strands supervisor delegating to specialists |
| 3 | Search Agent | `retrieval_03/search_agent.py` | `@tool` semantic search (pgvector) |
| 3 | Package Agent | `retrieval_03/package_agent.py` | `@tool` details + departure availability |
| 3 | Booking Agent | `retrieval_03/booking_agent.py` | `@tool` totals + Aurora booking writes |
| 4 | **Production Agent** | `production_04/concierge.py` | Strands concierge + RLS + AgentCore |
| 4 | Traveler Memory Agent | `production_04/memory_agent.py` | `@tool` recall / persist for Aurora memory |
| 5 | **Orchestration Agent** | `orchestration_05/workflow.py` | LangGraph `StateGraph` + pooled PostgresSaver |

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
| `LANGGRAPH_CHECKPOINT_DSN` / `LANGGRAPH_CHECKPOINT_*` | Phase 5 pooled PostgresSaver against Aurora |
| `LANGGRAPH_CHECKPOINT_REQUIRED=true` | Fail closed when durable workflow state is unavailable |

All SQL, prompts, and tools use the **travel schema** (`trip_packages`, `bookings`, `travelers`, `traveler_preferences`).
