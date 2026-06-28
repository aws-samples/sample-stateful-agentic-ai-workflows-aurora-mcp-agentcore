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

## 2) Start the demo stack

Backend:

```bash
cd meridian
source venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend (new terminal):

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

## 4) Gateway smoke test (direct)

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

## 5) Quick recovery playbook

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

## 6) Walkthrough mode (2 hours later)

Recommended:
- Reuse the same deployed AgentCore resources.
- Keep kiosk stack running if possible.
- For code walkthrough, explain deploy commands but avoid live redeploy unless that is the explicit session objective.

## 7) Operator notes

- Prefer reliability over "fresh deploy theater".
- Keep one terminal focused on backend logs and one on frontend logs.
- If a fix is applied, rerun Section 3 smoke tests before resuming booth traffic.
