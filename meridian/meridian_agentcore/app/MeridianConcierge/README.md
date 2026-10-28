# MeridianConcierge runtime

The Phase 4 agent, deployed to AgentCore Runtime as a CodeZip Python runtime.
The Meridian backend authorizes the traveler, reads their memory under RLS,
and invokes this runtime: with `bedrock-agentcore:InvokeAgentRuntime` signed
with AWS credentials in `iam` mode, or with the caller's Amazon Cognito access
token as a bearer token in `jwt` mode (`backend/agentcore/runtime.py`). The
runtime streams its trace, any hold or booking receipt, and the answer back as
server-sent events.

## Files

| File | Contents |
| --- | --- |
| `main.py` | The `@app.entrypoint`. Accepts `concierge_turn` payloads, runs a Strands agent with the gateway's MCP tools and an AgentCore Memory session, and streams `activity`, `packages`, `hold`, `booking`, `token` and `answer` events |
| `turn_trace.py` | Strands hooks that emit a span per tool call and pin the traveler ID, confirmation flag, budget ceiling and journey reference on every hold and confirmation call |
| `hold_execution.py` | Runs the hold or confirmation the traveler confirmed, through the gateway, before the model writes its reply |
| `caller_identity.py` | Who is calling. In `iam` mode the traveler comes from the payload; in `jwt` mode it comes from the `traveler_id` claim of the access token in the `Authorization` header, and a token that is malformed, expired or without the claim is refused |
| `gateway_auth.py` | How MCP requests reach the gateway. In `iam` mode they are signed with SigV4 using the runtime's execution role; in `jwt` mode they carry the caller's access token as received |
| `prompts.py` | Turn instructions and narration prompts; the tool guidance is the shared definition's |
| `concierge_definition.py` | Generated copy of backend/agents/concierge_definition.py (the tool contract, the prompt core, the traveler pin and `build_agent`); gitignored, staged on every render |
| `model/load.py` | Bedrock model factory for Claude Sonnet 5.5 (`BEDROCK_MODEL_ID`, default `global.anthropic.claude-sonnet-5-5`); repeats `backend/bedrock_model.py` |
| `tool_errors.py` | Ends the turn with the real error when a tool could not be set up; repeats `backend/agents/strands_hooks.py` |
| `turn_watchdog.py` | The 35 second idle watchdog and 300 second cap over a turn, the keepalive, and the closing of the saved history when a turn is ended early |
| `turn_work.py` | The small executor for setup and teardown calls that can hang, and the order of history repairs between turns |

## Model and latency

The managed concierge calls Claude Sonnet 5.5 through Bedrock's global inference profile
(`global.anthropic.claude-sonnet-5-5`) with a 16,000-token output budget.
`model/load.py` sends none of the three settings Sonnet 5.5 rejects:
no `temperature`, no forced tool choice and no disabled thinking, so tool choice stays
automatic. `REQUEST_FIELDS` sets adaptive thinking at effort medium (`thinking: adaptive`,
`output_config.effort: medium`), the same value as `backend/bedrock_model.py`; thinking
tokens count against the 16,000-token output budget. Local agents read their own `BEDROCK_MODEL_ID` from the backend
`.env` (the same default). The Runtime reuses its model client between turns while each
agent loads the authorized conversation through AgentCore Memory. To change the managed
model, edit `BEDROCK_MODEL_ID` in the AgentCore template, render the project
configuration, then redeploy; the CDK app accepts only a global inference profile id,
because it grants the execution role that one model through its profile. Changing only
the backend's `.env` does not change the managed Runtime. `BEDROCK_MAX_TOKENS` accepts 4,096
to 16,000; the floor leaves room for medium-effort thinking plus an answer. A
`BEDROCK_MODEL_ID` override still receives the thinking fields above, so it must name a
model that accepts them, or `REQUEST_FIELDS` must change in both files. GPT-OSS models are rejected by configuration. Throttling and transient errors
are retried by botocore in adaptive mode; a persistent failure ends the turn with its
error, and no other model answers. A tool that could not be set up (missing credentials,
permissions or configuration) ends the turn with that error (`tool_errors.py`).
There is no automatic replay of failed tool turns.

Discovery uses one hybrid search (meaning plus words, reranked), recommends up to two trips, and defers
availability checks until requested or needed to answer a missing fact. Follow-up
comparisons reuse the authorized context. Runtime startup, memory retrieval and
Gateway calls still contribute to first-turn latency; a model swap does not
remove those operations.

Search results stream as they arrive from Gateway. The backend forwards only
package IDs for provisional cards; the browser resolves these against its live
Aurora catalog. Unrecognized IDs are not displayed. Final hydration, memory
persistence and the completed response remain authoritative. Card actions stay
disabled while the turn is running.

## Turn limits, Memory and setup failures

A turn ends when nothing has happened for 35 seconds (the idle limit), or 300 seconds after it
started, whichever comes first. The backend reads the stream with a 45 second socket timeout, so after
15 seconds without an event for the caller the Runtime sends a `keepalive` event, which the backend
ignores and which does not count as progress. The agent build, the Gateway client start and close, and every
AgentCore Memory save and read run on worker threads, not on the event loop, so a slow call costs wall time
the idle watchdog sees and the keepalive keeps running. Each Memory client allows 2 seconds to connect,
4 seconds to read and 3 attempts in all, retried by botocore in standard mode. A hung Gateway client start ends
the turn at the idle limit, and a client that starts after the turn gave up is closed. A Gateway rejection or
other tool setup failure ends the turn with the real error (`tool_errors.py`), with 12-digit account ids masked.

A turn ended early can leave a tool call with no result in the saved history. The Runtime appends a fixed error
result for each open call and a fixed closing reply, so the next turn sends Bedrock a history that alternates
roles; the next turn waits up to 3 seconds for a repair still running. A Memory write that was still running
when its turn ended lands afterwards and sorts before the closing messages.

With thinking on, the assistant messages saved to AgentCore Memory, and the model-call span events
that tracing exports, include the model's reasoning text and its signature. That is the model's reasoning
about the same conversation, kept under the same retention and access as the conversation itself. The
traveler stream never carries it.

## Payload

`main.py` reads these fields from a `concierge_turn` payload: `prompt`,
`memory_context`, `traveler_id`, `conversation_id`, `budget_ceiling_cents`,
and, for a write the traveler confirmed, `hold_target` with `hold_confirmed`
or `booking_target` with `booking_confirmed`. The confirmation flags only take
effect when the matching target is present.

## Environment

`MERIDIAN_AGENTCORE_AUTH` (`iam` by default, or `jwt`) is rendered from the
template. The CDK app sets `AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL`, or
`AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL` for the `meridian-aurora-jwt`
Gateway, and `MEMORY_MERIDIAN_SESSION_ID`. The template adds the observability settings,
`BEDROCK_MODEL_ID`, `BEDROCK_MAX_TOKENS`,
`MERIDIAN_POLICY_MODE`, and, once they exist,
`MERIDIAN_GATEWAY_ID` and `MERIDIAN_POLICY_ENGINE_ID`, which label the trace.

## Deploy

Deploy with the rest of the AgentCore project; see the
[deployment runbook](../../../docs/AGENTCORE_DEPLOY_RUNBOOK.md). After a code
change, `agentcore deploy -y` from `meridian/meridian_agentcore/` publishes a
new runtime version.
