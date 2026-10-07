# Meridian operations

How to provision Aurora, run the workflow Runtime, exercise its
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
python scripts/apply_migrations.py     # journeys, snapshots, hold identity, booking functions
python scripts/seed_data.py            # catalog with embeddings, the sample traveler, and a grant for your identity
```

`init_aurora_schema.py` and a full `seed_data.py` refuse to run when Meridian
tables or data already exist. For an existing database, keep its journeys and
bookings and run only `python scripts/apply_migrations.py`. A database created
before traveler grants existed may also need
`python scripts/bind_current_identity.py`, which grants your current IAM or
AgentCore workload access to the sample traveler.

Each workload that sets a traveler scope needs its own grant: the backend's
identity (`seed_data.py` or `bind_current_identity.py`), the holds Lambda role
(`bind_gateway_workload.py`) and, for the hosted app, the App Runner instance
role (`bind_web_backend_role.py`).

## Run the workflow Runtime

Phase 5 runs as a Strands Graph in the `MeridianWorkflow` AgentCore Runtime. The
backend invokes it and relays the result; it saves one snapshot per node to the
Aurora table `workflow_snapshots`. The quick start in the
[repository README](../../README.md#quick-start) starts the backend. Before Phase 5
works you need three things:

1. Migrations 014 to 017 applied (`python scripts/apply_migrations.py`). They
   create `workflow_snapshots`, the `meridian_workflow` role and
   `workflow_session_stops`.
2. The Runtime's own database login. `meridian_workflow` is NOBYPASSRLS, owns
   nothing and reaches `meridian_app` only through `SET ROLE`. Create its
   password, its Secrets Manager secret and its access policy:

   ```bash
   python scripts/provision_workflow_login.py            # report only
   python scripts/provision_workflow_login.py --apply --write-env
   ```

   `--write-env` sets `AURORA_WORKFLOW_SECRET_ARN` in `.env`.
3. The deployed Runtime. Follow the [runbook](AGENTCORE_DEPLOY_RUNBOOK.md) to
   deploy `MeridianWorkflow`, then run `python scripts/sync_agentcore_env.py --write`
   so `.env` has `AGENTCORE_WORKFLOW_RUNTIME_ARN`, and
   `python scripts/bind_workflow_runtime.py` to grant the Runtime's role its
   traveler binding.

Confirm with `curl http://127.0.0.1:8013/api/health`:

```json
{
  "checkpoint_backend": "Aurora workflow_snapshots",
  "checkpoint_durable": true,
  "workflow_runtime_configured": true
}
```

`/api/health` runs one query (2 second timeout, cached for 10 seconds) that
checks that the `workflow_snapshots` and `workflow_session_stops` tables exist.
It reports `status` as `healthy` or `degraded`, with `aurora_reachable`,
`degraded_component` and `degraded_error_class` naming what failed. It reports
`degraded` if either table is missing, or if the workflow Runtime ARN is unset
outside development. The snapshot fields describe the configured store and the Runtime ARN,
not a second probe. `python scripts/smoke_workflow_runtime.py` pings the
deployed Runtime and touches no row. `/health` is process liveness only. After
renewing expired AWS credentials, restart the backend.

### Rotate the workflow login password

Re-run `python scripts/provision_workflow_login.py --apply` with `AWS_PROFILE`
set. It generates a new password, sends Postgres only its SCRAM-SHA-256
verifier, stores the new credential in the same secret and checks that the login
is `meridian_workflow` without BYPASSRLS. Between the `ALTER ROLE` and the secret
update, anything holding the old secret fails, so rotate while no workflow runs.
The same command repairs a half-finished run.

### Stop a Runtime session

`POST /api/journeys/{journey_id}/stop-session` stops the Runtime session of the
journey's active thread. On the Recovery desk this is **Stop runtime session** on
the continuity rail. The backend takes the thread from the journey under RLS, so
a caller can stop only their own journey's session. It answers 403 when the
traveler grant is denied, 404 when the
journey is not the traveler's, 409 when nothing is paused or running or the
session was already stopped, and 503 when the Runtime is not configured or the
stop failed.

Each stop is recorded in `workflow_session_stops` with the journey, thread,
Runtime session ID, who asked, and:

| `stopped_during` | Meaning | Lease |
| --- | --- | --- |
| `waiting` | The execution was paused, neither running nor finished, usually waiting for the traveler's review | Nothing to release |
| `running` | A worker was mid-run | Released as abandoned, so a resume claims at once |
| `finished` | The snapshot shows the Graph completed and only the release was outstanding | Closed as succeeded |

`last_step` is the last saved node and `released_execution_id` names the
execution whose lease the stop released. Read the records with the master role:

```sql
SELECT stopped_at, stopped_during, last_step, runtime_session_id
  FROM workflow_session_stops
 WHERE journey_id = '<journey-id>' ORDER BY stop_id DESC;
```

The next resume starts a new microVM on the same session ID. It claims the next
attempt and restores the newest snapshot, so `search` does not run again.

