# AgentCore Deploy Runbook (Chalk Talk Prep)

End-to-end deploy of Runtime + Gateway + Memory for the Meridian Phase 4
demo. Designed to be runnable in one sitting (~15 min hands-on,
~5–8 min CDK wait) the day before the chalk talk.

## What gets deployed

| Resource | Name | Purpose at the chalk talk |
|---|---|---|
| **Memory** | `meridian_session` | `create_event` write + `list_events` read on every Phase 4 turn — visible in trace |
| **Gateway** | `meridian-aurora` | Managed MCP endpoint fronting `semantic_trip_search` Lambda — `tools/list` + `tools/call` spans land in trace |
| **Runtime** | `MeridianConcierge` | Hosts the agent module; runtime ARN appears in the AgentCore Identity span |

All three are declared in
[`meridian_agentcore/agentcore/agentcore.json`](../meridian_agentcore/agentcore/agentcore.json).
Memory uses the `SEMANTIC` strategy: `create_event` writes are mirrored into
the session namespace and surfaced via `retrieve_memory_records` /
`list_memory_records` (the recall path the Phase 4 trace shows).

## Prerequisites

```bash
# 1. AWS credentials (Isengard / SSO / aws configure — whatever the
#    account uses). Confirm with:
aws sts get-caller-identity

# 2. Region pinned to us-east-1 (Bedrock model access for Cohere
#    Rerank cross-region inference profile and Claude models live there).
export AWS_DEFAULT_REGION=us-east-1

# 3. AgentCore CLI installed:
npm install -g @aws/agentcore-cli
agentcore --version  # expect 0.x or later

# 4. Docker running (Runtime build path uses CodeZip, but the CDK
#    asset bundler still pulls a Node image during synth).

# 5. Node 20+ for CDK:
node --version
```

## One-time bootstrap (CDK)

```bash
cd meridian/meridian_agentcore/agentcore

# CDK bootstrap stamps the account+region with the assets bucket /
# image repo / IAM roles needed for any AgentCore deploy. Idempotent —
# safe to re-run.
npx cdk bootstrap aws://$(aws sts get-caller-identity --query Account --output text)/us-east-1
```

## Deploy

```bash
cd meridian/meridian_agentcore/agentcore

# Validate the spec first so we catch typos before CDK spins up:
agentcore validate

# Synth + deploy. Expect 5–8 min the first time:
#   - Memory: ~1 min
#   - Gateway + Lambda target: ~2–3 min
#   - Runtime (ECR push + microVM): ~3–4 min
agentcore deploy
```

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
# Confirm all three resources are healthy:
agentcore status

# Tail Runtime logs while you click a Phase 4 pill in the showcase:
agentcore logs --runtime MeridianConcierge --follow
```

In the showcase trace panel you should see (real, not faked):
- `AgentCore Identity resolved` — workload identity envelope
- `AgentCore Gateway · tools/list` — real MCP discovery
- `AgentCore Gateway · tools/call → semantic_trip_search` — real tool invocation
- `AgentCore Memory · create_event` — real write into Memory
- `AgentCore Memory · list_events` (recall pill) — real read

## Rollback / cleanup

```bash
# Pause Runtime billing without losing the deploy:
agentcore pause MeridianConcierge

# Or tear everything down (Memory, Gateway, Runtime, Lambda):
agentcore destroy
```

## What to say if something fails on stage

The Phase 4 code path does **not** fall back to Aurora-direct when AgentCore
Runtime, Gateway, or Memory are missing. Trace will show the failure explicitly
with category=`error`, and the bot says `AgentCore platform not configured`.
Narrate it plainly: *"Production mode is the managed AgentCore path. We fail
closed instead of silently swapping in a different architecture."*
