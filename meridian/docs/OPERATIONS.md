# Meridian Operations

Everything for running the demo: **deploy** AgentCore (day-before), **run the
booth/kiosk** (day-of), and the **gotchas** we hit getting Phase 4 live.

- Deploy procedure → [Part 1](#part-1--deploy-agentcore-day-before)
- Booth / kiosk operation → [Part 2](#part-2--kiosk--booth-runbook-day-of)
- Lessons & gotchas → [Part 3](#part-3--learnings--gotchas)

Account/region for this demo: **`us-east-1`**, account `619763002613`.
Resources are named with the `meridianv2` project prefix.

---

# PART 1 — Deploy AgentCore (day-before)

End-to-end deploy of Runtime + Gateway + Memory for Phase 4. ~15 min hands-on,
~5–8 min CDK wait. Resources persist until explicitly destroyed — deploying the
day before is fine.

## What gets deployed

| Resource | Name | Purpose at the talk |
|---|---|---|
| **Memory** | `meridian_session` | `create_event` write + recall read every Phase 4 turn — visible in trace |
| **Gateway** | `meridian-aurora` | Managed MCP endpoint fronting the `semantic_trip_search` Lambda — `tools/list` + `tools/call` spans |
| **Runtime** | `MeridianConcierge` | Hosts the agent module; runtime ARN appears in the AgentCore Identity span |

All three are declared in
[`meridian_agentcore/agentcore/agentcore.json`](../meridian_agentcore/agentcore/agentcore.json).
Memory uses the **`SEMANTIC`** strategy: `create_event` writes are mirrored into
the session namespace and surfaced via `retrieve_memory_records` /
`list_memory_records` (the recall path the Phase 4 trace shows).

## Prerequisites

```bash
# 1. AWS credentials (Isengard / SSO / aws configure). Confirm:
aws sts get-caller-identity

# 2. Region pinned to us-east-1 (Bedrock access for Cohere Rerank + Claude Opus):
export AWS_DEFAULT_REGION=us-east-1

# 3. AgentCore CLI (Node-based, installed globally):
npm install -g @aws/agentcore
agentcore --version

# 4. Node 20+ for the CDK synth step:
node --version
```

## Deploy

```bash
cd meridian/meridian_agentcore

# Validate the config against the current CLI schema first:
agentcore status --json     # also shows whether resources already exist

# Synth + deploy (idempotent — updates the existing stack in place):
agentcore deploy -y
```

`agentcore deploy` writes the live ARNs to
`meridian_agentcore/agentcore/.cli/deployed-state.json`. The backend reads them
through [`backend/agentcore/cli_config.py`](../backend/agentcore/cli_config.py).

## Wire the ARNs into the backend env

```bash
cd meridian
python scripts/sync_agentcore_env.py --write
# Writes into .env:
#   AGENTCORE_RUNTIME_ARN, AGENTCORE_GATEWAY_URL,
#   AGENTCORE_GATEWAY_SEARCH_TOOL, AGENTCORE_MEMORY_ID, AGENTCORE_REGION
```

Restart the backend; the next Phase 4 turn uses real AgentCore data-plane calls.

## Verify

```bash
agentcore status --json     # all three resources: deploymentState "deployed"
```

In the showcase trace panel you should see (real, not faked):
- `AgentCore Identity resolved` — workload identity envelope
- `AgentCore Gateway · tools/list` — real MCP discovery
- `AgentCore Gateway · tools/call → semantic_trip_search` — real tool invocation
- `AgentCore Memory · create_event` — real write
- `AgentCore Memory · list/retrieve` (recall) — real read

> Transaction-search trace indexing takes **~10 min** after deploy to fully
> activate. Don't judge missing trace spans in the first few minutes.

## Graceful-degradation note (what to say if AgentCore fails on stage)

The Phase 4 code path falls back to **Aurora-direct** when AgentCore calls fail.
The trace shows the failure with `category=error`, the bot still answers from
Aurora, and you narrate: *"Production-grade fallback — when the managed plane is
degraded, we degrade gracefully to the underlying data plane."* That's truthful:
[`production_04/concierge.py`](../backend/agents/production_04/concierge.py)
catches AgentCore exceptions and continues with direct Aurora reads.

## Teardown (after the event)

Resources bill while they exist. When done:
```bash
cd meridian/meridian_agentcore
agentcore destroy           # Memory, Gateway, Runtime, Lambda target
```

---

# PART 2 — Kiosk / booth runbook (day-of)

Keep this open in one tab while operating the booth.

## 1) Preflight (10–15 min before)

```bash
aws sts get-caller-identity                 # AWS auth works

cd meridian/meridian_agentcore
agentcore status --json                     # resources healthy

cd ../ && rg "AGENTCORE_" .env              # env has the 4 keys below
```
Expected `.env` keys: `AGENTCORE_RUNTIME_ARN`, `AGENTCORE_GATEWAY_URL`,
`AGENTCORE_GATEWAY_SEARCH_TOOL`, `AGENTCORE_MEMORY_ID`.

## 2) Start the stack

```bash
# Backend
cd meridian && source venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# Frontend (new terminal)
cd meridian/frontend
npm run dev -- --host --port 5173
```

Surfaces (see README "Two surfaces, one app" for the full map):
- **Kiosk loop:** `http://localhost:5173/demo-stage?kiosk=1`
- **Demo Stage (presenter):** `http://localhost:5173/demo-stage` (Space=play/pause, ←/→=step, R=replay, B=builder)
- **Device Showcase:** `http://localhost:5173/showcase`
- **Meridian Pro:** `http://localhost:5173/`

## 3) Health checks (must pass)

```bash
curl -s http://localhost:8000/health | jq .                       # Opus 4.8 + cohere.embed-v4:0
curl -s http://localhost:8000/api/memory/trv_meridian_demo | jq . # Alex Morgan facts

# Phase 4 smoke — exercises Bedrock → AgentCore Gateway → Aurora end to end:
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"phase":4,"message":"A slow week somewhere we can drink good wine","traveler_id":"trv_meridian_demo"}' \
  | jq '.message, .conversation_id, (.products | length)'
```

## 4) Gateway smoke test (direct — run once before going live)

```bash
cd meridian
venv/bin/python - <<'PY'
import sys; sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv('.env')
from backend.agentcore.gateway import AgentCoreGatewayAdapter
ad = AgentCoreGatewayAdapter()
tools, _ = ad.list_tools(); print("tools:", [t.get("name") for t in tools])
pkgs, _ = ad.semantic_trip_search("wine week in europe", 3); print("packages:", len(pkgs))
PY
```

## 5) Recovery playbook

**`runtimeSessionId ... valid min length: 33`** — restart backend; verify
`rg "_build_runtime_session_id" backend/agentcore/runtime.py`.

**Gateway `no targets were configured`** — re-attach the Lambda target and redeploy:
```bash
cd meridian/meridian_agentcore
agentcore add gateway-target --name SemanticTripSearchLambda --gateway meridian-aurora \
  --type lambda-function-arn \
  --lambda-arn arn:aws:lambda:us-east-1:619763002613:function:meridian-semantic-trip-search \
  --tool-schema-file ./agentcore/gateway_targets/semantic_trip_search/tool-schema.json
agentcore deploy -y
```

**Phase 4 returns zero packages** — check `rg "AGENTCORE_GATEWAY_SEARCH_TOOL" .env`
equals `SemanticTripSearchLambda___semantic_trip_search`; restart backend after any `.env` change.

**UI issues** — restart frontend dev server; hard refresh (`Cmd+Shift+R`).

## 6) Operator notes

- Reuse the same deployed stack for both kiosk and the code walkthrough — avoid
  "fresh deploy theater" unless deploying is the explicit lesson.
- One terminal on backend logs, one on frontend logs.
- After any fix, rerun the Section 3 health checks before resuming booth traffic.
- The kiosk auto-loops real `/api/chat` calls on a timer → real Bedrock + Aurora
  spend. Stop it when not actively demoing.

---

# PART 3 — Learnings & gotchas

Hard-won notes from getting Phase 4 live. Most map to a recovery step above.

- **Gateway needs real targets.** Creating a Gateway isn't enough — `tools/call`
  fails until at least one target is attached.
- **`localhost` is not a valid target.** Gateway runs in AWS; targets must be
  cloud-reachable (Lambda, API Gateway, public MCP).
- **Tool names are target-prefixed.** Once a target is attached, the effective
  tool name is `<TargetName>___<toolName>` (hence
  `SemanticTripSearchLambda___semantic_trip_search`).
- **Runtime session IDs have constraints.** `runtimeSessionId` must satisfy
  AgentCore validation (≥33 chars); short conversation IDs were rejected.
- **Embedding dimensions must match the DB vectors.** Cohere Embed v4 can return
  1536 unless `output_dimension=1024` is set explicitly for our pgvector schema.
- **Region consistency matters.** Runtime / Gateway / Lambda must all be in
  `us-east-1` for this demo.
- **Config schema drifts with the CLI.** `agentcore.json` must use a project
  `name` that starts with a letter and is alphanumeric (`meridianv2`), and a
  memory strategy from `SEMANTIC | SUMMARIZATION | USER_PREFERENCE | EPISODIC`.

## Why two folders (`meridian/` and `meridian/meridian_agentcore/`)?

- **`meridian/`** = the product app (FastAPI + frontend + tests + demo code).
- **`meridian/meridian_agentcore/`** = the AgentCore CLI project (infra-as-code
  for the AgentCore resources + deployment state).

The app reads deployed values from `.env` / the CLI deployed-state file at
runtime. The split is intentional and fine; could be merged later if desired.