Where a mid-run stop lands depends on when it is pressed. It can land before
the hold, after the hold committed but before its snapshot was saved, or after
the run finished, which is recorded as `finished`. The `--during running` proof
can miss that window and then exits 2 with "missed the window"; run it again.
Only the local kill-and-resume proof stops at an exact step.

Before exposing the API beyond loopback, set `MERIDIAN_API_TOKEN` and an
explicit `CORS_ORIGINS` list.

## Exercise recovery failures

The canceled-flight workflow runs:

```text
classify → search → availability → prepare_hold → hold → synthesize
                                    │              │
                            saved intent          Gateway → Aurora transaction
                           (request and booking IDs)
```

A snapshot and a Gateway write are separate transactions. The design makes
the write idempotent and the execution resumable:

| Failure | Recovery behavior | Evidence |
| --- | --- | --- |
| Worker stops before the hold | Another worker takes over after the lease expires and resumes from the saved node | Same thread and pending node, a successful replacement execution |
| Hold committed, response lost | The retried intent returns the existing booking | Same request ID, booking ID and original expiry; one booking for the request |
| Worker stops after the hold is saved | The remaining nodes run without placing another hold | The saved hold and the persisted booking |
| A second worker while the lease is live | The second execution is refused | HTTP 409 and one running execution |
| Runtime session stopped by the presenter | The next resume starts a new microVM on the same session and restores the newest snapshot | A `workflow_session_stops` row, a new execution attempt and the same hold |
| Policy refusal or target failure | No hold is reported | The actual boundary or error, and the booking readback |

**Stop the session from the browser.** Run the canceled-flight prompt in the
Workflow phase; it pauses after `search`. Choose **Stop runtime session** on the
continuity rail, then select **Resume and request hold** on the Recovery desk.
The backend stays up. The workflow continues at `availability` on the same
`thread_id` in a new microVM; `search` does not run again.

**Scripts against real Aurora and Gateway calls.** Both need the deployed
AgentCore resources and create, then remove, their own journey, snapshot and
hold records. They do not reset the catalog or touch other bookings.

```bash
python scripts/kill_and_resume_proof.py
python scripts/lost_response_proof.py
```

Add `--worker-login` to either command to run the SIGKILLed or loss-injected worker
as the `meridian_workflow` login. Only the worker subprocess gets
`AURORA_WORKFLOW_SECRET_ARN` as its `AURORA_SECRET_ARN`; the driver keeps the master
client for verification and cleanup. The worker prints its `current_user`, and the
driver fails unless it is `meridian_workflow`.

- `kill_and_resume_proof.py` places a hold through the gateway, kills its worker
  with SIGKILL after the hold is saved, shows a second worker refused
  until the lease expires, then resumes and verifies one hold with the same
  booking ID and original expiry. `DEMO_LEASE_SECONDS` (default 20) sets the
  lease; a cold worker needs most of that before its first heartbeat.
- `lost_response_proof.py` receives a real committed hold from the gateway,
  discards the response and raises a timeout, so the `hold` node stays pending.
  A replacement worker retries the saved intent and gets the same booking and
  expiry. It also checks that Cedar denies unconfirmed and over-budget calls
  with the same request identity. The write and the retry are real; only the
  lost response is simulated.

On System evidence, a successful recovery shows the replacement execution ID,
`resumed_from_checkpoint`, the worker IDs, the hold creator, and the booking ID
and expiry. A second execution alone does not prove that the resume succeeded.

`stop_and_resume_proof.py` drives the running backend instead: it stops the
journey's Runtime session through the stop endpoint, once while the run waits
for review (`--during waiting`) and once mid-run (`--during running`), resumes
on a new microVM and removes its own rows unless you pass `--keep`.

A confirmed booking uses catalog capacity. To release the sample traveler's
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

The publisher checks that `AGENTCORE_WORKFLOW_RUNTIME_ARN` is set and that the
MeridianWorkflow Runtime is READY, and it refuses otherwise, even on a dry run.
The operator running it needs `bedrock-agentcore:GetAgentRuntime` for that check.
It does not create or delete App Runner services, rotate
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

The App Runner instance role is a workload and needs its own grant to the sample
traveler. Run this once after the `MeridianWebRoles` stack exists; without it,
Phase 4 and Phase 5 requests on the hosted site fail with
`aws_iam subject is not authorized for traveler`:

```bash
python scripts/bind_web_backend_role.py
```

A Git push runs CI only; it does not deploy the hosted app or the AgentCore
resources.

`scripts/validate_demo.py` runs the full sample contract (catalog, phases, holds,
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
| `/api/health` reports `workflow_runtime_configured: false`, or Phase 5 reports AgentCore is not configured | The backend has no `AGENTCORE_WORKFLOW_RUNTIME_ARN`. Deploy `MeridianWorkflow`, run `python scripts/sync_agentcore_env.py --write` and restart the backend. See [Run the workflow Runtime](#run-the-workflow-runtime). |
| `stop-session` returns 409 | The journey has no paused or running workflow session, or its session was already stopped. Read the journey before trying again. |

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
