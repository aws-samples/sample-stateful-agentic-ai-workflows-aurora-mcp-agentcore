# Meridian agents (Phases 1 to 5)

Five capabilities, in phase order, on the **same Aurora travel schema**. Open each
numbered guide for the running code, sample prompt, evidence and takeaway.

1. [01 - SQL](phase_01_sql/)
2. [02 - MCP](phase_02_mcp/)
3. [03 - Retrieval](phase_03_retrieval/)
4. [04 - Production](phase_04_production/)
5. [05 - Workflow](phase_05_workflow/)

| Phase | Agent | Module | Pattern |
| ----- | ----- | ------ | ------- |
| 1 | **SQL Agent** | `phase_01_sql/agent.py` | Strands `@tool` + direct RDS Data API |
| 2 | **MCP Agent** | `phase_02_mcp/agent.py` | Strands + `MCPClient` (postgres-mcp-server) |
| 3 | **Retrieval Agent** | `phase_03_retrieval/agent.py` | One Strands agent over the cumulative tools: hybrid search and package details in process, the Phase 2 tools over MCP stdio |
| 4 | **Production Agent** | `phase_04_production/production_agent.py` | Identity, traveler grant, RLS read and write around the managed runtime |
| 4 | Concierge runtime | `../../meridian_agentcore/app/MeridianConcierge/main.py` | Strands agent in AgentCore Runtime: tools from AgentCore Gateway over MCP, AgentCore Memory session, Cedar-governed hold and booking confirmation |
| 4 | Traveler Memory Agent | `phase_04_production/memory_agent.py` | `@tool` recall / persist for Aurora memory |
| 5 | **Workflow** | `phase_05_workflow/graph.py` | Strands `Graph` built by `GraphBuilder`, run by the `MeridianWorkflow` Runtime, with Aurora `workflow_snapshots`; the hold node places its courtesy hold through the AgentCore Gateway tool under Cedar (`phase_05_workflow/governed_hold.py`); the search node and the release step call Gateway tools the same way (`phase_05_workflow/governed_tools.py`) |

## Live API routing (`backend/routers/chat.py`)

| Phase | Live path | Strands module imported? |
| ----- | --------- | ------------------------- |
| 1 | `sql_search()`: code calls the typed `search_trips`, one parameterized statement | No (reference only) |
| 2 | `mcp_search()`: code calls typed tools on the `meridian-concierge` MCP server, `search_trips` for a filter | No (reference only; the reference agent uses postgres-mcp-server) |
| 3 | `retrieval_agent_search()` → `run_retrieval_turn()`: one Strands agent on Bedrock | **Yes** (the agent and its cumulative tools) |
| 4 | `production_search()` → `ProductionAgent.process_turn()` → AgentCore Runtime (which calls the gateway tools); `production_hold()` → `process_hold()` for the one-click hold and `production_booking()` → `process_booking()` for its confirmation | **Yes** (ProductionAgent + MemoryAgent; the runtime agent lives in `meridian_agentcore/app/MeridianConcierge`) |
| 5 | `run_workflow_turn()` → `WorkflowRuntimeClient` → the `MeridianWorkflow` Runtime, which runs `WorkflowRunner` | **Yes**, in the Runtime (Strands `Graph`; snapshots saved in Aurora) |

The Phase 1 and 2 agent modules show the Strands structure for those patterns. The API runs those two phases in code, without a model loop (`search_trips` directly, then over the `meridian-concierge` MCP server), so their results do not depend on model tool selection. Phases 3 to 5 import their agent modules at runtime.

The reference SQL agent and the Phase 3 agent expose no booking writer: Phase 3 offers no
write tool. Every clicked hold and booking confirmation uses the governed Phase 4 path,
regardless of the selected ladder phase; Phase 5 automatic holds use the same Gateway tool.

See the [code walkthrough](../../docs/CODE_WALKTHROUGH.md) for a guided tour of
these modules.

## Measuring tool choice

The Concierge's model picks its tools from the Gateway's tool descriptions and the "Choose tools
by the question" rules in `concierge_definition.py`. Changing either changes that choice, and
nothing else in the test suite would notice. The same pattern runs at three points.

