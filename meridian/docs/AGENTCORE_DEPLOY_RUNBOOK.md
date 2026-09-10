# AgentCore Deploy Runbook (Chalk Talk Prep)

End-to-end deploy of Runtime + Gateway + Memory for the Meridian Phase 4
demo. Designed to be runnable in one sitting (~15 min hands-on,
~5–8 min CDK wait) the day before the chalk talk.

## What gets deployed

| Resource | Name | Purpose at the chalk talk |
|---|---|---|
| **Runtime** | `MeridianConcierge` | The Phase 4 agent: Strands tool loop over the gateway, AgentCore Memory session, ADOT spans, streamed trace |
| **Memory** | `meridian_session` | The agent's session store; the Strands session manager restores it and writes each turn back |
| **Gateway** | `meridian-aurora` | Managed MCP endpoint, AWS_IAM inbound, Cedar policy engine attached in ENFORCE mode |
| **Gateway target** | `SemanticTripSearchLambda` | `semantic_trip_search(query, limit)` over Aurora pgvector |
| **Gateway target** | `MeridianHolds` | CDK-built Lambda: `get_package_details(packageId)`, `create_courtesy_hold(...)` and `confirm_booking(...)` with the identity chain |
| **Policy engine** | `MeridianGovernance` | `meridian_read_tools` permits the reads; `meridian_hold_governance` permits the hold only when confirmed, at most 12 hours, at most 6 travelers, within budget; `meridian_booking_governance` permits the confirmation only when confirmed and within budget |

All of them are declared in
[`meridian_agentcore/agentcore/agentcore.json`](../meridian_agentcore/agentcore/agentcore.json).
Never rename `meridian_session`: a rename replaces the memory and drops the
seeded Tokyo history. Memory uses the `SEMANTIC` strategy over
`/users/{actorId}/sessions/{sessionId}`; the runtime is the actor's session.

## Prerequisites

```bash
# 1. AWS credentials (Isengard / SSO / aws configure — whatever the
#    account uses). Confirm with:
aws sts get-caller-identity

# 2. Region pinned to us-east-1 (Bedrock model access for Cohere
#    Rerank cross-region inference profile and Claude models live there).
export AWS_DEFAULT_REGION=us-east-1

# 3. AgentCore CLI installed:
npm install -g @aws/agentcore
agentcore --version

# 4. Docker running (Runtime build path uses CodeZip, but the CDK
#    asset bundler still pulls a Node image during synth).

# 5. Node 20+ for CDK:
node --version
```

## One-time bootstrap (CDK)

```bash
cd meridian/meridian_agentcore/agentcore/cdk

# CDK bootstrap stamps the account+region with the assets bucket /
# image repo / IAM roles needed for any AgentCore deploy. Idempotent —
# safe to re-run.
npm run cdk -- bootstrap aws://$(aws sts get-caller-identity --query Account --output text)/us-east-1
```

## Deploy

```bash
cd meridian

# 1. The holds Lambda reads its Aurora settings from SSM (values come from .env):
python scripts/publish_gateway_parameters.py

cd meridian_agentcore

# 2. Validate the spec first so we catch typos before CDK spins up:
agentcore validate --json

# 3. Synth + deploy. Expect 5–8 min the first time:
#   - Memory: ~1 min
#   - Gateway, the two Lambda targets, the policy engine: ~2–3 min
#   - Runtime (code zip + microVM): ~3–4 min
agentcore deploy -y

# 4. The holds Lambda is a workload: grant its execution role access to Alex.
cd .. && python scripts/bind_gateway_workload.py
```

On a fresh account, deploy in two passes: first with `policyEngines: []` and
no `policyEngineConfiguration` on the gateway (the policies validate against the
target's tool schema, which must exist), then restore both and deploy again.
Updates to an existing stack are a single `agentcore deploy -y`.

`agentcore deploy` writes the live ARNs back into
`meridian_agentcore/agentcore/.cli/deployed-state.json`. The Meridian backend
reads them via the existing CLI-config loader at
[`backend/agentcore/cli_config.py`](../backend/agentcore/cli_config.py)
(`AGENTCORE_RUNTIME_ARN`, `AGENTCORE_GATEWAY_URL`, `AGENTCORE_MEMORY_ID`).

## Wire the ARNs into the backend env

There's already a sync script in the repo:

```bash
cd meridian
python scripts/sync_agentcore_env.py
# This reads .cli/deployed-state.json and updates .env with:
#   AGENTCORE_RUNTIME_ARN=...
#   AGENTCORE_GATEWAY_URL=https://....gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
#   AGENTCORE_MEMORY_ID=mem-...
```

Restart the FastAPI backend and the next Phase 4 turn will use real
AgentCore data-plane calls.

## Verify

```bash
cd meridian

# Runtime, Gateway, Memory, policy engine (ACTIVE · ENFORCE), four tools, observability:
python scripts/verify_agentcore.py

# tools/list plus one get_package_details call, signed from this laptop:
python scripts/smoke_gateway_tools.py CTY-002

# The governed path end to end: search, unconfirmed hold denied, confirmed hold held,
# over-budget hold denied. Prints three PASS lines and saves every event under
# .local/verification/.
python scripts/smoke_production_turn.py

# Tail Runtime logs while you click a Phase 4 pill in the showcase:
cd meridian_agentcore && agentcore logs --runtime MeridianConcierge --follow
```

In the showcase trace panel you should see (real, not faked):
- `AgentCore Identity resolved` and `Workload traveler grant allowed`
- `AgentCore Runtime · turn started`
- `AgentCore Gateway · tools/list` with four tools, SigV4
- `AgentCore Memory · session restored` with the event count
- `AgentCore Gateway · tools/call → semantic_trip_search` and its result
- On a Hold click: `tools/call → create_courtesy_hold`, its result with the
  Lambda's workload subject and `traveler_grant: allow`, and the hold receipt
- On a Confirm click: `tools/call → confirm_booking`, its result under
  `meridian_booking_governance`, and the receipt as **Confirmed booking**
- On a typed hold: `Hold refused by Cedar policy · Denied by policy`
- `AgentCore Runtime · turn complete` with the trace id and a CloudWatch link

Spans for that trace id are in the runtime log group
`/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`, stream `spans`, with
the application logs in the per-session `runtime-logs-*` streams.

## Rollback / cleanup

```bash
# AgentCore CLI 0.27.0 does not pause Runtime resources. To remove this stack,
# update the schema with these commands, then deploy the removals:
agentcore remove gateway-target --name SemanticTripSearchLambda -y
agentcore remove gateway --name meridian-aurora -y
agentcore remove memory --name meridian_session -y
agentcore remove agent --name MeridianConcierge -y
agentcore validate --json
agentcore deploy -y
```

## What to say if something fails on stage

The Phase 4 code path does **not** fall back to Aurora-direct when AgentCore
Runtime, Gateway, or Memory are missing. Trace will show the failure explicitly
with category=`error`, and the bot says `AgentCore platform not configured`.
Narrate it plainly: *"Production mode is the managed AgentCore path. We fail
closed instead of silently swapping in a different architecture."*
