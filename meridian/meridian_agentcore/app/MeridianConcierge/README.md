# MeridianConcierge runtime

The Phase 4 agent, deployed to AgentCore Runtime as a CodeZip Python runtime.
The Meridian backend authorizes the traveler, reads their memory under RLS,
and invokes this runtime with `bedrock-agentcore:InvokeAgentRuntime`. The
runtime streams its trace, any hold or booking receipt, and the answer back as
server-sent events.

## Files

| File | Contents |
| --- | --- |
| `main.py` | The `@app.entrypoint`. Accepts `concierge_turn` payloads, runs a Strands agent with the gateway's MCP tools and an AgentCore Memory session, and streams `activity`, `packages`, `hold`, `booking`, `token` and `answer` events |
| `turn_trace.py` | Strands hooks that emit a span per tool call and pin the traveler ID, confirmation flag, budget ceiling and journey reference on every hold and confirmation call |
| `hold_execution.py` | Runs the hold or confirmation the traveler confirmed, through the gateway, before the model writes its reply |
| `gateway_auth.py` | SigV4 signing for MCP requests to the gateway with the runtime's execution role |
| `prompts.py` | System, turn and narration prompts |
| `model/load.py` | Bedrock model client (`BEDROCK_MODEL_ID`, default `global.anthropic.claude-haiku-4-5-20251001-v1:0`) |

## Model and latency

The managed concierge defaults to Claude Haiku 4.5 with a 4,096-token output
budget. Local teaching agents keep their separately configured Sonnet model.
To change the managed model, edit `BEDROCK_MODEL_ID` in the AgentCore template,
render the project configuration, then redeploy. Changing only the backend's
`.env` does not change the managed Runtime. Optional reasoning models retain a
16,000-token budget because thinking and tool generation share that limit.

Search results stream as they arrive from Gateway. The backend forwards only
package IDs for provisional cards; the browser resolves these against its live
Aurora catalog. Unrecognized IDs are not displayed. Final hydration, memory
persistence and the completed response remain authoritative. Card actions stay
disabled while the turn is running.

## Payload

`main.py` reads these fields from a `concierge_turn` payload: `prompt`,
`memory_context`, `traveler_id`, `conversation_id`, `budget_ceiling_cents`,
and, for a write the traveler confirmed, `hold_target` with `hold_confirmed`
or `booking_target` with `booking_confirmed`. The confirmation flags only take
effect when the matching target is present.

## Environment

The CDK app sets `AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL` and
`MEMORY_MERIDIAN_SESSION_ID`. The template adds the observability settings,
`BEDROCK_MODEL_ID`, `MERIDIAN_POLICY_MODE`, and, once they exist,
`MERIDIAN_GATEWAY_ID` and `MERIDIAN_POLICY_ENGINE_ID`, which label the trace.

## Deploy

Deploy with the rest of the AgentCore project; see the
[deployment runbook](../../../docs/AGENTCORE_DEPLOY_RUNBOOK.md). After a code
change, `agentcore deploy -y` from `meridian/meridian_agentcore/` publishes a
new runtime version.