**Before a change: the offline gate.** `scripts/tool_choice_eval/cases.jsonl` holds the versioned
traveler prompts. Each case names the tools the model must call, the extra tools it may call, and
the arguments that matter (`price_range.destination` contains `tokyo`). A write tool is forbidden
unless the case expects or allows it, so a read question that ends in a hold fails. Unconfirmed
turns that ask for a hold are cases too, with the write allowed, because the Runtime lets the model
attempt it and the Gateway policy refuses. Confirmed turns give the model no tools, as the Runtime
does.

`scripts/run_tool_choice_eval.py` builds the Concierge agent the Runtime builds (the shared prompt,
the turn instructions, the traveler pin hook and the Runtime's model settings) over stub tools.
The stubs take their names and schemas from `meridian_agentcore/agentcore/gateway_targets/*/tool-schema.json`,
so a changed description is what the model sees on the next run. They return canned catalog
results and write nothing. It prints three numbers: selection accuracy, forbidden-call rate and
argument accuracy, with hashes of the prompt, the tool specs and the cases so two runs can be
compared. The prompt hash covers the shared prompt plus the instructions and narration of every
turn type; the cases hash covers each full case definition (memory context, expected tools and
argument matchers, confirmed-turn data) and the model inputs rendered from it. An expected
invocation passes its argument checks only when one call of the tool meets them all. Run it before
merging any change to the prompt, a tool description or an argument name, three times per case
(`--repeats 3`), and compare with the recorded baseline.

The averages alone can hide one bad case, so each case also has a floor. A run passes when it
calls the right tools, meets every argument check and calls nothing forbidden. A case must pass a
majority of its runs: 1 of 1 with one repeat, 2 of 3 with three, 3 of 4, 3 of 5. The script prints
the ids of the cases below the floor and exits 1 (a missed threshold), even when the averages pass.
A run that errored is not counted as a failure; it makes the result inconclusive (exit 2) unless a
case or an average already missed.

Baseline, 15 Nov 2026: `global.anthropic.claude-sonnet-5-5`, prompt `d4c88f717866`, tools
`f7102d25b38e`, cases `1458d7689217`, 29 cases x 3: 87 runs, none errored, selection accuracy
1.00, argument accuracy 1.00 over 93 checks (each judged against one call), forbidden-call rate
0.00, and every case passed all 3 of its runs, so it meets the per-case floor. The default thresholds are 0.95, 0.95 and 0.00. A changed hash means the baseline no longer
applies: run it again.

It is a dry run unless given `--apply --i-understand-this-changes-aws`, because it invokes the
model on Amazon Bedrock and bills the calling account; ask the account owner first. It refuses
when `CI` is set, and no unit test calls Bedrock. `tests/test_tool_choice_eval.py` checks the case
schema, the scorer, that every tool a case names exists in the Gateway schemas, that every read
tool is expected by a case, and that each write has an allowing and a forbidding case.

**In production: online sampling.** The sample measures tool choice offline only. In production,
turn on AgentCore Evaluations for the Concierge Runtime: an online evaluation with the built-in
`Builtin.ToolSelectionAccuracy` and `Builtin.ToolParameterAccuracy` evaluators scores a sample of
real sessions with a judge model and publishes the scores to CloudWatch, where you alarm when they
fall below the offline baseline. Give its execution role only the judge model you choose, the
Runtime's log group and span log, and `aws:SourceAccount` and `aws:SourceArn` conditions in its
trust policy. Add the prompts it flags to `cases.jsonl`.

The pick never decides what is allowed: the identity pin, Cedar and row-level security do. A wrong
pick costs answer quality, not safety.

## Environment

| Variable | Effect |
| -------- | ------ |
| `AGENTCORE_*` / CLI `@aws/agentcore` | Runtime, Gateway, and Memory for Phase 4, Phase 5 holds, and all clicked holds |
| `AGENTCORE_WORKFLOW_RUNTIME_ARN` | The `MeridianWorkflow` Runtime that runs Phase 5; set by `scripts/sync_agentcore_env.py --write` |
| `MERIDIAN_AGENTCORE_AUTH` | `iam` (default) signs the Runtime and Gateway calls with AWS credentials; `jwt` sends the signed-in person's Amazon Cognito access token and takes the traveler from its claim |
| `AURORA_WORKFLOW_SECRET_ARN` | The `meridian_workflow` login the Runtime uses; set by `scripts/provision_workflow_login.py --apply --i-understand-this-changes-aws --write-env` |

All SQL, prompts, and tools use the **travel schema** (`trip_packages`, `bookings`, `travelers`, `traveler_preferences`).
