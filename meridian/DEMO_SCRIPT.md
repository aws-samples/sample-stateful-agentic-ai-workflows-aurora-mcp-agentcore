# Meridian L300 chalk talk

## Build stateful agentic AI workflows with Aurora, MCP, and AgentCore

**Duration:** 60 minutes: 45 minutes of walkthrough and 15 minutes of distributed discussion.
**Route:** Concierge → Capability ladder → Recovery desk → System evidence.
Use Solution briefing for the diagrams and implementation disclosures.

> Statefulness lives in durable stores, not database connections.

The audience should leave able to place an authorization check, a checkpoint,
and a business transaction at the correct boundary. The five phases are
configured teaching examples. SQL can express business logic; MCP can expose
semantic search; Strands can participate in durable workflows. Meridian adds
those capabilities deliberately so the audience can inspect each addition.

## Timing and whiteboard

| Segment | Minutes | Question for the room |
| --- | ---: | --- |
| Concierge and architecture | 5 | What must survive when this process disappears? |
| SQL, MCP and retrieval | 8 | Which checks belong in the tool, regardless of the model? |
| Identity, memory and Cedar | 10 | Who is allowed to choose the traveler, budget and confirmation? |
| Recovery and failure windows | 15 | What happens if the write commits but the response is lost? |
| Evidence and design tradeoffs | 7 | Which record supports each claim? |

Draw three paths, adding labels as the demonstration reaches them:

```text
Browser → FastAPI → AgentCore Runtime / Strands → Gateway / Cedar → Lambda → Aurora
             └──→ LangGraph worker ────────────────────────┘
                       │
                       └──→ Aurora checkpoints + execution leases + hold intent

AgentCore Memory: conversation context for the Runtime
Aurora: traveler facts, authorization, workflow progress and business records
```

The browser has a separate HTTP access boundary. The sample uses a shared demo
principal bound to Alex; it is not a multi-user identity implementation. The
backend, runtime and Lambda are separate AWS workloads with separate roles.

## Secure preflight

Keep Aurora private. Laptop access uses the **HTTPS RDS Data API**, with normal
certificate verification and IAM authorization. No public PostgreSQL ingress or
security-group change is required for this path. Keep both local servers bound
to `127.0.0.1`, including while on public or aircraft Wi-Fi.

Use the existing configured Aurora database. Do not run schema initialization,
seed/reset scripts, deploy commands, or release existing bookings as part of a
last-minute health check.

From `meridian/`, with the existing environment configured:

```bash
source venv/bin/activate
LANGGRAPH_CHECKPOINT_DSN= LANGGRAPH_AUTO_CHECKPOINT_DSN=false \
LANGGRAPH_CHECKPOINT_DATA_API=true LANGGRAPH_CHECKPOINT_REQUIRED=true \
LANGGRAPH_CHECKPOINT_INIT_ON_STARTUP=true \
uvicorn backend.main:app --host 127.0.0.1 --port 8013
```

In a separate terminal, from `meridian/frontend/`:

```bash
VITE_API_ORIGIN=http://127.0.0.1:8013 npm run dev -- --host 127.0.0.1 --port 5176 --strictPort
```

Verify listener ownership before choosing ports. Open
`http://127.0.0.1:5176/showcase?view=briefing`.

```bash
curl --fail http://127.0.0.1:8013/health
python scripts/test_aurora_connection.py
python scripts/verify_agentcore.py
```

For this Data API path, health must report `AuroraDataApiSaver` and
`checkpoint_durable: true`. `MemorySaver` cannot support the restart claim.
An existing private tunnel and pooled `AsyncPostgresSaver` are another supported
path; name the backend that actually ran. Do not open port 5432 publicly to fix
a rehearsal failure.

Check the actual Gateway mode and four tools. An unavailable Runtime, Gateway
or policy engine is a failed preflight, not permission to demonstrate an
ungoverned fallback. `status: healthy` alone only proves the application answers.

Use **Projector readability**, then **Present fullscreen**. Rehearse a cold and
warm request on the current network; quote the timings from this run. Keep the
architecture diagram open while a slow request completes.

