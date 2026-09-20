# Meridian Kiosk Runbook

Use this when operating the booth demo. Keep this open in one tab. The
presenter path for the chalk talk itself is `docs/PRESENTER_GUIDE.md`; this
runbook covers the unattended kiosk loop and the operator checks around it.

## 1) Preflight (10-15 min before)

- Confirm AWS auth works:

```bash
aws sts get-caller-identity
```

- Confirm the AgentCore resources are deployed and healthy:

```bash
cd meridian
venv/bin/python scripts/verify_agentcore.py   # Runtime READY, Gateway READY, Memory ACTIVE, policy engine ACTIVE · ENFORCE
```

- Confirm the app env carries the AgentCore and checkpoint values:

```bash
cd meridian
rg "AGENTCORE_|LANGGRAPH_CHECKPOINT" .env
```

Expected keys:
- `AGENTCORE_RUNTIME_ARN`
- `AGENTCORE_GATEWAY_URL`
- `AGENTCORE_GATEWAY_SEARCH_TOOL` (`SemanticTripSearchLambda___semantic_trip_search`)
- `AGENTCORE_MEMORY_ID`
- `LANGGRAPH_CHECKPOINT_DATA_API=true`
- `LANGGRAPH_CHECKPOINT_REQUIRED=true`

The kiosk laptop reaches Aurora only over the RDS Data API. The cluster
instance is not publicly accessible, so the durable checkpointer is
`AuroraDataApiSaver`. The SSM tunnel path (`scripts/start_checkpoint_tunnel.sh`
plus `LANGGRAPH_CHECKPOINT_DSN`) is optional and only applies where a
port-forwarding instance exists.

## 2) Start the durable demo stack

Terminal 1, backend:

```bash
cd meridian
source venv/bin/activate
uvicorn backend.main:app --host 127.0.0.1 --port 8013
```

Restart the backend after any source or `.env` change. The process reads
`.env` at startup, and a stale process is the most common cause of a demo that
disagrees with the repository.

Keep the API loopback-only for the single-machine kiosk. If a remote browser
must connect, put an OIDC-aware reverse proxy or backend-for-frontend in front
of the API. It must authenticate the caller and keep `MERIDIAN_API_TOKEN`
server-side; do not put a bearer token in a Vite environment variable. Set an
explicit `CORS_ORIGINS` allow-list before using a non-loopback host.

Terminal 2, frontend:

```bash
cd meridian/frontend
VITE_API_ORIGIN=http://127.0.0.1:8013 npm run dev -- --host 127.0.0.1 --port 5176 --strictPort
```

Kiosk URLs:

- `http://127.0.0.1:5176/demo-stage?kiosk=1` (auto-playing loop, three scenarios)
- `http://127.0.0.1:5176/demo-stage?kiosk=1&phase=3` (retrieval-only loop)
- `http://127.0.0.1:5176/showcase` (attended concierge, ladder, recovery, evidence, briefing)

## 3) Health checks (must pass)

Public liveness (no auth):

```bash
curl -s http://127.0.0.1:8013/health | jq .
```

Readiness and configuration (loopback principal in development):

```bash
curl -s http://127.0.0.1:8013/api/health | jq .
```

Required fields:

```json
{
  "status": "healthy",
  "bedrock_model_id": "global.anthropic.claude-sonnet-5",
  "embedding_model_id": "cohere.embed-v4:0",
  "checkpoint_backend": "AuroraDataApiSaver",
  "checkpoint_durable": true,
  "checkpoint_required": true
}
```

If `checkpoint_backend` reads `MemorySaver (in-process)`, stop. The kiosk must
not present an in-process checkpointer as durable. With
`LANGGRAPH_CHECKPOINT_REQUIRED=true` the backend refuses to start in that
state, so a running backend showing `MemorySaver` means the env was not loaded.

Memory endpoint:

```bash
curl -s http://127.0.0.1:8013/api/memory/trv_meridian_demo | jq '.facts | length'
```

Phase 4 smoke test (runs through AgentCore Runtime; expect 20 to 45 seconds):

```bash
curl -s -X POST http://127.0.0.1:8013/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "phase": 4,
    "message": "Find Tokyo trips that fit my saved preferences.",
    "customer_id": "trv_meridian_demo",
    "memory_enabled": true
  }' | jq '.message, .conversation_id, (.products | length)'
```

## 4) Durable workflow smoke test

1. Open `/showcase?view=recovery` and press **Start recovery**.
2. Confirm the response reports `workflow_status: paused` with the single
   follow-up **Resume workflow from checkpoint**; the trace shows
   `checkpoint_durable=true` and `checkpointer=AuroraDataApiSaver`.
3. Stop and restart only the backend.
4. Press **Resume and request hold** without clearing the browser.
5. Confirm the same thread resumes at `availability`, the hold node reports
   `cedar_decision=allow`, and **System evidence** shows two executions with
   different worker ids plus one booking receipt.

Do not present `MemorySaver` as durable. It is an in-process fallback only.

## 5) Gateway smoke test (direct)

Run once before going live:

```bash
cd meridian
venv/bin/python scripts/verify_agentcore.py        # every row green, policy engine ACTIVE · ENFORCE
venv/bin/python scripts/smoke_gateway_tools.py     # four tools listed, one package read through the gateway
venv/bin/python scripts/smoke_production_turn.py   # PASS unconfirmed denied · PASS confirmed held · PASS over budget denied
```

The four gateway tools are `SemanticTripSearchLambda___semantic_trip_search`,
`MeridianHolds___get_package_details`, `MeridianHolds___create_courtesy_hold`,
and `MeridianHolds___confirm_booking`.

## 6) Quick recovery playbook

### A) Gateway error: `no targets were configured`

- Re-attach the target and deploy. Replace `123456789012` with the AWS account
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

### B) Phase 4 returns zero packages unexpectedly

- Verify the tool name in `.env`:

```bash
cd meridian
rg "AGENTCORE_GATEWAY_SEARCH_TOOL" .env
```

Expected value: `SemanticTripSearchLambda___semantic_trip_search`.

- Restart the backend after any `.env` change.

### C) Holds refused with `insufficient_inventory`

Repeated rehearsals confirm bookings against the same Tokyo package. Release
the demo traveler's bookings, then restart the loop:

```bash
cd meridian
venv/bin/python scripts/release_demo_bookings.py --dry-run
venv/bin/python scripts/release_demo_bookings.py
```

### D) Build or demo UI issues

- Restart the frontend dev server.
- Hard refresh the browser (`Cmd+Shift+R`).

## 7) Walkthrough mode (2 hours later)

Recommended:
- Reuse the same deployed AgentCore resources.
- Keep the kiosk stack running if possible.
- For the code walkthrough, explain deploy commands but avoid a live redeploy
  unless that is the explicit session objective.

## 8) Operator notes

- Prefer reliability over "fresh deploy theater".
- Keep one terminal focused on backend logs and one on frontend logs.
- If a fix is applied, rerun Section 3 smoke tests before resuming booth traffic.
