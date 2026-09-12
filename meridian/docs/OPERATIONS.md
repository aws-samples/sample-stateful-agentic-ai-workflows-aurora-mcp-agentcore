# Meridian Operations

Everything for running the demo: **deploy** AgentCore (day-before), **run the
booth/kiosk** (day-of), and the **gotchas** we hit getting Phase 4 live.

- Deploy procedure → [Part 1](#part-1--deploy-agentcore-day-before)
- Booth / kiosk operation → [Part 2](#part-2--kiosk--booth-runbook-day-of)
- Lessons & gotchas → [Part 3](#part-3--learnings--gotchas)

Default region for this demo: **`us-east-1`**. Replace sample account
placeholders such as `123456789012` with the AWS account running the workshop.
Resources are named with the `meridianv2` project prefix.

---

# PART 1 — Deploy AgentCore (day-before)

End-to-end deploy of Runtime + Gateway + Memory for Phase 4. ~15 min hands-on,
~5–8 min CDK wait. Resources persist until explicitly destroyed — deploying the
day before is fine.

## What gets deployed

| Resource | Name | Purpose at the talk |
|---|---|---|
| **Runtime** | `MeridianConcierge` | The Phase 4 agent: Strands tool loop over the gateway, AgentCore Memory session, ADOT spans, streamed trace |
| **Memory** | `meridian_session` | The agent's session store; restored and written by the Strands session manager |
| **Gateway** | `meridian-aurora` | Managed MCP endpoint, AWS_IAM inbound, Cedar policy engine attached in ENFORCE mode |
| **Gateway targets** | `SemanticTripSearchLambda`, `MeridianHolds` | `semantic_trip_search`; `get_package_details` and the identity-checked `create_courtesy_hold` and `confirm_booking` |
| **Policy engine** | `MeridianGovernance` | Permits the reads; permits the hold only when confirmed, at most 12 hours, at most 6 travelers, within budget; permits the booking confirmation only when confirmed and within budget |

All of them are declared in
[`meridian_agentcore/agentcore/agentcore.json`](../meridian_agentcore/agentcore/agentcore.json).
Never rename `meridian_session`: a rename replaces the memory and drops the
seeded Tokyo history. Memory uses the **`SEMANTIC`** strategy over
`/users/{actorId}/sessions/{sessionId}`.

## Prerequisites

```bash
# 1. AWS credentials (Isengard / SSO / aws configure). Confirm:
aws sts get-caller-identity

# 2. Region pinned to us-east-1 (Bedrock access for Cohere Rerank + Claude models):
export AWS_DEFAULT_REGION=us-east-1

# 3. AgentCore CLI (Node-based, installed globally):
npm install -g @aws/agentcore
agentcore --version

# 4. Node 20+ for the CDK synth step:
node --version
```

## Deploy

```bash
cd meridian

# The holds Lambda reads its Aurora settings from SSM (values come from .env):
python scripts/publish_gateway_parameters.py

cd meridian_agentcore

# Validate the config against the current CLI schema first:
agentcore validate --json

# Synth + deploy (idempotent — updates the existing stack in place):
agentcore deploy -y

# The holds Lambda is a workload: grant its execution role access to Alex.
cd .. && python scripts/bind_gateway_workload.py
```

On a fresh account, deploy in two passes: first with `policyEngines: []` and
no `policyEngineConfiguration` on the gateway, then restore both and deploy
again. The Cedar policies validate against the `MeridianHolds` tool schema, so
the target must exist first.

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
cd meridian
python scripts/verify_agentcore.py        # Runtime, Gateway, Memory, policy engine ACTIVE · ENFORCE, 4 tools, observability
python scripts/smoke_gateway_tools.py     # tools/list + get_package_details signed from this laptop
python scripts/smoke_production_turn.py   # search, unconfirmed hold denied, confirmed hold held, over budget denied
python scripts/kill_and_resume_demo.py    # Phase 5: hold through the gateway, SIGKILL the worker, resume with the same booking
```

For the published site, request `/` without credentials (expect 401) and `/health`
with the basic credential from `.local/published.json` (expect 200 and
`"checkpoint_durable": true`).

In the showcase trace panel you should see (real, not faked):
- `AgentCore Identity resolved` and `Workload traveler grant allowed`
- `AgentCore Runtime · turn started`, then `AgentCore Gateway · tools/list` with four tools
- `AgentCore Memory · session restored` with the event count
- `AgentCore Gateway · tools/call → semantic_trip_search` and its result
- On a Hold click: `tools/call → create_courtesy_hold`, its result with the Lambda's workload subject and `traveler_grant: allow`, and the hold receipt
- On a Confirm click: `tools/call → confirm_booking`, its result under `meridian_booking_governance`, and the receipt as **Confirmed booking** (same booking id, status `confirmed`)
- On a typed hold: `Hold refused by Cedar policy · Denied by policy`
- `AgentCore Runtime · turn complete` with the trace id and a CloudWatch link

Spans for the trace id land in `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`,
stream `spans`; application logs carry the same trace id in the `runtime-logs-*` streams.

> Transaction-search trace indexing takes **~10 min** after deploy to fully
> activate. Don't judge missing trace spans in the first few minutes.

## Publish behind CloudFront

The CDK app in `infra/` publishes the app to a password-protected CloudFront URL:
the Vite build in a private S3 bucket (origin access control), the backend as a
container on App Runner (1 vCPU, 2 GB, one instance kept warm), and a viewer
function that enforces basic auth, injects the backend bearer token on `/api/*`
and `/health`, and rewrites `/showcase` and friends to `index.html`. The
backend runs with `ENVIRONMENT=production`, so it refuses any caller without
the token; the App Runner URL is not an open door. `publish.py` deploys three
stacks and one SDK-created service, in order: `MeridianWebRoles` holds the App
Runner instance and ECR access roles and goes first, because App Runner cannot
deploy a service whose roles were created moments earlier (the script waits 90
seconds whenever it changes them); `MeridianWebBackend` builds and pushes the
backend image; the App Runner service `meridian-web` is then created or updated
with the SDK and the script waits until it runs, deleting and retrying a failed
creation; `MeridianWeb` is the site, routed to the service host. The service
carries no optional setting at all: App Runner in us-east-1 refused every
deployment that had a custom auto scaling configuration, a health check
interval, an explicit egress configuration, a tag list, or a Secrets Manager
reference for the token, and CloudFormation always sends a tag list, which is
why the service is not a stack resource. The bearer token therefore reaches the
container as a runtime environment variable (the same value already sits in the
CloudFront KeyValueStore, and it guards only the origin behind CloudFront); the
secret in Secrets Manager remains the operator's record of it. The
container starts through `backend/launch.py`, which opens port 8000 at once and
hands the socket to uvicorn, because App Runner also refused deployments whose
port stayed closed for the thirty seconds the backend needs to load on one vCPU;
the default TCP health check passes immediately and requests wait in the backlog
until startup has initialised the Aurora checkpoint backend.

```bash
cd meridian
finch vm start                      # Docker works too; the image is built for linux/amd64
python scripts/publish.py           # secret → frontend build → cdk deploy → KeyValueStore
python scripts/publish.py --skip-frontend   # redeploy after backend changes
python scripts/bind_web_backend_role.py     # once per roles stack: grant the instance role access to Alex
```

What the script does, in order: mints a basic-auth password and a bearer token
(or reuses the ones in `.local/published.json`), writes the token to Secrets
Manager (`meridian/web/api-token`, never read back), builds `frontend/dist`,
deploys `MeridianWebRoles` and `MeridianWebBackend`, creates or updates the App
Runner service, deploys `MeridianWeb`, all in the region of Aurora and AgentCore
with the non-secret settings copied from `.env`, writes the two credentials to the
CloudFront KeyValueStore through the AWS CLI, and records the URL. The instance
role is scoped to Bedrock invoke, the Aurora Data API on one cluster, one Aurora
secret, and `InvokeAgentRuntime` on the Meridian runtime.

That instance role is a workload like the holds Lambda. Run
`scripts/bind_web_backend_role.py` once after the roles stack exists (it binds the
role's RoleId in `traveler_identity_bindings`); until then every Phase 4 and
Phase 5 request on the published site fails with
`aws_iam subject is not authorized for traveler trv_meridian_demo`.

Verify with the credentials from `.local/published.json`:

```bash
curl -s -u meridian:PASSWORD https://<distribution>.cloudfront.net/health | jq .
curl -s -u meridian:PASSWORD -X POST https://<distribution>.cloudfront.net/api/chat \
  -H "Content-Type: application/json" \
  -d '{"phase":1,"message":"Show me city trips under $2,000 per traveler.","customer_id":"trv_meridian_demo"}' \
  | jq '(.products | length), [.activities[].title]'
```

Tear down with `cd meridian/infra && npx cdk destroy MeridianWeb`, then
`aws apprunner delete-service --service-arn <backendServiceArn from .local/published.json>`,
then `npx cdk destroy MeridianWebBackend MeridianWebRoles`; the secret
and the credentials file stay unless you delete them.

## If AgentCore fails on stage

The Phase 4 code path does **not** pretend to run Production mode without
AgentCore. If Runtime, Gateway, or Memory are missing, the chat response shows
`AgentCore platform not configured` and the trace carries the concrete missing
resource list. Narrate it plainly: *"Production mode is the managed AgentCore
path. We fail closed instead of silently swapping in a different architecture."*

## Teardown (after the event)

Resources bill while they exist. When done:
```bash
cd meridian/meridian_agentcore
agentcore remove gateway-target --name SemanticTripSearchLambda -y
agentcore remove gateway --name meridian-aurora -y
agentcore remove memory --name meridian_session -y
agentcore remove agent --name MeridianConcierge -y
agentcore validate --json
agentcore deploy -y
```

---

# PART 2 — Kiosk / booth runbook (day-of)

Keep this open in one tab while operating the booth.

## 1) Preflight (10–15 min before)

```bash
aws sts get-caller-identity                 # AWS auth works

cd meridian/meridian_agentcore
agentcore status --json                     # resources healthy

cd ..
venv/bin/python scripts/verify_agentcore.py  # checks resolved configuration without printing .env
```
Expected `.env` keys: `AGENTCORE_RUNTIME_ARN`, `AGENTCORE_GATEWAY_URL`,
`AGENTCORE_GATEWAY_SEARCH_TOOL`, `AGENTCORE_MEMORY_ID`. Then run
`venv/bin/python scripts/verify_agentcore.py` and expect every row green,
including `Policy engine … ACTIVE · ENFORCE` and `Gateway tools … 4 tools`.

## 2) Start the durable stack

For a laptop, use the existing HTTPS Data API endpoint. Aurora stays private;
no public PostgreSQL ingress or security-group change is needed. Keep certificate
verification enabled and bind local servers to loopback, including on public Wi-Fi.

Terminal 1 — fail-closed backend:
```bash
cd meridian
source venv/bin/activate
LANGGRAPH_CHECKPOINT_DSN= LANGGRAPH_AUTO_CHECKPOINT_DSN=false \
LANGGRAPH_CHECKPOINT_DATA_API=true LANGGRAPH_CHECKPOINT_REQUIRED=true \
LANGGRAPH_CHECKPOINT_INIT_ON_STARTUP=true \
uvicorn backend.main:app --host 127.0.0.1 --port 8013
```

Terminal 2 — frontend:
```bash
cd meridian/frontend
VITE_API_ORIGIN=http://127.0.0.1:8013 npm run dev -- --host 127.0.0.1 --port 5176 --strictPort
```

Open `http://127.0.0.1:5176/showcase`. The checks below use the backend on 8013.
Verify existing listeners before choosing ports.

An existing private SSM tunnel with a separately supplied checkpoint DSN can
instead use pooled `AsyncPostgresSaver`. Keep the DB private and configure TLS
certificate/hostname verification for that connection. The application does not
retrieve database passwords. Do not copy a password into the runbook.

Before exposing an API to other people, configure HTTP authentication and an
explicit CORS allow-list. Local preview does not require a network bind.

## 3) Health checks (must pass)

```bash
curl -s http://127.0.0.1:8013/health | jq .                       # Sonnet 5 + cohere.embed-v4:0
curl -s http://127.0.0.1:8013/api/memory/trv_meridian_demo | jq . # Alex Morgan facts

# Phase 4 smoke — identity, RLS, AgentCore Runtime, Gateway tools, Cedar, Aurora end to end:
curl -s -X POST http://127.0.0.1:8013/api/chat \
  -H "Content-Type: application/json" \
  -d '{"phase":4,"message":"A slow week somewhere we can drink good wine","customer_id":"trv_meridian_demo"}' \
  | jq '.message, .conversation_id, (.products | length), [.activities[].title]'

# Governed hold smoke — the click is the confirmation; expect an order with a HLD- id:
curl -s -X POST http://127.0.0.1:8013/api/chat/order \
  -H "Content-Type: application/json" \
  -d '{"phase":4,"product_id":"TKY-001","quantity":2,"traveler_id":"trv_meridian_demo","action":"hold"}' \
  | jq '.order.order_id, .order.hold_expires_at, [.activities[] | select(.telemetry.status=="denied" or .activity_type=="order") | .title]'
```

For the stage proof, `/health` must include:

```json
{
  "checkpoint_backend": "AuroraDataApiSaver",
  "checkpoint_durable": true,
  "checkpoint_required": true
}
```

If it reports `MemorySaver`, stop. That is an honest local fallback, not the
Aurora durability proof.

## 4) Gateway and governed-path smoke tests (direct — run once before going live)

```bash
cd meridian
venv/bin/python scripts/smoke_gateway_tools.py CTY-002   # tools/list + get_package_details from this laptop
venv/bin/python scripts/smoke_production_turn.py         # runtime → gateway → Cedar → Aurora; expect three PASS lines
```

The second script places one real 12-hour hold on a Tokyo package for Alex.
Holds expire on their own; `tests/test_order_hold.py` shows how to purge one.

## 5) Prove pause, restart, and resume

1. Open `http://127.0.0.1:5176/showcase`, choose **Capability ladder**, then
   **Workflow**, and run:
   `My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options.`
2. Confirm the reply says the workflow paused and the proof names
   the configured durable backend with `next=availability`.
3. Stop only the backend with `Ctrl+C`. Leave the browser and frontend running.
4. Restart the same backend command with the durable settings from section 2.
5. Confirm `/health` is durable again.
6. Select **Continue at recovery desk**, then **Resume and request hold** in
   the existing conversation. This requests a 15-minute package hold.
7. Confirm `Workflow resumed from checkpoint` uses the same `thread_id` and
   continues at `availability`; the `search` node must not run twice.

This is the title claim made visible: the worker process disappears, while the
execution position survives in Aurora's `checkpoints`, `checkpoint_blobs`, and
`checkpoint_writes` tables.

For a separate hard-kill rehearsal, run `venv/bin/python scripts/kill_and_resume_demo.py`:
worker one places the hold through the gateway tool, is SIGKILLed, a second
worker is refused until the lease clears, and the resumed run reports one hold
with the same booking id and the original expiry. `DEMO_LEASE_SECONDS` (default
20) sets the lease; a cold worker needs most of that before its first heartbeat.
This script kills after the hold is checkpointed. It does not inject a lost
response between the business commit and checkpoint commit. See
[`DEMO_SCRIPT.md`](../DEMO_SCRIPT.md) for the three failure windows.

## 6) Recovery playbook

**`runtimeSessionId ... valid min length: 33`** — restart backend; verify
`rg "_build_runtime_session_id" backend/agentcore/runtime.py`.

**Gateway `no targets were configured`** — re-attach the Lambda target and redeploy:
```bash
cd meridian/meridian_agentcore
agentcore add gateway-target --name SemanticTripSearchLambda --gateway meridian-aurora \
  --type lambda-function-arn \
  --lambda-arn arn:aws:lambda:us-east-1:123456789012:function:meridian-semantic-trip-search \
  --tool-schema-file ./gateway_targets/semantic_trip_search/tool-schema.json
agentcore deploy -y
```

**Phase 4 returns zero packages** — the runtime found nothing or the gateway
search tool is missing. Run `venv/bin/python scripts/verify_agentcore.py`
(expect four tools) and `agentcore logs --runtime MeridianConcierge --follow`
while repeating the prompt.

**A Hold click answers `traveler_not_authorized`** — the holds Lambda role lost
its grant: `venv/bin/python scripts/bind_gateway_workload.py`.

**Phase 4 or 5 on the published site answers `aws_iam subject is not authorized
for traveler`** — the App Runner instance role has no grant:
`venv/bin/python scripts/bind_web_backend_role.py`.

**Every hold is denied, including a confirmed one** — the policy engine may be
detached (`verify_agentcore.py` shows `Policy engine … MISSING`): redeploy with
`agentcore deploy -y`. If it shows `ACTIVE · ENFORCE`, read the denied span's
`arguments` field: the ceiling comes from Alex's `budget_cap` fact ($3,200 per
person) times the party size, and packages above it are refused on purpose.

**Runtime replies but the trace has no gateway spans** — the runtime is on an
older version. `agentcore status` shows the version; `agentcore deploy -y`
publishes the current `app/MeridianConcierge` code.

**UI issues** — restart frontend dev server; hard refresh (`Cmd+Shift+R`).

## 7) Operator notes

- Reuse the same deployed stack for both kiosk and the code walkthrough — avoid
  "fresh deploy theater" unless deploying is the explicit lesson.
- One terminal on backend logs, one on frontend logs.
- After any fix, rerun the Section 3 health checks before resuming booth traffic.
- The kiosk auto-loops real `/api/chat` calls on a timer → real Bedrock + Aurora
  spend. Stop it when not actively demoing.
- Never narrate durable recovery unless `/health` says
  `"checkpoint_durable": true` and the trace names the configured Aurora saver.

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
- **Cedar has no floats and needs required arguments.** Amounts are integer
  cents, and every argument a policy names is `required` in the tool schema.
- **A `forbid` deployed next to its `permit` fails validation.** CloudFormation
  creates policies in parallel; the forbid is validated before the permit exists
  and is rejected as overly restrictive. One permit with all conditions deploys.
- **The CDK `lambda` target has no environment variables.** The holds Lambda
  reads its Aurora settings from SSM (`scripts/publish_gateway_parameters.py`).
- **The gateway Lambda is a workload.** Grant its role in
  `traveler_identity_bindings` with `scripts/bind_gateway_workload.py`.
- **The model retries a refused hold.** The runtime hook settles the hold once
  per turn and hands the model the explained decision instead of the raw error.
  See [AGENTCORE_LEARNINGS.md](AGENTCORE_LEARNINGS.md) for the full list.

## Why two folders (`meridian/` and `meridian/meridian_agentcore/`)?

- **`meridian/`** = the product app (FastAPI + frontend + tests + demo code).
- **`meridian/meridian_agentcore/`** = the AgentCore CLI project (infra-as-code
  for the AgentCore resources + deployment state).

The app reads deployed values from `.env` / the CLI deployed-state file at
runtime. The split is intentional and fine; could be merged later if desired.