## 1. Concierge and the three state questions

Show Alex's returned traveler context and one trip recommendation. Separate the
sample travel inventory from the AWS execution. No supplier is contacted and no
payment is taken.

Ask which state must survive each event:

| Event | Required state | Authoritative record |
| --- | --- | --- |
| Another conversational turn | Relevant preferences and prior context | Aurora facts; configured AgentCore Memory session |
| Worker termination | Execution position and saved task output | Aurora LangGraph checkpoint and pending writes |
| Retried hold | Original business intent, booking and expiry | `hold_requests`, `bookings`, `booking_lines` |

A conversation ID, a checkpoint ID and a booking ID identify different things.
A remembered sentence is not a booking receipt.

## 2. Capability ladder in eight minutes

Use one working prompt and carry the boundary prompt forward. Avoid spending
most of an L300 session on progressively nicer search results.

| Phase | Working prompt | Boundary to discuss |
| --- | --- | --- |
| SQL | Show me city trips under $2,000 per traveler. | The configured filter path does not implement comparison plus FX. |
| MCP | Compare three trip types and convert each price to euros. | A tool protocol does not choose a retrieval strategy. |
| Retrieval | Find a quiet, romantic wine-country retreat with a private villa. | Ranked candidates do not establish identity, availability or a committed hold. |
| Production | Recall my Tokyo plan and saved preferences: home airport, food needs, and budget. | Managed conversation context is not the workflow's recovery checkpoint. |

Trace: query embedding → independent semantic and lexical candidates → fusion
and reranking → returned packages. Explain that the query and corpus must use
the same embedding space. Quote returned rankings, not a promised fixed order.
A lexical fallback must be labeled as such; it does not prove semantic search.

Source: `backend/routers/chat.py`, `backend/agents/retrieval_03/search_agent.py`,
`backend/db/embedding_service.py` and `backend/mcp/concierge_server.py`.

## 3. Identity, memory and Cedar

Draw the authorization order:

```text
HTTP caller → authorized traveler claim
AWS workload → traveler_identity_bindings → restricted role + RLS → scoped SQL
Runtime/worker → Gateway policy decision → Lambda grant + RLS → atomic write
```

Run the RLS probe and inspect its actual positive and negative controls. Do not
memorize row counts: prior demonstrations can add records. Explain that RLS
protects the queries executed under the restricted role; it is not a universal
claim about every administrative principal or every application endpoint.

For the governed hold, show the arguments pinned by application code: traveler,
confirmation, party, budget and journey. Cedar evaluates the supplied context;
it does not discover human intent or independently read the saved budget.

| Demonstration | Expected evidence |
| --- | --- |
| Typed hold without confirmation | Actual Gateway policy refusal; no booking receipt |
| Confirmed hold within configured limits | Policy permission followed by a persisted booking receipt |
| Confirmed request over budget or over six travelers | Policy refusal before the target writes |
| IAM or traveler-grant failure | Access failure at that boundary, not a fabricated Cedar decision |

All clicked holds now use the governed path irrespective of the UI phase.
The HTTP phase number selects a demonstration view, not a weaker authorization
path. The Phase 5 worker uses the same Gateway hold tool with its persisted
intent and current execution lease.

The recovery demonstration treats starting/resuming disruption recovery as a
request for an automatic 15-minute courtesy hold on the leading eligible option.
The UI says this explicitly. A separate Concierge confirmation turns that hold
into a confirmed catalog booking. For a real travel product, persist approval of
exact terms and bind it to the verified end user before committing a purchase.

Source: `backend/http_auth.py`, `backend/db/rds_data_client.py`,
`meridian_agentcore/app/MeridianConcierge/turn_trace.py`, and
`meridian_agentcore/agentcore/agentcore.json`.

### Optional discussion: Cedar plus Dogwood

Cedar is configured today. Dogwood is an **assessed extension**, not enabled or
validated in this deployment. Proposed rule: a hold must follow a successful
lookup of the same package within five minutes in the same authenticated policy
session. That is an ordering condition, not proof of approval or fresh inventory.

