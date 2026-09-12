# Meridian agents (Phases 1–5)

Five orchestration phases, each teaching a different builder pattern on the **same Aurora travel schema**.

| Phase | Agent | Module | Pattern |
| ----- | ----- | ------ | ------- |
| 1 | **SQL Agent** | `sql_01/agent.py` | Strands `@tool` + direct RDS Data API |
| 2 | **MCP Agent** | `mcp_02/agent.py` | Strands + `MCPClient` (postgres-mcp-server) |
| 3 | **Retrieval Agent** | `retrieval_03/supervisor.py` | Strands supervisor delegating to specialists |
| 3 | Search Agent | `retrieval_03/search_agent.py` | `@tool` semantic search (pgvector) |
| 3 | Package Agent | `retrieval_03/package_agent.py` | `@tool` details + departure availability |
| 3 | Booking Agent | `retrieval_03/booking_agent.py` | Read-only price estimates from the Aurora catalog |
| 4 | **Production Agent** | `production_04/concierge.py` | Identity, traveler grant, RLS read and write around the managed runtime |
| 4 | Concierge runtime | `../../meridian_agentcore/app/MeridianConcierge/main.py` | Strands agent in AgentCore Runtime: tools from AgentCore Gateway over MCP, AgentCore Memory session, Cedar-governed hold and booking confirmation |
| 4 | Traveler Memory Agent | `production_04/memory_agent.py` | `@tool` recall / persist for Aurora memory |
| 5 | **Orchestration Agent** | `orchestration_05/workflow.py` | LangGraph `StateGraph` + Aurora checkpoints; the hold node places its courtesy hold through the AgentCore Gateway tool under Cedar (`orchestration_05/governed_hold.py`) |

## Live API routing (`backend/routers/chat.py`)

| Phase | Live path | Strands module imported? |
| ----- | --------- | ------------------------- |
| 1 | `sql_search()` — procedural keyword SQL | No (reference only) |
| 2 | `mcp_search()` — MCP only (postgres-mcp-server) | No (reference only) |
| 3 | `retrieval_supervisor_search()` — Strands + Bedrock delegation | **Yes** (supervisor, SearchAgent, PackageAgent, and read-only pricing specialist) |
| 4 | `production_search()` → `ProductionAgent.process_turn()` → AgentCore Runtime (which calls the gateway tools); `production_hold()` → `process_hold()` for the one-click hold | **Yes** (concierge + TravelerMemoryAgent; the runtime agent lives in `meridian_agentcore/app/MeridianConcierge`) |
| 5 | `orchestration_workflow()` → `OrchestrationAgent` | LangGraph (not Strands) |

**Presenter note:** Phases 1–2 agent modules are the **canonical Strands structure** to show on screen; the live API uses the same SQL/MCP mechanics without the LLM loop so demos stay reliable. Phases 3–5 import agent modules at runtime.

The reference SQL agent and Phase 3 pricing specialist expose no booking writer.
The retrieval supervisor refuses write delegation. Every clicked hold and
booking confirmation uses the governed Phase 4 path, regardless of the selected
ladder phase; Phase 5 automatic holds use the same Gateway tool.

See the [presenter guide](../../docs/PRESENTER_GUIDE.md) for code references and
talk tracks.

## Environment

| Variable | Effect |
| -------- | ------ |
| `AGENTCORE_*` / CLI `@aws/agentcore` | Runtime, Gateway, and Memory for Phase 4, Phase 5 holds, and all clicked holds |
| `LANGGRAPH_CHECKPOINT_DATA_API` | Phase 5 `AuroraDataApiSaver` over HTTPS when no checkpoint DSN resolves |
| `LANGGRAPH_CHECKPOINT_DSN` / `LANGGRAPH_CHECKPOINT_*` | Alternative `AsyncPostgresSaver` over an existing private PostgreSQL connection |
| `LANGGRAPH_CHECKPOINT_REQUIRED=true` | Fail closed when durable workflow state is unavailable |

All SQL, prompts, and tools use the **travel schema** (`trip_packages`, `bookings`, `travelers`, `traveler_preferences`).
