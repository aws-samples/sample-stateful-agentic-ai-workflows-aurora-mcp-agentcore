# Meridian operations

How to provision Aurora, run Meridian with durable checkpoints, exercise its
recovery behavior, publish the web app, and troubleshoot a deployment.
Commands run from `meridian/` with the virtual environment active unless a
step says otherwise. The AgentCore deployment has its own
[runbook](AGENTCORE_DEPLOY_RUNBOOK.md).

## Provision Aurora

Meridian needs an Aurora PostgreSQL cluster with the RDS Data API enabled and a
Secrets Manager secret for its database user. If you already have one, set
`AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN` and `AURORA_DATABASE` in
`meridian/.env` and skip to [Prepare the database](#prepare-the-database).

`infra/bin/meridian-aurora.ts` is a CDK app that creates an encrypted Aurora
PostgreSQL 18 cluster: one private Serverless v2 writer (0.5 to 16 ACUs), the
Data API, TLS required, no inbound database port, seven-day backups, deletion
protection, and an RDS-managed master secret. It needs a VPC with two subnets
in different Availability Zones.

First run the read-only preflight. It checks your identity, subnet capacity,
the engine version, RDS quotas, the RDS service-linked role, CDK bootstrap and
IAM permissions, and makes no changes:

```bash
python scripts/provision_preflight.py --account <account-id> --region <region> \
  --vpc-id <vpc-id> --subnet-ids <subnet-in-az-a> <subnet-in-az-b> \
  --engine-version 18.3 --output .local/provision-preflight.json
```

Then review and deploy the stack:

```bash
cd infra
npm ci && npm run build
CONTEXT="-c account=<account-id> -c region=<region> -c clusterIdentifier=<new-cluster-name> \
  -c vpcId=<vpc-id> -c subnetIds=<subnet-in-az-a>,<subnet-in-az-b> -c engineVersion=18.3"
npx cdk diff   --app 'node dist/bin/meridian-aurora.js' $CONTEXT
npx cdk deploy --app 'node dist/bin/meridian-aurora.js' $CONTEXT
```

The stack outputs `ClusterArn` and `MasterSecretArn`; copy them into
`AURORA_CLUSTER_ARN` and `AURORA_SECRET_ARN`, and set `AURORA_DATABASE=meridian`.
Passing `-c snapshotArn=<snapshot-arn>` restores from a snapshot instead; the
restored cluster keeps the snapshot's database users, so keep using the
existing secret.

To delete the cluster, first turn off deletion protection. The stack takes a
final snapshot of the cluster and retains the writer instance, subnet group,
security group and parameter group, so `cdk destroy` alone does not remove
every billable resource; delete the retained resources and any snapshots you
do not need.

## Prepare the database

For a new, empty database:

```bash
python scripts/init_aurora_schema.py   # base schema and the meridian_app RLS role
python scripts/apply_migrations.py     # journeys, checkpoints, hold identity, booking functions
python scripts/seed_data.py            # catalog with embeddings, the demo traveler, and a grant for your identity
```

`init_aurora_schema.py` and a full `seed_data.py` refuse to run when Meridian
tables or data already exist. For an existing database, keep its journeys and
bookings and run only `python scripts/apply_migrations.py`. A database created
before traveler grants existed may also need
`python scripts/bind_current_identity.py`, which grants your current IAM or
AgentCore workload access to the demo traveler.

Each workload that sets a traveler scope needs its own grant: the backend's
identity (`seed_data.py` or `bind_current_identity.py`), the holds Lambda role
(`bind_gateway_workload.py`) and, for the hosted app, the App Runner instance
role (`bind_web_backend_role.py`).

## Run with durable checkpoints

The quick start in the [repository README](../../README.md#quick-start) starts
the backend with these settings:

| Variable | Value | Effect |
| --- | --- | --- |
| `LANGGRAPH_CHECKPOINT_DATA_API` | `true` | Store checkpoints in Aurora through the Data API (`AuroraDataApiSaver`) |
| `LANGGRAPH_AUTO_CHECKPOINT_DSN` | `false` | Do not build a PostgreSQL DSN from other settings |
| `LANGGRAPH_CHECKPOINT_REQUIRED` | `true` | Refuse to start without a durable checkpoint store |
| `LANGGRAPH_CHECKPOINT_INIT_ON_STARTUP` | `true` | Initialize and probe the store at startup |

Confirm with `curl http://127.0.0.1:8013/api/health`:

```json
{
  "checkpoint_backend": "AuroraDataApiSaver",
  "checkpoint_durable": true,
  "checkpoint_required": true
}
```

If it reports `MemorySaver`, checkpoints live in the process and do not survive
a restart. `/api/health` also runs a live Aurora `SELECT 1` (2 second timeout,
cached for 10 seconds) and reports `status` as `healthy` or `degraded`, with
`aurora_reachable`, `degraded_component` and `degraded_error_class` naming what
failed. The checkpoint fields are the configured backend, not a second probe.
`/health` is process liveness only. After renewing expired AWS credentials,
restart the backend.

To checkpoint over a direct PostgreSQL connection instead, supply
`LANGGRAPH_CHECKPOINT_DSN` (or the discrete `LANGGRAPH_CHECKPOINT_*` settings)
from a network location that can reach the private cluster endpoint, with TLS
certificate and hostname verification. The backend then uses one bounded pool
and `AsyncPostgresSaver`. Use a least-privilege database role for checkpoints,
not the master user. `scripts/start_checkpoint_tunnel.sh` opens an SSM port
forward for this path.

Before exposing the API beyond loopback, set `MERIDIAN_API_TOKEN` and an
explicit `CORS_ORIGINS` list.

## Exercise recovery failures

The canceled-flight workflow runs:

```text
classify → search → availability → prepare_hold → hold → synthesize
                                    │              │
                           checkpoint intent      Gateway → Aurora transaction
                           (request and booking IDs)
```

A checkpoint and a Gateway write are separate transactions. The design makes
the write idempotent and the execution resumable:

| Failure | Recovery behavior | Evidence |
| --- | --- | --- |
| Worker stops before the hold | Another worker takes over after the lease expires and resumes from the saved node | Same thread and pending node, a successful replacement execution |
| Hold committed, response lost | The retried intent returns the existing booking | Same request ID, booking ID and original expiry; one booking for the request |
| Worker stops after the hold is checkpointed | The remaining nodes run without placing another hold | The saved hold and the persisted booking |
| A second worker while the lease is live | The second execution is refused | HTTP 409 and one running execution |
| Policy refusal or target failure | No hold is reported | The actual boundary or error, and the booking readback |

**Restart from the browser.** Run the canceled-flight prompt in the Workflow
phase; it pauses after `search`. Stop the backend with `Ctrl+C`, start it with
the same settings, confirm `/api/health` is durable, and select **Resume and
request hold** on the Recovery desk. The workflow continues at `availability`
on the same `thread_id`; `search` does not run again.

**Scripts against real Aurora and Gateway calls.** Both need the deployed
AgentCore resources and create, then remove, their own journey, checkpoint and
hold records. They do not reset the catalog or touch other bookings.

```bash
export LANGGRAPH_CHECKPOINT_DSN=
export LANGGRAPH_AUTO_CHECKPOINT_DSN=false
export LANGGRAPH_CHECKPOINT_DATA_API=true
export LANGGRAPH_CHECKPOINT_REQUIRED=true
python scripts/kill_and_resume_demo.py
python scripts/lost_response_demo.py
```

- `kill_and_resume_demo.py` places a hold through the gateway, kills its worker
  with SIGKILL after the hold is checkpointed, shows a second worker refused
  until the lease expires, then resumes and verifies one hold with the same
  booking ID and original expiry. `DEMO_LEASE_SECONDS` (default 20) sets the
  lease; a cold worker needs most of that before its first heartbeat.
- `lost_response_demo.py` receives a real committed hold from the gateway,
  discards the response and raises a timeout, so the `hold` node stays pending.
  A replacement worker retries the saved intent and gets the same booking and
  expiry. It also checks that Cedar denies unconfirmed and over-budget calls
  with the same request identity. The write and the retry are real; only the
  lost response is simulated.

On System evidence, a successful recovery shows the replacement execution ID,
`resumed_from_checkpoint`, the worker IDs, the hold creator, and the booking ID
and expiry. A second execution alone does not prove that the resume succeeded.

A confirmed booking uses catalog capacity. To release the demo traveler's
bookings, list them first:

```bash
python scripts/release_demo_bookings.py --dry-run
python scripts/release_demo_bookings.py --booking-id <booking-id>
```

## Publish the web app

`scripts/publish.py` publishes the frontend to S3 behind CloudFront and the
backend image to an existing AWS App Runner service, using the CDK app in
`infra/bin/meridian-web.ts` (stacks `MeridianWebRoles`, `MeridianWebBackend`
and `MeridianWeb`). It needs the exact account, Region and App Runner service
ARN. Without `--apply` it builds the frontend, synthesizes the stacks and shows
their diffs; with `--apply` it deploys them:

```bash
python scripts/publish.py --account <account-id> --region <region> --service-arn <app-runner-service-arn>
python scripts/publish.py --account <account-id> --region <region> --service-arn <app-runner-service-arn> --apply
```

The publisher does not create or delete App Runner services, rotate
credentials or read secret values. App Runner reads the API token from the
Secrets Manager secret `meridian/web/api-token`. The release record is written
to `.local/hosted-release.json` (gitignored). `python scripts/published.py`
reads that record and checks reachability; `--url` prints just the URL. Set
`MERIDIAN_HOSTED_AUTH` at runtime for an authenticated check. The helper never
copies or persists credentials. Confirm current asset hashes, image identity
and a streamed turn through CloudFront before marking a release verified.
[App Runner no longer accepts new customers](https://aws.amazon.com/apprunner/),
so this path applies to accounts that already use it; a new account needs a
different container host for the backend, such as Amazon ECS.

The App Runner instance role is a workload and needs its own grant to the demo
traveler. Run this once after the `MeridianWebRoles` stack exists; without it,
Phase 4 and Phase 5 requests on the hosted site fail with
`aws_iam subject is not authorized for traveler`:

```bash
python scripts/bind_web_backend_role.py
```

A Git push runs CI only; it does not deploy the hosted app or the AgentCore
resources.

`scripts/validate_demo.py` runs the full demo contract (catalog, phases, holds,
confirmation and cleanup) against a backend. It uses real services and removes
only its own records. Against a hosted backend it needs
`--allow-hosted-demo-writes`, and `MERIDIAN_HOSTED_AUTH` must supply the site's
basic-auth credentials at run time. Resolve that value from Secrets Manager
when you run the command; do not store it in a file.

To tear down the hosted app, run `npx cdk destroy MeridianWeb` from `infra/`,
delete the App Runner service, then run
`npx cdk destroy MeridianWebBackend MeridianWebRoles`.

The container starts through `backend/launch.py`, which opens the port before
the application finishes loading; [AGENTCORE_LEARNINGS.md](AGENTCORE_LEARNINGS.md)
explains why App Runner needs this.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `AgentCore platform not configured` | The backend has no runtime, gateway or memory ID. Run `python scripts/sync_agentcore_env.py --write` and restart the backend. Production does not fall back to direct Aurora access. |
| `runtimeSessionId ... valid min length: 33` | The runtime session ID is too short. Restart the backend on current code; `_build_runtime_session_id` in `backend/agentcore/runtime.py` builds a valid ID. |
| Gateway reports `no targets were configured` | A target is missing. Check that `meridian-semantic-trip-search` exists, render the config and run `agentcore deploy -y`. |
| Production returns no packages | Run `python scripts/verify_agentcore.py` (expect four tools) and watch `agentcore logs --runtime MeridianConcierge --follow` while you repeat the prompt. |
| A hold returns `traveler_not_authorized` | The holds Lambda role has no grant. Run `python scripts/bind_gateway_workload.py`. |
| Hosted Phase 4 or 5 returns `aws_iam subject is not authorized for traveler` | The App Runner instance role has no grant. Run `python scripts/bind_web_backend_role.py`. |
| Every hold is denied, including confirmed ones | If `verify_agentcore.py` shows the policy engine `MISSING`, render (it must print `Configuration complete.`) and deploy. If it shows `ACTIVE` and `ENFORCE`, read the denied span's `arguments`: the ceiling is the traveler's saved per-person budget times the party size, and packages above it are refused. |
| The runtime replies but the trace has no gateway spans | The runtime runs older code. `agentcore status` shows the version; `agentcore deploy -y` publishes `app/MeridianConcierge`. |
| `npx agentcore` fails with a cloud assembly schema version error | `npx` resolved an older cached CLI. Run the globally installed `agentcore`. |
| `/api/health` reports `MemorySaver` | No durable checkpoint store resolved. Set the variables in [Run with durable checkpoints](#run-with-durable-checkpoints). |

## Waits, retries and readback

The browser bounds ladder chat, hold and confirmation waits to 55 seconds.
The main Concierge streams real AgentCore text over `/api/chat/stream`, with a
120-second browser deadline. The stream sends a heartbeat every 10 seconds
while awaiting events, below CloudFront's 60-second origin idle timeout.
App Runner still imposes a 120-second total request limit; streaming does not
extend it. A partial reply is marked incomplete and is never retried automatically. The runtime client uses a 45-second
socket read timeout with one attempt; this is not an end-to-end workflow
deadline, and a multi-step workflow can outlive the browser's wait. Its thread
stays addressable in the URL, and the UI reads the saved outcome back before
offering a retry. Stopping the wait does not undo a committed transaction.

An unconfirmed chat turn without a hold or booking target is retried once,
after 250 ms, if the connection closes before the runtime responds; it keeps
the same payload and conversation and session IDs. Holds and confirmations are
single attempts: read back their outcome before retrying. Timeouts, partial
streams, permission errors and runtime errors are not retried.

Direct-hold intent IDs and booking references are stored per traveler in the
browser. Receipts come from authenticated, RLS-scoped reads from Aurora. If
browser storage is blocked, the app does not send a new direct hold. Do not
clear browser storage to work around an uncertain hold; open its journey and
booking first.
