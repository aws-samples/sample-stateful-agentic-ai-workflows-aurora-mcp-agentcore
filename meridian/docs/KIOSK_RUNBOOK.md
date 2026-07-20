# Meridian Kiosk Runbook

Use this when operating the booth demo. Keep this open in one tab.

## 1) Preflight (10-15 min before)

- Confirm AWS auth works:

```bash
aws sts get-caller-identity
```

- Confirm AgentCore project exists:

```bash
cd meridian/meridian_agentcore
agentcore status
```

- Confirm app env has AgentCore values:

```bash
cd meridian
rg "AGENTCORE_" .env
```

Expected keys:
- `AGENTCORE_RUNTIME_ARN`
- `AGENTCORE_GATEWAY_URL`
- `AGENTCORE_GATEWAY_SEARCH_TOOL`
- `AGENTCORE_MEMORY_ID`

## 2) Start the durable demo stack

Terminal 1 — Aurora checkpoint tunnel:

```bash
cd meridian
./scripts/start_checkpoint_tunnel.sh
```

Terminal 2 — backend:

```bash
cd meridian
source venv/bin/activate
export LANGGRAPH_CHECKPOINT_HOST=127.0.0.1
export LANGGRAPH_CHECKPOINT_PORT=15432
export LANGGRAPH_CHECKPOINT_DATABASE=meridian
export LANGGRAPH_CHECKPOINT_REQUIRED=true
export LANGGRAPH_CHECKPOINT_INIT_ON_STARTUP=true
export LANGGRAPH_DEMO_INTERRUPT_AFTER=search
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Terminal 3 — frontend:

```bash
cd meridian/frontend
npm run dev -- --host --port 5173
```

Presenter URL:

- `http://localhost:5173/showcase`

## 3) Health checks (must pass)

API health:

```bash
curl -s http://localhost:8000/health | jq .
```

Required checkpoint fields:

```json
{
  "checkpoint_backend": "PostgresSaver (Aurora · pooled)",
  "checkpoint_durable": true,
  "checkpoint_required": true
}
```

Memory endpoint:

```bash
curl -s http://localhost:8000/api/memory/trv_meridian_demo | jq .
```

Phase 4 smoke test:

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "phase": 4,
    "message": "A slow week somewhere we can drink good wine",
    "traveler_id": "trv_meridian_demo"
  }' | jq '.message, .conversation_id, (.products | length)'
```

## 4) Durable workflow smoke test

1. Run the cancelled JFK-to-Tokyo prompt in Workflow.
2. Confirm the workflow pauses after `search` with `next=availability`.
3. Stop and restart only the backend.
4. Click **Resume workflow from checkpoint** without clearing the browser.
5. Confirm the same thread resumes at `availability`.

Do not present `MemorySaver` as durable. It is an in-process fallback only.

## 5) Gateway smoke test (direct)

Run once before going live:

```bash
cd ..
meridian/venv/bin/python - <<'PY'
import sys
sys.path.insert(0,'meridian')
from dotenv import load_dotenv
load_dotenv('meridian/.env')
from backend.agentcore.gateway import AgentCoreGatewayAdapter
ad = AgentCoreGatewayAdapter()
tools, _ = ad.list_tools()
print("tools:", [t.get("name") for t in tools])
pkgs, _ = ad.semantic_trip_search("wine week in europe", 3)
print("packages:", len(pkgs))
PY
```

## 6) Quick recovery playbook

### A) `runtimeSessionId ... valid min length: 33`

- Pull latest code with runtime session id fix and restart backend.
- If needed, verify backend imports latest:

```bash
cd meridian
rg "_build_runtime_session_id" backend/agentcore/runtime.py
```

### B) Gateway error: `no targets were configured`

- Re-attach target and deploy. Replace `123456789012` with the AWS account
  running the workshop:

```bash
cd meridian/meridian_agentcore
agentcore add gateway-target \
  --name SemanticTripSearchLambda \
  --gateway meridian-aurora \
  --type lambda-function-arn \
  --lambda-arn arn:aws:lambda:us-east-1:123456789012:function:meridian-semantic-trip-search \
  --tool-schema-file ./agentcore/gateway_targets/semantic_trip_search/tool-schema.json
agentcore deploy -y
```

### C) Phase 4 returns zero packages unexpectedly

- Verify tool name in `.env`:

```bash
cd meridian
rg "AGENTCORE_GATEWAY_SEARCH_TOOL" .env
```

Expected value:
- `SemanticTripSearchLambda___semantic_trip_search`

- Restart backend after any `.env` change.

### D) Build/demo UI issues

- Restart frontend dev server.
- Hard refresh browser (`Cmd+Shift+R`).

## 7) Walkthrough mode (2 hours later)

Recommended:
- Reuse the same deployed AgentCore resources.
- Keep kiosk stack running if possible.
- For code walkthrough, explain deploy commands but avoid live redeploy unless that is the explicit session objective.

## 8) Operator notes

- Prefer reliability over "fresh deploy theater".
- Keep one terminal focused on backend logs and one on frontend logs.
- If a fix is applied, rerun Section 3 smoke tests before resuming booth traffic.
