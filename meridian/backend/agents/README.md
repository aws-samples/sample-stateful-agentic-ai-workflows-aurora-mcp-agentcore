# Meridian agents (Phases 1 to 5)

Five capabilities, in talk order, on the **same Aurora travel schema**. Open each
numbered guide for the running code, demo prompt, evidence and takeaway.

1. [01 - SQL](phase_01_sql/)
2. [02 - MCP](phase_02_mcp/)
3. [03 - Retrieval](phase_03_retrieval/)
4. [04 - Production](phase_04_production/)
5. [05 - Workflow](phase_05_workflow/)

| Phase | Agent | Module | Pattern |
| ----- | ----- | ------ | ------- |
| 1 | **SQL Agent** | `phase_01_sql/agent.py` | Strands `@tool` + direct RDS Data API |
| 2 | **MCP Agent** | `phase_02_mcp/agent.py` | Strands + `MCPClient` (postgres-mcp-server) |
| 3 | **Retrieval Agent** | `phase_03_retrieval/supervisor.py` | Strands supervisor delegating to specialists |
| 3 | Search Agent | `phase_03_retrieval/search_agent.py` | `@tool` semantic search (pgvector) |
| 3 | Package Agent | `phase_03_retrieval/package_agent.py` | `@tool` details + departure availability |
| 3 | Booking Agent | `phase_03_retrieval/booking_agent.py` | Read-only price estimates from the Aurora catalog |
| 4 | **Production Agent** | `phase_04_production/concierge.py` | Identity, traveler grant, RLS read and write around the managed runtime |
| 4 | Concierge runtime | `../../meridian_agentcore/app/MeridianConcierge/main.py` | Strands agent in AgentCore Runtime: tools from AgentCore Gateway over MCP, AgentCore Memory session, Cedar-governed hold and booking confirmation |
| 4 | Traveler Memory Agent | `phase_04_production/memory_agent.py` | `@tool` recall / persist for Aurora memory |
| 5 | **Orchestration Agent** | `phase_05_workflow/graph.py` | Strands `Graph` + Aurora `workflow_snapshots`; the hold node places its courtesy hold through the AgentCore Gateway tool under Cedar (`phase_05_workflow/governed_hold.py`) |

## Live API routing (`backend/routers/chat.py`)

| Phase | Live path | Strands module imported? |
| ----- | --------- | ------------------------- |
| 1 | `sql_search()`: procedural keyword SQL | No (reference only) |
| 2 | `mcp_search()`: MCP only (postgres-mcp-server) | No (reference only) |
| 3 | `retrieval_supervisor_search()`: Strands + Bedrock delegation | **Yes** (supervisor, SearchAgent, PackageAgent, and read-only pricing specialist) |
| 4 | `production_search()` → `ProductionAgent.process_turn()` → AgentCore Runtime (which calls the gateway tools); `production_hold()` → `process_hold()` for the one-click hold | **Yes** (concierge + TravelerMemoryAgent; the runtime agent lives in `meridian_agentcore/app/MeridianConcierge`) |
| 5 | `orchestration_workflow()` → `WorkflowRunner` | **Yes** (Strands `Graph`; snapshots saved in Aurora) |

The Phase 1 and 2 agent modules show the Strands structure for those patterns; the API runs the same SQL and MCP steps without the model loop, so their results do not depend on model tool selection. Phases 3 to 5 import their agent modules at runtime.

The reference SQL agent and Phase 3 pricing specialist expose no booking writer.
The retrieval supervisor refuses write delegation. Every clicked hold and
booking confirmation uses the governed Phase 4 path, regardless of the selected
ladder phase; Phase 5 automatic holds use the same Gateway tool.

See the [code walkthrough](../../docs/CODE_WALKTHROUGH.md) for a guided tour of
these modules.

## Environment

| Variable | Effect |
| -------- | ------ |
| `AGENTCORE_*` / CLI `@aws/agentcore` | Runtime, Gateway, and Memory for Phase 4, Phase 5 holds, and all clicked holds |
| `LANGGRAPH_CHECKPOINT_DATA_API` | Phase 5 `AuroraDataApiSaver` over HTTPS when no checkpoint DSN resolves |
| `LANGGRAPH_CHECKPOINT_DSN` / `LANGGRAPH_CHECKPOINT_*` | Alternative `AsyncPostgresSaver` over an existing private PostgreSQL connection |
| `LANGGRAPH_CHECKPOINT_REQUIRED=true` | Fail closed when durable workflow state is unavailable |

All SQL, prompts, and tools use the **travel schema** (`trip_packages`, `bookings`, `travelers`, `traveler_preferences`).