Persist and propagate the policy session; do not assume the Runtime and backend
share history just because they reuse a string. Replace the existing hold permit
with the combined condition so the earlier permit cannot still admit the call.
Test an isolated engine; do not weaken the live Gateway to `LOG_ONLY`.
See [the integration assessment](docs/DOGWOOD_POLICY_ASSESSMENT.md).

## 4. Recovery: spend time at the failure windows

Run the canonical disruption prompt:

> My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options.

The recovery path is:

```text
classify → search → availability → prepare_hold → hold → synthesize
                                     │            │
                           checkpoint intent      Gateway → Aurora transaction
                           request + booking IDs
```

Meridian pauses this recovery after `search` by default. Open Recovery desk,
inspect the shortlist, and explain **Resume and request hold** before selecting
it. The saved intent must be checkpointed before the hold tool executes.

| Failure window | Recovery behavior to inspect | Evidence required |
| --- | --- | --- |
| Before the business write | Resume from the saved graph position after lease takeover | Same thread, pending node and successful replacement execution |
| Write committed, response lost | Retry the same intent; Aurora returns the existing booking | Same request ID, booking ID and original expiry; one booking for that request |
| Checkpoint saved after the hold | Continue remaining nodes without placing another hold | Saved hold channel and persisted booking |
| Competing worker while lease is live | Refuse the second execution | HTTP 409 and one running execution |
| Policy refusal or target failure | Do not invent a hold | Correct boundary/error plus actual booking readback |

A checkpoint and a Gateway side effect are **not one distributed transaction**.
The design accepts retried execution and makes the business write idempotent.
Do not call this exactly-once execution.

### Two different rehearsals

**Browser pause/restart:** stop and restart the backend without clearing the
browser, restore the same journey, then resume. This proves that execution state
survives a replacement process when a durable saver is active. A normal shutdown
is not the same failure as SIGKILL.

**Hard process death after the hold:** run `python scripts/kill_and_resume_demo.py`
in the configured rehearsal environment. It creates temporary demo records,
commits the hold, kills its own worker, waits for lease expiry, resumes, checks
the receipt and cleans up its records. This verifies a hold surviving a crash;
it does not by itself inject a lost Gateway response before checkpointing. Do
not describe it as that stronger failure test.

On System evidence inspect the successful execution ID, `resumed_from_checkpoint`,
worker IDs, hold creator, booking ID, expiry and observation time. A second
execution alone does not prove a successful resume. One displayed hold does not
prove all retry schedules are safe.

Source: `backend/agents/orchestration_05/workflow.py`, `hold_intent.py`,
`execution.py`, `backend/db/journey_store.py`, and
`scripts/migrations/008_hold_request_identity.sql`.

## 5. Close with tradeoffs and evidence

- Short authorization transactions commit before slow model calls; writes
  reauthorize. Scope is transaction-local, not a long-lived database session.
- Aurora owns capacity, uniqueness and replay. Policy owns admission to the
  tool. AgentCore Memory supplies context. None replaces the others.
- `AuroraDataApiSaver` is a sample adapter with bounded blob windows and explicit
  compatibility limits, including no metadata filtering in `alist`. Discuss
  pooled PostgreSQL checkpointing when throughput and adapter maintenance matter.
- The sample still needs production choices for end-user identity, approval
  records, rate limits, load behavior, retention and supplier/payment integration.
- Live evidence must be from the build and environment being shown. A historical
  successful rehearsal is useful backup material, labeled with its date.

If a refresh fails, System evidence identifies the retained observation. If the
network fails, continue with Solution briefing and source. Do not infer success
from a completed-looking screen or silently change the network or policy boundary.

## Validation before sharing

```bash
# From meridian/: offline tests cannot make unmocked network connections.
venv/bin/python -m pytest -m "not database"
venv/bin/ruff check backend scripts tests

# From meridian/frontend/:
npm run lint
npm run test:run
npm run build
```

Live database tests and the process-death rehearsal are separate proof. Run them
only against the intended rehearsal inventory and retain their actual results.
