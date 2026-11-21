# Meridian operations

How to provision Aurora, run the workflow Runtime, exercise its
recovery behavior, set up the service logins and sign-in, publish the web app,
and troubleshoot a deployment.
Commands run from `meridian/` with the virtual environment active unless a
step says otherwise. The AgentCore deployment has its own
[runbook](AGENTCORE_DEPLOY_RUNBOOK.md).

## Provision Aurora

Meridian needs an Aurora PostgreSQL cluster with the RDS Data API enabled and a
Secrets Manager secret for its database user. If you already have one, set
`AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN` and `AURORA_DATABASE` in
`meridian/.env` and skip to [Prepare the database](#prepare-the-database).

`infra/bin/meridian-aurora.ts` is a CDK app that creates an encrypted Aurora
PostgreSQL 18 cluster: one private Serverless v2 writer and one failover reader whose
Availability Zone you name (1 to 16 ACUs), the Data API, TLS required, no inbound
database port, seven-day backups, deletion protection, and an RDS-managed master
secret. It needs a VPC with two subnets in different Availability Zones. The stack pins only
the reader's zone. The writer has no zone property, so RDS places it, and the two zones are
different only when you have read them back and seen that they are (see
[First deployment: place the reader](#first-deployment-place-the-reader)). The reader's zone
must meet three conditions: it is one of the cluster's own Availability Zones, fixed when
the cluster is created (`aws rds describe-db-clusters` lists them as `AvailabilityZones`); RDS
offers `db.serverless` there for the engine version (`aws rds describe-orderable-db-instance-options
--engine aurora-postgresql --engine-version 18.6 --db-instance-class db.serverless`; in US East
(N. Virginia) on 14 Nov 2026 it did not list `us-east-1e`); and `subnetIds` includes a subnet in
it. A zone that fails any of these makes CloudFormation roll the update back.
The stack never gives the writer a zone, because adding one to an existing writer makes
CloudFormation replace it. The reader is in promotion tier 1, so it scales
with the writer and a failover lands on warm capacity. The Data API reaches only the
writer, so the reader carries no application reads. The 1 ACU minimum lifts the 2,000
`max_connections` cap that a 0 or 0.5 ACU minimum imposes; `max_connections` is static,
so reboot the instances after changing the maximum.

First run the read-only preflight. It checks your identity, subnet capacity,
the engine version, RDS quotas, the RDS service-linked role, CDK bootstrap and
IAM permissions, and makes no changes:

```bash
python scripts/provision_preflight.py --account <account-id> --region <region> \
  --vpc-id <vpc-id> --subnet-ids <subnet-in-az-a> <subnet-in-az-b> \
  --engine-version 18.6 --output .local/provision-preflight.json
```

Two checks fail on a new account until you act, and the preflight prints the fix beside each one
(on stderr, and as `recovery` in the JSON report). `cdk_bootstrap` needs the CDK environment
bootstrapped once per account and Region: from `infra/`, run
`npm ci && npx cdk bootstrap aws://<account-id>/<region>`, which the
[AgentCore runbook](AGENTCORE_DEPLOY_RUNBOOK.md#3-bootstrap-cdk) repeats for its own CDK app.
`rds_service_linked_role` needs the RDS service-linked role, which a first cluster creates for
you only when the deploying identity may create it; create it up front with
`aws iam create-service-linked-role --aws-service-name rds.amazonaws.com`. Run the preflight
again after each fix.

The stack creates a writer and a reader, so a first creation needs room for two DB instances
and the preflight fails with one. For a stack that is already deployed, add
`--cluster-identifier <cluster-name>`: it then checks only the DB instances that cluster still
lacks (none when it has both, one when the reader is missing). It does not count the one extra
DB instance slot that replacing the reader needs (see
[First deployment: place the reader](#first-deployment-place-the-reader)), so confirm that slot
yourself before a replacement.

Then review and deploy the stack:

```bash
cd infra
npm ci && npm run build
CONTEXT=(-c account=<account-id> -c region=<region> -c clusterIdentifier=<new-cluster-name>
  -c vpcId=<vpc-id> -c subnetIds=<subnet-in-az-a>,<subnet-in-az-b> -c engineVersion=18.6
  -c readerAvailabilityZone=<one-of-your-subnet-zones>)
npx cdk diff   --app 'node dist/bin/meridian-aurora.js' "${CONTEXT[@]}"
npx cdk deploy --app 'node dist/bin/meridian-aurora.js' "${CONTEXT[@]}"
```

The stack outputs `ClusterArn` and `MasterSecretArn`; copy them into
`AURORA_CLUSTER_ARN` and `AURORA_SECRET_ARN`, and set `AURORA_DATABASE=meridian`.
Passing `-c snapshotArn=<snapshot-arn>` restores from a snapshot instead; the
restored cluster keeps the snapshot's database users, so keep using the
existing secret. Pass the same `-c snapshotArn` on every later `cdk diff` and
`cdk deploy` of a stack created that way: without it the template drops
`SnapshotIdentifier`, and the diff replaces the cluster and its writer.

### First deployment: place the reader

On a first deployment the writer does not exist yet, so its zone cannot be read before the
deploy. Use two stages and trust only what you read back.

1. Name either of your two subnet zones as `readerAvailabilityZone` and deploy as above. The
   cluster's zones come from the subnet group, so a zone that has one of your two subnets is
   the candidate; step 2 confirms it. If CloudFormation rolls the first deploy back on the
   reader's zone, CloudFormation cannot update the stack, so delete the failed stack and deploy
   again. Read its status first:

   ```bash
   aws cloudformation describe-stacks --region <region> --stack-name MeridianAurora \
     --query 'Stacks[0].StackStatus' --output text
   ```

   The cluster is created with deletion protection and the stack with termination protection,
   so the rollback may not have been able to remove the cluster.

   - `ROLLBACK_COMPLETE`: the rollback removed everything. Turn off termination protection,
     delete the stack and wait until it is gone:

     ```bash
     aws cloudformation update-termination-protection --region <region> \
       --stack-name MeridianAurora --no-enable-termination-protection
     aws cloudformation delete-stack --region <region> --stack-name MeridianAurora
     aws cloudformation wait stack-delete-complete --region <region> --stack-name MeridianAurora
     ```

   - `ROLLBACK_FAILED`: the cluster and its writer were created before the reader failed, and
     deletion protection kept the rollback from removing the cluster. Turn off the leftover
     cluster's deletion protection, then turn off termination protection, delete the stack and
     wait until it is gone:

     ```bash
     aws rds modify-db-cluster --region <region> --db-cluster-identifier <cluster-name> \
       --no-deletion-protection --apply-immediately
     aws cloudformation update-termination-protection --region <region> \
       --stack-name MeridianAurora --no-enable-termination-protection
     aws cloudformation delete-stack --region <region> --stack-name MeridianAurora
     aws cloudformation wait stack-delete-complete --region <region> --stack-name MeridianAurora
     ```

   The cluster identifier is free again once the cluster is gone. Then name the other subnet
   zone and deploy again. This failure path has not been run against AWS; read each status
   and each command's result as you go.
2. Read the cluster's zones, which instance is the writer, and each instance's zone:

   ```bash
   aws rds describe-db-clusters --region <region> --db-cluster-identifier <cluster-name> \
     --query 'DBClusters[0].{zones:AvailabilityZones,members:DBClusterMembers[].[DBInstanceIdentifier,IsClusterWriter]}'
   aws rds describe-db-instances --region <region> \
     --filters Name=db-cluster-id,Values=<cluster-name> \
     --query 'DBInstances[].[DBInstanceIdentifier,AvailabilityZone,DBInstanceStatus]' --output text
   ```

3. If the writer and the reader are in different zones, you can say the reader is in another
   zone than the writer. If they are in the same zone, run the deploy again with
   `-c readerAvailabilityZone=<the-other-subnet-zone>` and the same `CONTEXT` otherwise.
   Read `cdk diff` first: it must show the `Reader` replaced and no change to `Writer`. Stop if
   it names the writer. CloudFormation creates the replacement reader before it deletes the old
   one, so the account needs one free DB instance slot during the update. Repeat step 2 until
   the two zones differ.

A failover swaps the two roles and moves neither instance, so the zones stay different. The
existing-cluster procedure is unchanged: read the writer's zone with the commands above and
name the other one.

To delete the cluster, turn off the stack's termination protection and the
cluster's deletion protection, then destroy the stack from `infra/` with the
same `CONTEXT`:

```bash
aws cloudformation update-termination-protection --region <region> \
  --stack-name MeridianAurora --no-enable-termination-protection
aws rds modify-db-cluster --region <region> --db-cluster-identifier <cluster-name> \
  --no-deletion-protection --apply-immediately
npx cdk destroy --exclusively --app 'node dist/bin/meridian-aurora.js' "${CONTEXT[@]}"
```

The stack takes a final snapshot of the cluster, then deletes the cluster and
its writer instance. It keeps the subnet group, security group and parameter
group, which cost nothing; delete them if you want nothing left. Delete the
final snapshot (`aws rds delete-db-cluster-snapshot`) once you no longer need
it, because snapshot storage is billed. Check Secrets Manager afterwards for
the `rds!cluster-` master secret and delete it if it remains.

## Prepare the database

For a new, empty database, initialize the base schema once:

```bash
python scripts/init_aurora_schema.py
python scripts/apply_migrations.py --pending
```

For each pending file, in order, dry-run that named file, inspect the result, then apply it:

```bash
python scripts/apply_migrations.py --dry-run --only <first-pending-migration.sql>
python scripts/apply_migrations.py --only <first-pending-migration.sql>
python scripts/apply_migrations.py --pending
```

The listing leaves out migration 034 (login rotation clones), which is opt-in: add
`--with-rotation` to `--pending`, `--dry-run` and `--only` only when you adopt
[automatic password rotation](#rotate-workload-passwords-automatically). Without it the
migrations that follow 034 still apply in order, and a database that already has 034 applied
lists it as done either way.

Run with no flag, the migration script only lists the pending files and prints its usage (exit 2); it
writes nothing, and nothing applies without `--only`. `--only` refuses any file other than the first pending
migration. Dry runs acquire database locks and execute DDL before rolling back; they require
the same operational review as other AWS access. Apply prerequisites before dry-running the
next migration, because a rolled-back prerequisite does not exist for the next check.

After all migrations on a new installation, run `python scripts/seed_data.py` once. It calls
Bedrock for embeddings. The schema and full seed commands refuse existing Meridian tables or
data. For an existing installation, preserve its records and upgrade with named migrations.
An older installation may also need `bind_current_identity.py` to establish its workload grant.

Migration `021_audit_outcome.sql` adds `tool_calls` and `turn_error` to `agent_audit_log`.
Those final-turn annotations are distinct from the atomic business-action audit added in 028.

**Migrations 026 to 029:** 026-028 precede the holds Lambda that uses them; 029 follows a
verified application cutover. Follow [the booking functions upgrade](BOOKING_FUNCTIONS_UPGRADE.md)
before applying them. The action audit commits with each business action; final turn audit
persistence remains best effort if a process is killed.

**Migration 030:** updates pgvector to 0.8.2, the version Aurora PostgreSQL 18.6 provides. An
engine upgrade does not update installed extensions. Upgrade an existing cluster to 18.6 through
the Aurora stack first (`-c engineVersion=18.6`, after a `cdk diff` that changes only
`EngineVersion`), then apply 030. The migration refuses to run when 0.8.2 is not available.

**Migration 031:** sets `statement_timeout` 30s, `lock_timeout` 10s and
`idle_in_transaction_session_timeout` 120s on each workload login. The statement limit is below the
Data API's 45 second call timeout, and the idle limit is below its three minute idle rollback, so
Aurora ends a runaway statement or an abandoned transaction itself. New Data API connections pick
the limits up, and a pooled connection opened earlier keeps the old values until the Data API
recycles it; the master login keeps the defaults. The lock limit also bounds the advisory locks a
hold or confirmation takes: under heavy contention on one package, a write that waits more than
10 seconds fails cleanly with the Lambda's fixed code instead of waiting toward the 45 second call
timeout.

**Migration 032:** moves `create_courtesy_hold`, `confirm_booking` and `release_courtesy_hold` from
the master login to `meridian_booking_owner`, a role that cannot log in or bypass RLS. It reads every
booking for the seat count through explicit `FOR SELECT` policies, and each insert or update it makes
must still pass the traveler-scope policies, so Aurora refuses a booking row outside the
transaction's scope even if a function had a bug. It may update only `status` and `confirmed_at`.
EXECUTE grants are unchanged. The master inherits the owner role, so a later migration run as the
master can still grant, revoke or alter these functions; a later `CREATE OR REPLACE` must also
grant `CREATE ON SCHEMA public` to `meridian_booking_owner` around the statement and revoke it
after, as 032 does.

**Migration 033:** creates the read-only `meridian_audit_export` login for the audit export, with the
same session limits as 031 and TimeZone UTC, and adds one export-order index to each audit table. It
changes no existing policy or grant. Apply it only before provisioning that login; see
[Audit export](#audit-export).

**Migration 034:** creates `<login>_clone` for each of the six workload logins and the
`meridian_rotator` role that sets their passwords, for
[automatic rotation](#rotate-workload-passwords-automatically). The clones are members of their login
and hold no grant of their own; no password is set and no running workload changes. Apply it only
when you intend to use rotation: the default `apply_migrations.py` listing skips it, and
`--with-rotation` includes it.

Migrations 024 and 025 rename the `journeys` store column to `journeys.snapshot_store` in two
steps. 024 adds `snapshot_store` beside the old column with a trigger that keeps them equal; 025
drops the old column and the trigger. A new database applies both in sequence before deployment. An existing
deployment from before commit `95eff0c5` applies 024, deploys `95eff0c5` (the catalog Lambda
first, then the AgentCore stack, then the web app), verifies, and only then applies 025 and
deploys this commit: 025 breaks any writer still running code from before `95eff0c5`. The same
two commits rename the `/api/health` fields to `snapshot_store`, `snapshot_durable` and
`snapshot_required`, and the `currency_convert` arguments to `from_currency` and `to_currency`
(see the [runbook](AGENTCORE_DEPLOY_RUNBOOK.md#what-gets-deployed)).

Each workload that sets a traveler scope needs its own grant: the backend's
identity (`seed_data.py` or `bind_current_identity.py`), the holds Lambda role
(`bind_gateway_workload.py`) and, for the hosted app, the App Runner instance
role (`bind_web_backend_role.py --apply`). The backend role and the Workflow
Runtime role are also bound to the second traveler, Jordan Lee
(`--traveler trv_demo_decoy`); the holds role and your laptop identity are not.

## Run the workflow Runtime

Phase 5 runs as a Strands Graph in the `MeridianWorkflow` AgentCore Runtime. The
backend invokes it and relays the result; it saves one snapshot per node to the
Aurora table `workflow_snapshots`. The quick start in the
[repository README](../../README.md#quick-start) starts the backend. Before Phase 5
works you need three things:

1. Migrations 014 to 017 applied individually with `apply_migrations.py --only`. They
   create `workflow_snapshots`, the `meridian_workflow` role and
   `workflow_session_stops`.
2. The Runtime's own database login. `meridian_workflow` is NOBYPASSRLS, owns
   nothing and reaches `meridian_app` only through `SET ROLE`. Create its
   password, its Secrets Manager secret and its access policy:

   ```bash
   python scripts/provision_workflow_login.py            # report only
   python scripts/provision_workflow_login.py --apply --i-understand-this-changes-aws --write-env
   ```

   `--apply` without `--i-understand-this-changes-aws` exits 3 and changes nothing.

   `--write-env` sets `AURORA_WORKFLOW_SECRET_ARN` in `.env`.
3. The deployed Runtime. Follow the [runbook](AGENTCORE_DEPLOY_RUNBOOK.md) to
   deploy `MeridianWorkflow`, then run `python scripts/sync_agentcore_env.py --write`
   so `.env` has `AGENTCORE_WORKFLOW_RUNTIME_ARN`, and
   `python scripts/bind_workflow_runtime.py --apply` to grant the Runtime's role its
   traveler binding. Add `--traveler trv_demo_decoy --apply` for Jordan Lee.

Confirm with `curl http://127.0.0.1:8013/api/health`:

```json
{
  "snapshot_store": "Aurora workflow_snapshots",
  "snapshot_durable": true,
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

Re-run `python scripts/provision_workflow_login.py --apply --i-understand-this-changes-aws` with `AWS_PROFILE`
set. It generates a new password, sends Postgres only its SCRAM-SHA-256
verifier, stores the new credential in the same secret and checks that the login
is `meridian_workflow` without BYPASSRLS. Between the `ALTER ROLE` and the secret
update, anything holding the old secret fails, so rotate while no workflow runs.
The same command repairs a half-finished run. To rotate without that gap, use
[automatic rotation with alternating users](#rotate-workload-passwords-automatically), which is
built but not enabled on any live login.

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
| Runtime session stopped by an operator | The next resume starts a new microVM on the same session and restores the newest snapshot | A `workflow_session_stops` row, a new execution attempt and the same hold |
| Policy refusal or target failure | No hold is reported | The actual boundary or error, and the booking readback |
| A step fails after the hold | The runner releases the hold through the Gateway's `release_hold` tool, for the hold this run placed only, with the run's execution ID | The booking reads `released` and its places count again. The Runtime's error event carries a `hold_release` phrase when the hold was left in place; read it in the Runtime output or log, because the backend log and the traveler see only the fixed reference message. `lost_response_proof.py` ends by doing this |

**Stop the session from the browser.** Open the Recovery desk and click **Start recovery**
once; the workflow pauses after `search`. Choose **Stop runtime session** on the
continuity rail, then select **Resume and request hold** on the Recovery desk.
The backend stays up. The workflow continues at `availability` on the same
`thread_id` in a new microVM; `search` does not run again.

**Scripts against real Aurora and Gateway calls.** Both need the deployed
AgentCore resources and create their own journey, snapshot and hold records.
They give their holds back through the Gateway and keep their rows. They do not
reset the catalog or touch other bookings.

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
`resumed_from_snapshot`, the worker IDs, the hold creator, and the booking ID
and expiry. A second execution alone does not prove that the resume succeeded.

`stop_and_resume_proof.py` drives the running backend instead: it stops the
journey's Runtime session through the stop endpoint, once while the run waits
for review (`--during waiting`) and once mid-run (`--during running`), resumes
on a new microVM. At the end it gives the run's hold back through the governed Gateway release
tool and keeps every row: the journey, hold request, booking and `booking_action_audit` rows stay,
so an audit export taken during the run still matches Aurora. `--keep` leaves the hold held.

The other exercises follow the same rule. `kill_and_resume_proof.py`, `lost_response_proof.py` and
`validate_demo.py` release their active holds through the Gateway and keep their rows. The
confirmation step of `validate_demo.py` leaves one confirmed booking; the cleanup keeps it and
prints its id, because a confirmed booking is not a hold and still uses package capacity. Each of
the three has `--delete-from-disposable-database`, off by default, which deletes the run's rows
instead. That deletion removes `booking_action_audit` rows, so a comparison with an earlier audit
export reports `row-deleted`. Use it only on a database you are going to discard.

A confirmed booking uses catalog capacity. To give back the sample traveler's active holds, list
the bookings first:

```bash
python scripts/release_demo_bookings.py
python scripts/release_demo_bookings.py --booking-id <booking-id> --apply \
  --i-understand-this-changes-aws
```

Without `--apply` the script only lists (`--dry-run` says so explicitly). With `--apply` it
releases each held booking through the Gateway as the traveler and deletes nothing; confirmed
bookings are listed and kept. `--apply` without `--i-understand-this-changes-aws` exits 3 and
changes nothing. To free the capacity of a confirmed booking on a disposable database, add
`--delete-from-disposable-database`. That is an administrative deletion, not a release: it
removes the booking, its lines, its hold request and its action audit rows.

## Governed tools and the workflow audit row

The workflow searches the catalog, places holds and gives holds back through the Gateway, as the signed-in caller:

| Step | Gateway tool | Decided by |
| --- | --- | --- |
| `search` | `MeridianCatalog___hybrid_trip_search` | Cedar `meridian_read_tools`, then the catalog Lambda |
| `hold` | `MeridianHolds___create_courtesy_hold` | Cedar `meridian_hold_governance`, then the holds Lambda |
| compensation | `MeridianHolds___release_hold` | Cedar `meridian_release_governance`, then the holds Lambda, which checks the hold belongs to the pinned traveler and the run's lease is live |

The availability, memory recall and read-back reads are scoped Aurora reads under the workflow
login, not Gateway calls. A failed Gateway call fails the run with a code or a fixed phrase,
never the Gateway's text. Each run that starts attempts one final `agent_audit_log` row
(`operation = 'workflow_run'`) with `tool_calls` and `turn_error`. A hard kill or audit-write
failure can omit that row; a malformed request and a busy thread write none.
`tests/test_workflow_audit_aurora.py` shows the row the workflow login writes. Separately, the
holds Lambda commits `booking_action_audit` in the same transaction as a successful action or
target replay. These application records carry service correlation IDs, not native Cedar decisions.

### Add a Gateway tool and its policy in `jwt` mode

A policy is validated against the Gateway's tool schema, so a tool and its policy ship in two deploys through `release_identity.py deploy`. The `complete` stage accepts a plan that updates a Runtime, adds the `MeridianCatalog` target or updates a Cedar rule, so a plan that changes only the holds target and Lambda is refused: the tool deploy carries a changed Runtime bundle.

1. If the interceptor's pin list changed: `python scripts/release_identity.py interceptor` (dry run), then with `--apply --i-understand-this-changes-aws`.
2. Deploy the tool: `python scripts/render_agentcore_config.py`, then `python scripts/release_identity.py deploy` (dry run: a changed Lambda, target and Runtime, no policy), then with `--apply --i-understand-this-changes-aws`. `python scripts/smoke_gateway_tools.py --probe-release` fails here by design: the tool is denied by default.
3. Deploy the policy the same way. The plan adds the policy and updates the binding rule. The probe now exits 0.

## Service logins

Five Aurora logins sit next to `meridian_workflow`. None owns anything or can
bypass row-level security, and each holds only what its workload queries.

| Login | Workload | Holds |
| --- | --- | --- |
| `meridian_backend` | The App Runner backend | SELECT on `traveler_identity_bindings` and `trip_packages`, INSERT on `traveler_access_audit`, EXECUTE on `backend_admin_count`, and SET ROLE to `meridian_app` |
| `meridian_gateway` | The `MeridianHolds` Lambda | Identity and package reads, access audit INSERT, SET ROLE to `meridian_app`, EXECUTE on the three bounded booking functions, and SELECT/INSERT on `booking_action_audit`; no direct booking DML after migration 029 |
| `meridian_identity` | The Amazon Cognito pre-token-generation Lambda | SELECT on `traveler_identity_bindings` |
| `meridian_catalog` | The `meridian-catalog-tools` Lambda behind the `MeridianCatalog` target | SELECT on `trip_packages` and EXECUTE on `package_places_left`, and nothing else: no identity bindings, no audit, no SET ROLE |
| `meridian_audit_export` | The audit export Lambda ([Audit export](#audit-export)) | SELECT on `agent_audit_log`, `traveler_access_audit` and `booking_action_audit` through one SELECT policy each, and nothing else: no write, no SET ROLE |

The backend counts rows across every traveler in only two places, the RLS probe
and the session receipt. The master login used to do that without RLS applying
to it. The backend login calls `backend_admin_count` instead, a definer function
that answers eight named counts (`rls_traveler_preferences`,
`rls_trip_interactions`, `rls_conversations`, `rls_conversation_messages`,
`audit_allow`, `audit_deny`, `agent_audit` and `workflow_snapshots`) and returns
nothing else. Five of the counted tables force row-level security, so migration
018 also creates one SELECT policy on each for the role that applies the
migration (`traveler_preferences_admin_count_select`,
`trip_interactions_admin_count_select`, `conversations_admin_count_select`,
`conversation_messages_admin_count_select` and `agent_audit_admin_count_select`).
No service login can use those policies.

### What remains after the release

In a `jwt` release the hosted backend connects as `meridian_backend`, the
`MeridianHolds` Lambda connects as `meridian_gateway`, the
`meridian-catalog-tools` Lambda as `meridian_catalog`, the workflow Runtime
connects as `meridian_workflow`, and the Amazon Cognito trigger connects as
`meridian_identity`. The hosted `jwt` release is published with
`scripts/publish.py --tighten`, which drops the master and shared-token secret
grants from the App Runner instance role; a publish without it plans to add both
grants back, so read the roles stack's diff before applying. Two removals are
not done:

- The shared API token secret. `scripts/publish.py` stops passing the token to a
  `jwt` service and `--tighten` removes its grant, but the secret still exists.
- The Basic edge check. The `jwt` viewer function holds no credential and reads
  no KeyValueStore, but the `iam` viewer function and the stored credential are
  still in the repository and the store.

Rolling back from `jwt` to `iam` has not been tested.

The AgentCore template grants the holds Lambda only the `meridian_gateway` secret.
A plain render preserves that restriction; `--tighten` is retained for older local
templates. A deployment still using the master login must complete the gateway
login migration before deploying this template.

Set `MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS` once in the release environment, or
leave it unset for the whole-trip default of 400000 cents. Rendering carries it
to both Runtimes, and CDK gives the holds Lambda the same value. Publish compares
the intended backend setting with the actual Runtime and Lambda settings and
refuses a mismatch. After changing it, render and deploy AgentCore before
publishing the backend. Removing the setting also removes any old hosted override.

### Create the logins

1. List what is pending. The list must be exactly `018_service_logins.sql`.
   Apply only that migration, and never run `apply_migrations.py` without
   `--pending` first:

   ```bash
   python scripts/apply_migrations.py --pending
   ```

   The migration creates the three roles with NOLOGIN and stops with an error if
   a role of that name already exists with other attributes.

2. Report the plan, then provision. This enables LOGIN with a SCRAM-SHA-256
   verifier, stores each password only in its own AWS Secrets Manager secret
   (`meridian/aurora/backend-login`, `meridian/aurora/gateway-login` and
   `meridian/aurora/identity-login`), creates each managed policy
   (`MeridianBackendAuroraAccess`, `MeridianGatewayAuroraAccess` and
   `MeridianIdentityAuroraAccess`), checks that the login connects without
   BYPASSRLS, and writes `AURORA_BACKEND_SECRET_ARN`, `AURORA_GATEWAY_SECRET_ARN`
   and `AURORA_IDENTITY_SECRET_ARN` to `.env`:

   ```bash
   for login in backend gateway identity; do
     python scripts/provision_service_logins.py --login $login
     python scripts/provision_service_logins.py --login $login --apply \
       --i-understand-this-changes-aws --write-env
   done
   ```

   With only migration 018 applied, name the three 018 logins one at a time, as above.
   `--login all` also provisions `catalog` (role `meridian_catalog`, created by migration 022)
   and `audit` (role `meridian_audit_export`, created by migration 033), so use it only once
   both are applied; before that it rotates the earlier passwords and then fails at the first
   `ALTER ROLE` for a role that does not exist. `--login` is required, and `--apply` also needs
   `--i-understand-this-changes-aws`: without it the script exits 3 and changes nothing, and so does an
   abbreviated flag. Running `--apply` again rotates the password. Between the `ALTER ROLE` and
   the secret update, anything holding the old secret fails.
   [Automatic rotation](#rotate-workload-passwords-automatically) avoids that gap once it is
   enabled for a login.

3. Prove the grants against the live cluster, including what each login must
   not do:

   ```bash
   python -m pytest tests/test_service_logins_aurora.py tests/test_backend_login_aurora.py \
     tests/test_gateway_login_aurora.py tests/test_identity_login_aurora.py -m database
   ```

   The deny tests add `traveler_access_audit` deny rows for the decoy on every
   run. That is deliberate evidence, not residue. The backend also writes a
   `traveler_access_audit` deny row whenever it answers 403 to a request for
   another traveler (`authorize_traveler` in `backend/http_auth.py`), so a deny
   row does not always come from these tests.

As `meridian_backend`, the Phase 1 to 3 search agents and the Phase 2 MCP server
can read only `trip_packages` outside a traveler scope. A Phase 2 prompt that
asks about any other table is refused by Amazon Aurora. The Phase 2 run under the
backend login has not been exercised against the live cluster.

4. Prove the running backend, not just the secret, before the release:

   ```bash
   python scripts/prove_backend_login.py
   python scripts/prove_backend_login.py --apply --i-understand-this-changes-aws
   ```

   The first command prints the plan. The second starts the real backend on
   127.0.0.1:8014 with the `meridian_backend` secret, refuses to start if that
   port is already bound, and checks that `/api/health` reports
   `database_user` as `meridian_backend`. It then runs the warm-up, two Phase 2
   turns that must show successful MCP tool activity with rows (the typed
   `search_trips` tool and `compare_packages`, both on the concierge server), the RLS probe, the
   session receipt and the Phase 5 stop-and-resume recovery, and writes
   `.local/release-b2/backend-login-proof.json`. An invalid receipt is written
   before anything starts, and a failed, crashed or interrupted (Ctrl-C or
   SIGTERM) run overwrites it with `ok: false`, so an older passing receipt
   never survives. A timed-out step gets SIGINT, then SIGTERM, each with a grace
   period, before SIGKILL, so its own clean-up runs. The recovery runs on a
   `phase5-proof-` thread the run names. After it, passed or failed, that
   thread's holds are given back through the governed Gateway release tool
   (the recovery's own teardown, then a `--release-only` step), and nothing is
   deleted. A hold is read back before each release call, and a second call goes
   out only after a read-back shows it still held. The run then reads Aurora,
   pinned to the traveler and the booking agent because the booking tables force
   row level security: every hold of the thread must be inactive, with its
   booking, hold request, journey and action audit rows kept (a `hold` row, and
   a `release` row for a released booking). Anything else, or no hold after a
   passing recovery, fails the receipt's `cleanup` check, and the run prints the
   thread, journey, request and booking ids it checked. Only that thread is read
   or released; older `phase5-proof-` threads are left alone.

   The proof follows the identity mode the live Runtimes and the Gateway are in. `--mode iam|jwt`
   picks it; without the flag it reads `MERIDIAN_AGENTCORE_AUTH` from `.env` or the shell. In `iam`
   mode everything signs with the shell's AWS credentials, as above. In `jwt` mode (run it again
   after the Runtimes move, with `--mode jwt` or with the setting in `.env`) the backend runs with
   the pool settings, so it verifies a bearer token and forwards it to the Runtimes and the
   Gateway. The tool signs Jordan in through `scripts/cognito_tokens.py` and sends the token
   only in the `Authorization` header of its own requests to 127.0.0.1:8014; the token is never
   put in an argument or an environment variable. The warm-up and recovery scripts sign in
   themselves. The receipt has the same eight fields, so a jwt run that fails (for example because
   a Runtime refuses the token) records `ok: false`, and `publish.py`, `gateway --apply` and
   `check` then refuse until a run passes. A run in the wrong mode fails the same way.

   Run it only from a checkout with no uncommitted change under `meridian/`
   (it refuses with exit 3 otherwise), since the receipt names the commit.
   `release_identity.py` and `publish.py` do not re-check the working tree.

   While it runs, the backend on 127.0.0.1:8014 is open as Jordan
   (`trv_meridian_demo`) without a token; any process on the machine can call it.

   Residue the run does not delete: the warm-up turns write
   `conversation_messages`, `trip_interactions`, `conversations` and
   `traveler_preferences` rows for Jordan, and the access checks add
   append-only audit rows (`traveler_access_audit` and the agent audit log).
   The recovery step's hold is given back through the Gateway release tool; its journey,
   hold request, booking and action audit rows stay.

   | Code | Meaning |
   | --- | --- |
   | 0 | every check passed and the receipt was written |
   | 1 | a check failed, the backend never answered or exited, the run crashed, or it was interrupted |
   | 3 | refused: a missing setting, a dirty tree, the wrong account or Region, a login that is not least-privilege, a bound port, a usage error or a missing confirmation flag |

[SIGNED_SCOPE_EVALUATION.md](SIGNED_SCOPE_EVALUATION.md) records why the
database does not verify a signed traveler scope itself: the logins can set any
traveler, so the application check and the workload grant remain the controls.

### Create the catalog login

The `meridian-catalog-tools` Lambda behind the `MeridianCatalog` target runs as its own login,
`meridian_catalog`, created by migration 022 (`022_catalog_login.sql`). The Lambda reads `trip_packages` and
calls `package_places_left`, and that is all it does. `meridian_gateway`, the holds Lambda's login, can also
read the identity bindings, append to the access audit and `SET ROLE meridian_app`; the catalog needs none of
that, so it does not get that login. Migration 023 drops the legacy product search function, a leftover that
read a table the repository no longer has and that `PUBLIC` could still execute.

1. L0a: apply migrations 022 and 023 individually, in that order. List pending migrations,
   then dry-run and apply only the first one:

   ```bash
   python scripts/apply_migrations.py --pending
   python scripts/apply_migrations.py --dry-run --only 022_catalog_login.sql
   python scripts/apply_migrations.py --only 022_catalog_login.sql
   python scripts/apply_migrations.py --pending
   ```

   Repeat with `023_drop_legacy_product_search.sql`. If 022 is already applied, start with 023;
   if both are applied, skip this step. Run with no flag, the script only lists pending
   migrations and prints usage (exit 2), so name the file as above. Never edit an applied migration.
   The runner commits each file and its `schema_migrations` record together and rolls back on
   error. A failed role-attribute check is a finding to investigate, not permission to alter
   the role into shape. Current installations must also follow the 026-029 release sequence.

2. L0b: report the plan, then provision. Name the login: without `--login`, an `--apply` rotates every
   login's password, so the script refuses to run without it.

   ```bash
   python scripts/provision_service_logins.py --login catalog
   python scripts/provision_service_logins.py --login catalog --apply \
     --i-understand-this-changes-aws --write-env
   ```

   This enables LOGIN with a SCRAM-SHA-256 verifier, creates the secret `meridian/aurora/catalog-login` and
   the managed policy `MeridianCatalogAuroraAccess` (the Data API on the cluster, and
   `secretsmanager:GetSecretValue` on that secret alone), checks that the login connects without
   BYPASSRLS, and writes `AURORA_CATALOG_SECRET_ARN` to `.env`. A `new version` or `update` line in the dry run
   means a catalog login was provisioned before: stop and read what exists.

3. L0c: prove the login against the live cluster, including what it must not do. Run it from a clean checkout:

   ```bash
   python -m pytest -m database tests/test_migration_022_live.py
   ```

   As `meridian_catalog`, the catalog's own statements run; a SELECT on `traveler_identity_bindings`, an INSERT
   into `traveler_access_audit`, `SET ROLE meridian_app` and every write to `trip_packages` are refused with
   `permission denied`. Treat a failure as a defect: fix a missing grant with a migration, never a one-off `GRANT`.

Then create the Lambda and its role, as section 2 of the
[AgentCore deploy runbook](AGENTCORE_DEPLOY_RUNBOOK.md#2-create-the-catalog-lambda) says.

## Audit export

The audit tables (`agent_audit_log`, `traveler_access_audit` and `booking_action_audit`) live in
Aurora, where the master login can edit or delete a row without leaving a trace in the table. The
audit export copies new rows to an Amazon S3 bucket with Object Lock, as a hash chain, so a later
edit or deletion can be found. It is an export a customer can lift into production, not a change
to how the application writes audit rows.

### How it works

- **Login.** `meridian_audit_export` (migration `033_audit_export_login.sql`) holds SELECT on the
  three audit tables and nothing else: no sequence, no function, no write, no role. It does not
  bypass RLS. It has the same session limits as the other logins (statement timeout 30 seconds,
  lock timeout 10 seconds, idle-in-transaction timeout 120 seconds) and TimeZone UTC. The migration
  also adds an index on each table in export order, `(ran_at, audit_id)`, `(decided_at, audit_id)`
  and `(recorded_at, action_audit_id)`; the runner applies a migration in one transaction, so they
  are plain `CREATE INDEX`, which blocks writes to those small tables briefly. Apply it when no
  workflow is running. It sees every traveler's rows through one explicit policy per table,
  `FOR SELECT TO meridian_audit_export USING (true)`. The exporter never writes to Aurora.
- **Schedule.** An EventBridge Scheduler schedule runs the exporter Lambda
  (`infra/functions/audit_export`) every five minutes. The Lambda reads through the Data API.
  The tables take turns: each unfinished table gets one batch per round, up to
  `AUDIT_EXPORT_MAX_BATCHES` rounds, so a table with a large backlog cannot use up the run before
  the others are read. A run counts as completed (`RunsCompleted` 1) only when every table was
  read at least once and none failed. When the time runs out before a table's first read, the run
  logs a warning naming that table, emits `RunsCompleted` 0, and reports that table's backlog as
  3600 seconds in `BacklogAgeSeconds`, not 0.
- **Watermark.** Rows are read in `(timestamp, id)` order, strictly after the last exported row,
  using the column the database sets on insert (`ran_at`, `decided_at`, `recorded_at`) and the
  primary key as the tie-breaker. None of the tables has a sequence column, and a sequence would not
  help: it is assigned at insert, not commit. A transaction stamps its rows with its start time, so a
  row can commit after a row with a later stamp. The exporter therefore reads only rows older than
  `settleSeconds` (default 180), which must exceed the longest transaction that writes audit rows. The
  application's scoped sessions write an audit row and commit within one request. A row that still commits behind
  the watermark is never exported; the verifier reports it as `row-unexported`.
- **Time zone.** `agent_audit_log.ran_at` is a timestamp without time zone, holding the writing
  session's local time. The settle comparison uses `CURRENT_TIMESTAMP AT TIME ZONE 'UTC'`, so it does
  not depend on the exporter's session, but it assumes every writer stores UTC. A writer in another
  zone would shift that table's settle time by its offset. `tests/test_migration_033_live.py`
  asserts `TimeZone = UTC` for every workload login and the audit login; run it before relying on
  the export, and again after changing a login's settings.
- **State.** There is no state store. The latest object of a table names the last exported row, and
  its own bytes give the hash the next object carries, so a retry recomputes the same position. A
  write uses `If-None-Match: *`, so an overlapping run or a retry after a lost reply cannot overwrite
  an object; it finds the object on its next run. An empty batch writes nothing.
- **Objects.** One JSON Lines object per table and batch, at
  `audit-export/v1/<table>/<group>/<seq>.jsonl`. The first line is a header with the sequence
  number, `prev_sha256` (the SHA-256 of the previous object's bytes, zeros for the first), the row
  count, the first and last row, and the export time. Each following line is one row as Aurora
  renders it with `to_jsonb`. The same range is in the object's metadata. A batch holds at most 200
  rows, halves on a Data API response over its 1 MB limit, and fails with a fixed phrase when one row
  alone is too large.
- **Bucket.** Object Lock with a default retention (`GOVERNANCE`, 30 days; set `retentionMode`
  and `retentionDays`), versioning, SSE-KMS with a customer-managed key that rotates, Block Public
  Access, a TLS-only policy, and a policy that denies `s3:DeleteObject`, `s3:DeleteObjectVersion`
  and (in `GOVERNANCE`) `s3:BypassGovernanceRetention` to every principal, and a policy that denies
  `s3:PutObject` to every principal unless the request carries `If-None-Match` (the documented
  `Null` condition on `s3:if-none-match`), so no key can be overwritten. The key is customer
  managed so that reading an export needs `kms:Decrypt` as well as S3 access, and every use is in
  CloudTrail; the bucket and key are retained when the stack is deleted.
- **Exporter permissions.** The Data API and the login secret through the managed policy
  `MeridianAuditExportAuroraAccess`; `s3:PutObject` and `s3:GetObject` on the export prefix,
  `s3:ListBucket` for that prefix, and `kms:GenerateDataKey` and `kms:Decrypt` on the key. No delete,
  retention or legal-hold permission.
- **Alarms.** Lambda errors; no completed run for three schedule periods (`RunsCompleted`
  from the Lambda's embedded metric line); and export lag (`BacklogAgeSeconds` over 1800 seconds for
  two periods, which a full batch of old rows causes). Set `AUDIT_EXPORT_ALARM_TOPIC_ARN` in
  `meridian/.env` to notify an SNS topic on every deploy (`-c alarmTopicArn=<topic>` overrides it).
  Without either the alarms only change state, and a deploy without it removes existing alarm
  actions.
- **Chain heads.** Each run that writes an object logs the table, sequence and SHA-256 of its head,
  and each run logs the boto3 version it ran with. The log group is retained when the stack is deleted
  and keeps 365 days (`-c logRetentionDays=<days>`).
  Copy a head somewhere the bucket's administrators cannot write, and pass it to the verifier.

### Choose the Object Lock mode

`GOVERNANCE` is the default because this repository deploys into workshop and event accounts, where
an irreversible lock is a cost with no benefit. A principal with `s3:BypassGovernanceRetention`
can delete a governed version, and the bucket policy denies that permission to everyone, so doing so
takes a bucket policy change first (`s3:PutBucketPolicy`, recorded by CloudTrail). `COMPLIANCE` lets
nobody delete or shorten retention before the date, root included, and cannot be undone: deploy it
with `-c retentionMode=COMPLIANCE -c retentionDays=<days>` only when your retention policy says so.
The sample sets no lifecycle expiry; expiry and cost follow your retention policy.

### What this proves, and what it does not

- It detects an edit or a deletion of an exported row, an edit, a removal or a delete marker on an
  object, and a gap in the chain, when the verifier runs.
- A row edited or deleted between its insert and its export (about `settleSeconds` plus the
  schedule period, normally under ten minutes) is exported as it then stood. The export does not
  detect it.
- A master-user action before export is not recorded. Nothing here records the actions, only the
  rows.
- A row that commits behind the watermark is not exported, and the verifier reports it.
- Removing the newest objects (a truncated tail) leaves a valid chain. A delete marker, or a head you
  recorded elsewhere, reveals it. Governance retention can be bypassed by whoever may first change
  the bucket policy.
- A principal who can write both Aurora and the bucket (for example one account's administrator)
  can rewrite both. Keep the bucket in an account whose administrators differ from the database's,
  and use `COMPLIANCE`, for evidence you must rely on.
- The export holds the audit rows' content, including traveler ids and verified subjects. Treat
  the bucket and the key as the same sensitivity as the audit tables.

### Create the audit export

Each step that calls AWS needs the owner's approval. Run from `meridian/`.

1. L0a: apply migration 033 alone. List pending migrations, dry-run it, read the result, then apply:

   ```bash
   python scripts/apply_migrations.py --pending
   python scripts/apply_migrations.py --dry-run --only 033_audit_export_login.sql
   python scripts/apply_migrations.py --only 033_audit_export_login.sql
   ```

   A failed role check is a finding to investigate, not permission to alter the role into shape.

2. L0b: report the plan, then provision the login, its secret and its managed policy:

   ```bash
   python scripts/provision_service_logins.py --login audit
   python scripts/provision_service_logins.py --login audit --apply \
     --i-understand-this-changes-aws --write-env
   ```

   This writes `AURORA_AUDIT_EXPORT_SECRET_ARN` to `.env`. The stack needs the managed policy to
   exist before it deploys, as the identity stack does.

3. L0c: prove the login against the live cluster, including what it must not do, and that the
   exporter's SQL pages every row once in the order Aurora sorts by:

   ```bash
   python -m pytest -m database tests/test_migration_033_live.py
   ```

4. L1: deploy the stack from `infra/` with the real npm binary:

   ```bash
   cd infra
   npm run build
   node_modules/.bin/cdk diff -a "node dist/bin/meridian-audit-export.js"
   node_modules/.bin/cdk deploy -a "node dist/bin/meridian-audit-export.js" \
     -c retentionMode=GOVERNANCE -c retentionDays=30
   ```

   Read the diff first. It should create one bucket, one key, one Lambda and its role and log
   group, the schedule and its role, and three alarms. The stack is termination protected.

5. L2: wait for the first scheduled run (five minutes), then read the Lambda's log for the
   `audit_export_head` lines and the `RunsCompleted` metric. A run that exports nothing is normal when
   no row is older than the settle time.

6. L3: verify. Dry-run first, then read the bucket, then compare with Aurora:

   ```bash
   python scripts/verify_audit_export.py --bucket <bucket> --dry-run
   python scripts/verify_audit_export.py --bucket <bucket>
   python scripts/verify_audit_export.py --bucket <bucket> --compare-aurora \
     --head agent_audit_log=<seq>:<sha256>
   ```

   The verifier is read-only: it lists and reads objects and, with `--compare-aurora`, reads the
   tables as `meridian_audit_export`. Exit 0 means verified, 1 findings, 2 could not run, 3 usage
   error. A `--head` must name a known table that `--table` selects (all tables when `--table` is
   not given) and may be given once per table; otherwise the verifier exits 3 before it calls
   AWS. A recorded head that the chain does not reach, including one for an empty chain, is a
   `head-mismatch`. With `--compare-aurora`, a missing row that sorts before the watermark row in
   the database's `(timestamp, id)` order is `row-unexported`, also when it shares the
   watermark's timestamp. The comparison reads the newest row's `(timestamp, id)` once at the
   start, compares Aurora one page at a time up to that row, and keeps the exported rows in a
   temporary SQLite file, so memory does not grow with the history and rows appended during the
   scan are not read (they are not reported as missing or pending). Its identity needs `s3:ListBucketVersions`, `s3:GetObject`, `s3:GetObjectVersion` and
   `kms:Decrypt`.

| Finding | Meaning |
| --- | --- |
| `chain-broken`, `chain-gap` | An object was edited or removed, or a sequence number is missing |
| `metadata-mismatch`, `extra-version` | An object's bytes differ from what the exporter recorded, or a second version of its key exists |
| `delete-marker` | A delete was requested on an object key |
| `row-count`, `range-mismatch`, `header-mismatch`, `object-malformed`, `duplicate-row` | An object is inconsistent with its own header |
| `head-mismatch` | The latest object is not the head you recorded |
| `row-edited` | An exported row differs in Aurora now; the columns are named, not the values |
| `row-deleted` | An exported row is not in Aurora |
| `row-unexported` | A row behind the watermark was never exported |

To stop exporting, delete the schedule, or turn off termination protection and destroy the stack.
Do this before you retire Aurora. The bucket (the `BucketName` output of `MeridianAuditExport`),
its KMS key and the export Lambda's log group are retained by the stack and stay in the account:
name them in your teardown notes, because nothing else removes them and Object Lock keeps the
objects until their retention ends. To retire the login, run `ALTER ROLE meridian_audit_export NOLOGIN PASSWORD
NULL` as the master login and delete the secret and the managed policy.

## Rotate workload passwords automatically

Status: built and tested offline, **not enabled on any live login**. Nothing in this repository turns
rotation on. Until you do, the manual rotation in
[Rotate the workflow login password](#rotate-the-workflow-login-password) and
[Create the logins](#create-the-logins) is the only rotation, and it has the gap those sections
describe. Enable it as a planned change with its own rehearsal, never during a change freeze: it
adds roles and a Lambda per login, and the first live rotation is the first live test of it.

### How it works

Secrets Manager rotates with the **alternating users** strategy
([tutorial](https://docs.aws.amazon.com/secretsmanager/latest/userguide/tutorials_rotation-alternating.html),
[strategy summary](https://docs.aws.amazon.com/help-panel/secretsmanager/latest/console/rotation-separate-creds-rds-pane.html)).
Each login has two database users, `meridian_x` and `meridian_x_clone`. The login's secret names one
of them. A rotation sets a new password on the other user and then makes the secret name that user.
The user the secret named before keeps its old password, so a consumer that still holds the previous
secret value keeps working. It stops working at the rotation after next, which changes that
user's password. Every consumer must therefore read its secret again at least once per rotation
interval (30 days by default). Every consumer in this repository passes the secret's ARN to the Data
API on each call and never reads the value itself, so each call uses the current version.

One rotation runs the four steps Secrets Manager calls on the rotation Lambda
(`infra/functions/login_rotation/login_rotation.py`). Secrets Manager repeats a step after a failure
or a timeout, so each step is safe to run twice.

1. `createSecret`: puts a pending version of the secret naming the other user, with a new random
   password. A retry finds the pending version and does nothing.
2. `setSecret`: checks that the login and its clone are consistent, then runs
   `ALTER ROLE <other user> LOGIN PASSWORD '<SCRAM-SHA-256 verifier>'` through the Data API. Postgres
   receives the verifier, never the password. A retry sets the same password again.
3. `testSecret`: checks the two roles again and that the other user can log in.
4. `finishSecret`: moves `AWSCURRENT` to the new version, then connects through the Data API with the
   secret as a consumer would. If that connection fails (three attempts, two seconds apart), it moves
   `AWSCURRENT` back to the old version and fails the rotation. Moving back is safe because the old
   user's password was never changed.

The Lambda refuses to act when the secret names a user other than the login or its clone, when the
clone is missing, or when either role has SUPERUSER, BYPASSRLS, CREATEROLE, CREATEDB or REPLICATION,
a different connection limit, different role settings, or no membership of the login. A clone that
has drifted fails the rotation before any password changes.

### Constraints that shaped the design

- **No VPC path.** The cluster is private and the Lambda runs outside a VPC, so the Lambda reaches
  Postgres only through the RDS Data API. The AWS-provided PostgreSQL rotation functions connect over
  the network and cannot be used here, so this Lambda is our own code.
- **The Data API reads `AWSCURRENT` only.** It takes a secret ARN
  ([access](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.access.html)) and has
  no way to name a secret version, so `testSecret` cannot connect with the pending credential. The
  connection test runs in `finishSecret`, after the promotion, and is undone on failure as above.
  That leaves a short window, normally seconds, in which a consumer could read a version that does
  not work. The alternating strategy is what makes undoing it safe.
- **SCRAM only.** For Aurora PostgreSQL 14 and later the Data API supports only `scram-sha-256`
  ([limits](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.limitations.html)).
  The Lambda builds the SCRAM verifier itself, as `scripts/provision_service_logins.py` does, and a
  test holds the two implementations to the same output.
- **Utility statements take no parameters.** The role name is checked against the login and its
  clone, and the verifier against its exact shape, before either is put in the statement.
- **Role settings are not inherited.** The 031 timeouts are set per role, so migration 034 copies them
  to each clone, and the Lambda refuses a clone whose settings differ.

### Privileges

The Lambda connects as `meridian_rotator` (migration 034), not as the master login.

- It holds CREATEROLE and ADMIN OPTION on exactly the twelve roles it rotates (the six logins and
  their clones), with INHERIT FALSE and SET FALSE: it can change their passwords and memberships, and
  it holds none of their table privileges. On PostgreSQL 16 and later, ALTER ROLE ... PASSWORD on
  another role needs CREATEROLE plus ADMIN OPTION on that role
  ([CREATE ROLE](https://www.postgresql.org/docs/18/sql-createrole.html)), so this is the smallest
  grant that works.
- It cannot alter the master login or any role it holds no ADMIN OPTION on. It cannot give a role
  SUPERUSER, REPLICATION or BYPASSRLS, because a CREATEROLE role cannot grant an attribute it lacks.
  It does not bypass RLS and holds no table, sequence or function privilege.
- It can still create new roles, and it could grant itself membership of a role it administers, so it
  can act as any of the six logins. Changing their passwords already allows that. Treat the rotator
  secret like the logins' secrets.
- The master login would also work, but it would put the credential that bypasses RLS and owns every
  object into six Lambdas. The rotator costs one more role and one more secret.

Each Lambda can read and write its own login secret, read the rotator secret and call the Data API's
`ExecuteStatement` on this cluster. It has no other secret, no delete or policy permission, and no
IAM, S3 or KMS permission. Each login has its own Lambda, so a grant never spans two logins.

### The clone

`<login>_clone` is created by migration 034, not by the Lambda, so the Lambda needs no CREATE ROLE
right and the clone is reviewed like any other migration.

- Same attributes as the login: NOLOGIN until a rotation sets its password, NOBYPASSRLS,
  NOINHERIT, NOCREATEDB, NOCREATEROLE, no replication, no connection limit.
- It holds **no grant of its own**. It is a member of its login with INHERIT TRUE and SET TRUE, so
  every table, sequence and function privilege, every row policy written `TO <login>` (a policy
  applies to a member that inherits the role), and the path to `meridian_app` come through that
  membership. A privilege added to the login later reaches the clone at once, so the two cannot
  drift. The alternative, copying each grant and policy onto the clone, would break the first time
  a migration grants the login something new.
- It gets a copy of the login's role settings (statement, lock and idle-in-transaction timeouts, and
  TimeZone for the audit login). The migration clears the clone's settings and copies them when it
  runs. `scripts/apply_migrations.py` records an applied migration and does not run it again, so
  after changing a login's settings, apply the same `ALTER ROLE` to the clone in a new migration.
- The migration refuses an existing clone with other attributes or other memberships.

`tests/test_migration_034_contract.py` and `tests/test_login_rotation_clone_contract.py` check the
migration's statements offline. `tests/test_migration_034_live.py` (marked `database`) compares each
clone with its login in Aurora: attributes, role settings, the membership, SELECT, INSERT, UPDATE and
DELETE on every public table, EXECUTE on every public function, and the SET ROLE path to
`meridian_app`. Run it only with approval.

### Turn it on for one login first

Each step that calls AWS needs the owner's approval. Run from `meridian/`. Start with the `audit`
login: one Lambda reads through it, it is read-only, and the export has a heartbeat alarm that
reports a failure.

1. Apply migration 034 alone. Dry-run it, read the result, then apply:

   ```bash
   python scripts/apply_migrations.py --pending --with-rotation
   python scripts/apply_migrations.py --dry-run --only 034_login_rotation_clones.sql --with-rotation
   python scripts/apply_migrations.py --only 034_login_rotation_clones.sql --with-rotation
   ```

   The migration changes no password and no running workload. It needs the migrations that create
   the six logins (015, 018, 022 and 033) and PostgreSQL 16 or later.
2. Check the clones against their logins: `python -m pytest -m database
   tests/test_migration_034_live.py`.
3. Give the rotator a password, in its own secret. Without `--apply` the script only reports:

   ```bash
   python scripts/provision_rotation_login.py
   python scripts/provision_rotation_login.py --apply --i-understand-this-changes-aws --write-env
   ```

   This sets `AURORA_ROTATOR_SECRET_ARN` in `meridian/.env`. Nothing rotates this password; run the
   script again to change it.
4. Deploy the rotation stack for that one login. It is a separate CDK app, and it creates nothing
   unless `rotateLogins` names a login:

   ```bash
   cd infra
   npm run build
   npx cdk deploy -a "node dist/bin/meridian-login-rotation.js" -c rotateLogins=audit
   ```

   Add `-c rotationDays=<days>` to change the 30 day interval. The schedule does not rotate when it
   is created. `-c rotateLogins=audit,catalog` names several logins; each needs its
   `AURORA_*_SECRET_ARN` in `.env`. Without `rotateLogins` the app prints that rotation is off and
   creates no stack. No other stack in `infra/` refers to rotation, so their templates are the same
   with or without it.
5. Rotate once, on purpose, and verify before leaving it on a schedule:

   ```bash
   aws secretsmanager rotate-secret --secret-id meridian/aurora/audit-export-login
   ```

6. Only after that login has rotated twice (once to the clone, once back) and its consumer has run
   cleanly in between, turn rotation on for the next login.

### Verify

- `aws secretsmanager describe-secret --secret-id <name>` shows `RotationEnabled` true, the
  Lambda, `NextRotationDate`, and the stages `AWSCURRENT`, `AWSPREVIOUS` and no leftover
  `AWSPENDING` once the rotation has finished.
- The secret now names the other user: the login after an even number of rotations, `<login>_clone`
  after an odd number. Print the name only, never the password:
  `aws secretsmanager get-secret-value --secret-id <name> --query SecretString --output text | python -c 'import json,sys; print(json.load(sys.stdin)["username"])'`
- The consumer works with the new credential. For the audit login, the next export run completes
  (`RunsCompleted` in the heartbeat alarm's metric) and `scripts/verify_audit_export.py` still
  verifies the chain. For the backend, run `scripts/prove_backend_login.py`, which accepts either `meridian_backend` or
  `meridian_backend_clone` (as `release_identity.py` preflight and `kill_and_resume_proof.py` do for their logins).
- The rotation Lambda's log group has one line per step and, on a failure, a fixed phrase and the AWS
  error code, never a password or the service's own text.
- Run `tests/test_migration_034_live.py` again after any later migration that grants a login
  something or changes its settings.

What to read when a rotation fails: the Lambda's log, then `describe-secret`. A failed `setSecret` or
`testSecret` leaves `AWSCURRENT` where it was and nothing to undo. A failed `finishSecret` has already
moved `AWSCURRENT` back. Secrets Manager shows the rotation as failed. It does not clean up for you:
the `RotateSecret` API documents that when `AWSPENDING` is present on a version other than
`AWSCURRENT`, a later `RotateSecret` call assumes a rotation is still in progress and returns an
error. After a failed rotation, check `describe-secret` for a leftover `AWSPENDING`, read the
Lambda log for the cause, fix it, then remove the label with `aws secretsmanager
update-secret-version-stage --secret-id <name> --remove-from-version-id <id> --version-stage
AWSPENDING` before rotating again.

### Roll back

- **Stop rotating one login:** `aws secretsmanager cancel-rotate-secret --secret-id <name>`, then
  remove that login from `rotateLogins` and deploy again, or delete the stack (turn off its
  termination protection first). The secret keeps naming whichever user it names, and both users
  keep working.
- **Return the secret to the original user:** run `provision_service_logins.py --login <name> --apply
  --i-understand-this-changes-aws` (or `provision_workflow_login.py` for the workflow login). It sets
  the original user's password and writes the secret with the original user's name. Do not run it
  while a rotation is in progress.
- **Retire the clones and the rotator:** after the secrets name the originals again, run as the master
  login `ALTER ROLE <login>_clone NOLOGIN PASSWORD NULL` for each clone, then `DROP ROLE` for each
  clone and for `meridian_rotator`, and delete the rotator's secret and managed policy
  (`MeridianRotatorAuroraAccess`). The clones own nothing, so the drop needs no reassignment.

### What it does not cover

- The master login's password and the Cognito users' passwords. The master secret is managed by
  Amazon RDS, which rotates it.
- A consumer that reads a secret once at start-up and keeps the value for longer than the rotation
  interval. None does today, and a new one must be added to this list before its login rotates.
- Whether Aurora accepts `ALTER ROLE ... PASSWORD` from a CREATEROLE login with ADMIN OPTION through
  the Data API, whether the row policies apply to a clone through its membership, and how Secrets
  Manager labels versions around `finishSecret` have been checked against the documentation and
  with fakes, not against the live cluster. The first rotation of the audit login is that check.
- A global database. A rotation changes the primary cluster's roles, which a secondary cluster
  receives through replication; see [Recover in another Region](#recover-in-another-region).

## Sign-in and who is calling

A signed-in person is the only source of the traveler identity. The Amazon
Cognito user pool has two seeded users: Jordan Morgan, bound to
`trv_meridian_demo`, and Jordan Lee, the decoy, bound to `trv_demo_decoy`. The
web client signs in inline with Cognito's `USER_PASSWORD_AUTH` API over HTTPS.
Passwords go directly to the regional Cognito endpoint, never to the backend or
browser storage. Authorization code with PKCE remains the hosted fallback for
additional verification. An access token lasts one hour. A pre-token-generation trigger reads the user's active
`cognito` row in `traveler_identity_bindings` and copies its `traveler_id` into
the access token. A user with no active binding cannot sign in.

1. Deploy the identity stack. It needs `AURORA_IDENTITY_SECRET_ARN` in `.env`
   (step 2 above) and a Hosted UI domain prefix that is unique in the Region.
   Return URLs are HTTPS, or HTTP on localhost. Run the build and the deploy
   from `infra/` with the real npm binary; `npm` is an alias in some shells and
   a chained `npm` command can hide a failure:

   ```bash
   cd infra
   npm run build
   node_modules/.bin/cdk deploy -a "node dist/bin/meridian-identity.js" \
     -c domainPrefix=<unique-prefix> \
     -c callbackUrls=http://localhost:5173/showcase,https://<site-host>/showcase
   cd ..
   python scripts/sync_cognito_env.py --write
   ```

   The stack uses the Essentials plan and the V2_0 pre-token-generation
   trigger, which the account accepted. Amazon Cognito allows a trigger 5
   seconds, and the function times out at 4. The trigger retries its Aurora
   lookup once after a dropped connection (never after an answer from the Data
   API or a read timeout), and only while the second attempt can end 0.3 s
   before the function does. The client allows `USER_PASSWORD_AUTH` for the inline
   form and the authorization code flow with openid, profile and email scopes, no client
   secret, a one-hour access and ID token and a 24-hour refresh token. It also
   enables `ADMIN_USER_PASSWORD_AUTH`, which only a caller with IAM permission
   can use; the live tests use it to mint real tokens. Inline sessions refresh through
   `GetTokensFromRefreshToken` and revoke through `RevokeToken`. Tokens remain in memory,
   so reloading requires another sign-in. The browser sends no credential request to an
   arbitrary domain and refuses redirects on Cognito API requests.

   Deploy this client setting before the inline frontend. The web stack's CSP allows
   the configured Cognito domain and its regional API endpoint, with no wildcard.
   An MFA or password-change challenge never opens the application: the form offers
   the hosted flow to finish verification. The reference pool has MFA and account
   self-recovery disabled; production rollout must choose and test those policies.

   `sync_cognito_env.py` writes `MERIDIAN_COGNITO_REGION`,
   `MERIDIAN_COGNITO_USER_POOL_ID` and `MERIDIAN_COGNITO_APP_CLIENT_ID` to `.env`,
   and `VITE_COGNITO_DOMAIN` and `VITE_COGNITO_CLIENT_ID` to
   `frontend/.env.development.local`. Vite reads that file only in development.
   A hosted `jwt` build gets the same two values from the identity stack outputs
   (see [Sign-in settings in the web build](#sign-in-settings-in-the-web-build)).

2. Create the users. The script generates each password, stores it in AWS
   Secrets Manager (`meridian/cognito/jordan` and `meridian/cognito/decoy`) and in
   the macOS Keychain (service `meridian-cognito`), and writes the binding rows.
   It prints no password. Without `--apply` it only reports:

   ```bash
   python scripts/seed_cognito_users.py
   python scripts/seed_cognito_users.py --apply
   ```

   Off macOS there is no Keychain. `--apply` then stops before it creates, resets or stores
   anything and names the fix: add `--no-keychain` to keep each password in Secrets Manager only
   (`python scripts/seed_cognito_users.py --apply --no-keychain`). The token tools read the
   password from Secrets Manager, so the binding is complete either way.

   What the script does, as verified against the live cluster:

   - It checks that each traveler exists before it creates anything. The
     `travelers` table forces row-level security, so the check runs inside a
     transaction pinned to that traveler with `app.current_traveler_id`;
     an unpinned read returns nothing even when the row exists. A missing
     traveler stops the run and names the seed script to run first.
   - It stops when the user's `sub` already has an active `cognito` binding to a
     different traveler, and tells you to revoke the extra binding (set
     `status = 'revoked'` as the master login) and re-run. A reset keeps the
     `sub` and does not revoke bindings, so it warns instead of leaving them.
   - A run that stops partway is repaired by running it again. An existing user
     is reset with a new password, and the secret, Keychain item and binding row
     are rewritten. A reset does not refresh `name` or `picture`.

   The two sign-in emails are `jordan.morgan@example.com` (Jordan Morgan) and
   `jordan.lee@example.com` (Jordan Lee). To sign in by hand, open the web app
   locally, choose sign in, and enter one of those emails. Read the password
   from the Keychain when asked, and never paste it into a file or a chat:
   `security find-generic-password -s meridian-cognito -a <email> -w`.

   Jordan Lee signs in and uses the app with their own data. The App Runner
   instance role and the `MeridianWorkflow` Runtime role hold an `aws_iam`
   binding for `trv_demo_decoy` as well as for `trv_meridian_demo`:

   ```bash
   python scripts/bind_web_backend_role.py --traveler trv_demo_decoy            # dry run
   python scripts/bind_web_backend_role.py --traveler trv_demo_decoy --apply
   python scripts/bind_workflow_runtime.py --traveler trv_demo_decoy            # dry run
   python scripts/bind_workflow_runtime.py --traveler trv_demo_decoy --apply
   ```

   `--traveler` accepts only the two seeded Cognito travelers and defaults to
   Jordan Morgan. Without `--apply` the script prints the plan and writes
   nothing. The holds Lambda role stays bound to Jordan Morgan only, so a hold
   for the decoy is refused at the holds grant, and `bind_gateway_workload.py`
   and `bind_current_identity.py` refuse `trv_demo_decoy` before any write.
   `tests/test_decoy_has_no_workload_binding.py` pins the Gateway rule, and its
   database test checks that the cluster has no active decoy binding for the
   holds role. If Jordan Lee sees "Traveler details are unavailable", the
   backend role has no decoy binding yet. Sign in again after the grant.

3. Check the claim, the verification and the refusals against the deployed pool:

   ```bash
   python -m pytest tests/test_cognito_aurora.py -m database
   ```

How the backend decides who is calling. `require_http_principal` tries a bearer
token in this order and never retries a failed token against a weaker check:

1. The shared `MERIDIAN_API_TOKEN`. `scripts/publish.py` refuses to pass it, or
   `MERIDIAN_ALLOW_INSECURE_LOCALHOST`, to a `jwt` service, so a `jwt` release has no
   such path. The `iam` build still uses it.
2. A Cognito access token, when the three `MERIDIAN_COGNITO_*` settings are
   present. The signature, issuer, `token_use=access`, app client and expiry are
   verified, and the traveler is the verified `traveler_id` claim. An ID token, a
   token for another app client and a token with a changed claim are refused.
3. A direct connection from the same machine in development, so local scripts and
   tests run without a user pool. This path is skipped when `MERIDIAN_API_TOKEN`
   is set, because a set token makes every other request fail with 401.

`GET /api/me` returns the traveler the credential is bound to. The page sends
`me` instead of a traveler id, and the API resolves it. A request that names any
other traveler id is refused with 403, including one from the decoy that names
Jordan.

The workload grant is a second check. The App Runner role and the Workflow
Runtime role are bound to both seeded travelers, so Jordan Lee reaches their own
records; the holds Lambda role is bound to Jordan Morgan only, so a hold for the
decoy is refused there. A request that names another traveler is refused before
the grant by the identity check.

### Sign-in settings in the web build

`VITE_COGNITO_DOMAIN` and `VITE_COGNITO_CLIENT_ID` together turn on the sign-in
screen. With neither set, the bundle is an ungated build and writes one console
line, "Meridian is running as an ungated build: sign-in is not configured."
`VITE_REQUIRE_SIGN_IN=1` makes a build fail when either variable is missing
instead of shipping without sign-in; leave it unset for local and plain builds.
The failure message names both variables.

A `jwt` build from `scripts/publish.py` sets `VITE_REQUIRE_SIGN_IN=1` and both
Cognito variables from the identity stack outputs (`HostedUiDomain` and
`AppClientId`), so it fails rather than shipping ungated. An `iam` build blanks
all three, so a variable exported in the operator's shell cannot gate it. Local
development reads the two Cognito variables from
`frontend/.env.development.local`.

### Revoking access: what stops at once and what waits

An access token stays valid for up to its hour after you revoke the user's
binding or disable the user. The backend checks the token's signature, issuer,
`token_use`, app client and expiry on each request. It does not ask Cognito or
re-read `traveler_identity_bindings`, so a token that was issued before the
change keeps working until it expires. What stops immediately is new sign-ins
and refreshes: the pre-token-generation trigger runs on a refresh and fails
closed when the user has no active `cognito` binding, and a disabled user
cannot sign in.

To revoke as fast as the system allows:

1. Revoke the binding as the master login
   (`UPDATE traveler_identity_bindings SET status = 'revoked' WHERE ...`).
2. Disable the user (`aws cognito-idp admin-disable-user`, the
   `AdminDisableUser` API) and invalidate the user's refresh tokens
   (`aws cognito-idp admin-user-global-sign-out`, the `AdminUserGlobalSignOut`
   API). The repository has no script for either call; run them against the
   pool in `MERIDIAN_COGNITO_USER_POOL_ID` with the user's email as the
   username.
3. If the hour is too long, the only switch that cuts off an already issued
   token is on the backend: change or unset the three `MERIDIAN_COGNITO_*`
   settings and restart the service. That refuses every Cognito token, for all
   users, until you restore them. `AdminUserGlobalSignOut` alone does not do
   this, because the backend never calls Cognito to check a token.

### The browser test suite runs ungated

`npm run test:accessibility` (Playwright, from `frontend/`) starts its dev server
with `--mode e2e` on port 4174. That mode does not read
`frontend/.env.development.local`, so a machine that ran
`scripts/sync_cognito_env.py --write` still gets the showcase and the suite
passes. The suite is ungated by design. The sign-in screen is covered by the
`gated` Playwright project (`e2e/gated/signIn.spec.ts`), which starts a second
server on port 4175 in mode `gated-e2e` with placeholder `VITE_COGNITO_*`
values, and by the `AuthGate` component tests. Neither uses the real settings.

### Roll back the service logins and sign-in

Undo in the reverse of the order above and stop at the step you need. Before step 3, remove
everything that still attaches a login's managed policy, because IAM refuses to delete a policy
that a role uses: the hosted web roles (the App Runner instance role or the Express task role,
which attach `MeridianBackendAuroraAccess`) and the AgentCore stack (its Workflow Runtime role attaches `MeridianWorkflowAuroraAccess`) and the
catalog Lambda's role (which attaches `MeridianCatalogAuroraAccess`). Retire the hosted app and then AgentCore first; the
README's [Clean up](../../README.md#clean-up) list gives the whole order.

1. Users. Delete the two Cognito users and the two `meridian/cognito/*`
   secrets, delete the `cognito` binding rows
   (`DELETE FROM traveler_identity_bindings WHERE identity_provider = 'cognito'`
   as the master login), then, on the macOS machine that seeded them (a `--no-keychain` run has none), remove the Keychain items:

   ```bash
   security delete-generic-password -s meridian-cognito -a jordan.morgan@example.com
   security delete-generic-password -s meridian-cognito -a jordan.lee@example.com
   ```

2. Identity stack. From `infra/`, destroy the stack with the same `-c` values
   you deployed with, then remove the settings it wrote:

   ```bash
   node_modules/.bin/cdk destroy --exclusively -a "node dist/bin/meridian-identity.js" \
     -c domainPrefix=<unique-prefix> -c callbackUrls=<urls> --force
   sed -i '' '/^MERIDIAN_COGNITO_/d' .env   # macOS; on Linux: sed -i '/^MERIDIAN_COGNITO_/d' .env
   rm frontend/.env.development.local
   ```

3. Logins (provisioning). For each of the three roles, run
   `ALTER ROLE <role> NOLOGIN PASSWORD NULL`, delete its secret with
   `ForceDeleteWithoutRecovery`, delete the non-default versions of its managed
   policy and then the policy, and remove the three `AURORA_*_SECRET_ARN` lines
   from `.env`. Do this before the migration rollback.

   The catalog login is a fourth. Detach `MeridianCatalogAuroraAccess` from
   `MeridianCatalogToolsRole` first (or delete the function and its role first), then run
   `ALTER ROLE meridian_catalog NOLOGIN PASSWORD NULL`, delete its secret with
   `ForceDeleteWithoutRecovery`, delete the policy's non-default versions and then the policy, and remove
   the `AURORA_CATALOG_SECRET_ARN` line from `.env`. Then, as the master login:

   ```sql
   DROP OWNED BY meridian_catalog;
   DROP ROLE meridian_catalog;
   DELETE FROM schema_migrations WHERE migration_name = '022_catalog_login.sql';
   ```

   Roll it back only while nothing uses the login: the `meridian-catalog-tools` Lambda stops answering
   without it.

   The workflow login is a fifth. Delete the AgentCore stack first (its Runtime role attaches
   `MeridianWorkflowAuroraAccess`), then run `ALTER ROLE meridian_workflow NOLOGIN PASSWORD NULL`,
   delete the `meridian/aurora/workflow-login` secret with `ForceDeleteWithoutRecovery`, delete the
   policy's non-default versions and then the policy, and remove the `AURORA_WORKFLOW_SECRET_ARN`
   line from `.env`. When you are deleting the cluster too, skip the `ALTER ROLE` and the SQL in
   this step and the next, for every login.

4. Migration 018. Run these as the master login, in this order. The five
   `admin_count_select` policies belong to the master, so `DROP OWNED BY` on
   the service roles does not remove them and each needs its own statement:

   ```sql
   DROP POLICY IF EXISTS traveler_preferences_admin_count_select ON traveler_preferences;
   DROP POLICY IF EXISTS trip_interactions_admin_count_select ON trip_interactions;
   DROP POLICY IF EXISTS conversations_admin_count_select ON conversations;
   DROP POLICY IF EXISTS conversation_messages_admin_count_select ON conversation_messages;
   DROP POLICY IF EXISTS agent_audit_admin_count_select ON agent_audit_log;
   DROP POLICY IF EXISTS traveler_access_audit_backend_insert ON traveler_access_audit;
   DROP POLICY IF EXISTS traveler_access_audit_gateway_insert ON traveler_access_audit;
   DROP FUNCTION IF EXISTS backend_admin_count(TEXT, INTERVAL, TEXT);
   DROP OWNED BY meridian_backend, meridian_gateway, meridian_identity;
   DROP ROLE meridian_backend;
   DROP ROLE meridian_gateway;
   DROP ROLE meridian_identity;
   DELETE FROM schema_migrations WHERE migration_name = '018_service_logins.sql';
   ```

   Roll back only while no workload uses the logins. In a `jwt` release the
   workloads use them, so move the workloads back to the master login first.

## Switch the AgentCore identity mode

`MERIDIAN_AGENTCORE_AUTH` chooses how every AgentCore call is authenticated. `iam` (unset means
`iam`) signs each call with AWS credentials. `jwt` sends the signed-in person's Cognito access
token on every hop. Any other value is refused by name.

### What `jwt` mode switches

- Both Runtimes use inbound JWT authorizers and list `Authorization` in their request header
  allowlist.
- The Gateway is `meridian-aurora-jwt`, a `CUSTOM_JWT` Gateway with the request interceptor
  attached. The `iam` Gateway (`meridianv2-meridian-aurora`) cannot change its authorizer, so the
  release replaces it (see [How the Gateway is replaced](#how-the-gateway-is-replaced)).
- The Cedar policy `meridian_traveler_binding` is in the policy engine. The render adds it in
  `jwt` mode, unless `MERIDIAN_GATEWAY_ENFORCEMENT=interceptor`.
- The backend and the hosted service set `MERIDIAN_AGENTCORE_AUTH=jwt`, and `scripts/publish.py`
  does not pass `MERIDIAN_API_TOKEN` or `MERIDIAN_ALLOW_INSECURE_LOCALHOST` to the service.
- The command-line proofs and checks other than the three smoke scripts still sign with AWS
  credentials and stop working against a `jwt` Runtime until they are converted.
- `StopRuntimeSession` is posted with the bearer token in `jwt` mode: a JWT Runtime refuses a SigV4 stop ("Authorization method mismatch").

### Prerequisites

Do not start until all of these hold.

1. The throwaway-Gateway harness (`scripts/run_gateway_harness.py`) has been run with `--apply
   --i-understand-this-creates-aws-resources` and its verdict table is recorded. It decides whether
   the interceptor and Cedar are both needed. The profile needs the tag permissions
   `lambda:TagResource`, `lambda:ListTags`, `iam:TagRole`, `iam:ListRoleTags`,
   `bedrock-agentcore:TagResource` and `bedrock-agentcore:ListTagsForResource`; the dry run (no
   flags, no AWS call) lists them. If a run is interrupted, `--teardown
   .local/gateway-harness/<name>/ledger.json --i-understand-this-creates-aws-resources` deletes what
   it left; the ledger must be inside that folder. Flags must be spelled out in full. Exit code 0
   is a pass, 1 a failed or unknown check or an unexpected error, 2 resources left behind, 3
   refused (including any command-line usage error).
2. The interceptor Lambda is deployed with its own role: invoke permission for the Gateway role and
   log access only, no Aurora access.
3. The Cognito user pool, the two seeded users and their `cognito` binding rows exist, and
   `python scripts/cognito_tokens.py` mints an access token for each (see
   [Sign-in and who is calling](#sign-in-and-who-is-calling)).
4. The three `MERIDIAN_COGNITO_*` settings are in `.env`, and the Cognito app client id is the one
   both Runtime authorizers and the Gateway authorizer will allow.
5. The previous Gateway, both Runtime and hosted service configurations are saved outside the
   repository, and the previous backend image and site bundle can be deployed again.
6. A maintenance window is agreed, because a Runtime accepts IAM or JWT callers, never both, and
   every IAM caller fails from the moment its Runtime changes.

### The switch

Set `MERIDIAN_AGENTCORE_AUTH` in `.env` and render. The render writes the value into both Runtimes'
environment and adds the Cedar rule in the same pass. It writes only the ignored local files and
prints `AgentCore identity mode: jwt`, the Gateway enforcement design and, in `jwt` mode, whether
the Cedar rule is in the render (`Cedar rule: included (meridian_traveler_binding)` or `Cedar rule:
omitted for this deploy`); compare the files with the saved configuration before deploying.

```bash
python scripts/render_agentcore_config.py
```

The release then changes, in one window and with a read-back after each step: a new Gateway, both
Runtimes and the Cedar rule through four staged stack deploys, the holds Lambda, the interceptor, and last
the backend and the hosted service with `MERIDIAN_AGENTCORE_AUTH=jwt`. Setting `jwt` on the backend alone, or on one Runtime alone, makes
that hop send or expect a credential the next hop refuses.

### How the Gateway is replaced

An existing Gateway can never change its authorizer type. CloudFormation refused it in the first window
("Authorizer type cannot be updated for an existing gateway"), and the `UpdateGateway` API refused it in the
third window (the same words, a `ValidationException`). Only a new Gateway can be `CUSTOM_JWT`, so the `jwt`
release replaces the Gateway. Nothing is moved in place and no command changes a live authorizer.

**The new Gateway's identity.** The render names the Gateway after the mode. The name is the construct id
the CDK builds the CloudFormation logical id from, so a different name is a different resource.

| | `iam` | `jwt` |
| --- | --- | --- |
| Name in `agentcore.json` | `meridian-aurora` | `meridian-aurora-jwt` |
| Name the control plane reports | `meridianv2-meridian-aurora` | `meridianv2-meridian-aurora-jwt` |
| Construct id | `GatewayMeridianAurora` | `GatewayMeridianAuroraJwt` |
| Authorizer | `AWS_IAM` | `CUSTOM_JWT`, the pool's discovery URL, the web app client alone |
| URL variable on each Runtime | `AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL` | `AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL` |
| Key in `agentcore/.cli/deployed-state.json` | `mcp.gateways.meridian-aurora` | `mcp.gateways.meridian-aurora-jwt` |
| Runtime roles | get `bedrock-agentcore:InvokeGateway` on it | get no such statement (the CDK adds it for `AWS_IAM` only) |

A Runtime has exactly one of the two URL variables, so the Runtime code reads whichever is set and has
no mode branch for it. Every tool name is the same in both Gateways (`MeridianCatalog___hybrid_trip_search`,
`MeridianHolds___create_courtesy_hold` and the others), because the `iam` and `jwt` renders use the same
target names.

**Why the old and the new Gateway cannot sit side by side.** The preferred cutover (create the new Gateway
next to the old one, repoint, prove, delete the old one later) is not possible with this tooling, and the
repository pins the reasons in the CDK tests (`meridian_agentcore/agentcore/cdk/test/cdk.test.ts`):

- Both Gateways would have a target called `MeridianHolds` and one called `MeridianCatalog` (the catalog target's
  name since Release 2). The
  CDK adds the outputs `GatewayTarget<Name>IdOutput` at stack scope, so two Gateways with the same target
  names throw "already a Construct" while the stack is synthesized. Only different target names would avoid
  it, and a target name is the prefix of every tool name (`<target>___<tool>`) in the Concierge, the
  workflow, the interceptor, the Cedar rules and the proof scripts.
- The holds Lambda is created as `meridianv2-MeridianHolds` (the CDK builds the name from the project and the
  target), and a function name is fixed at creation. Replacing the Gateway creates the new function before
  CloudFormation deletes the old one, so a single deploy that swaps the Gateway would fail on the name.
- CloudFormation deletes a resource that leaves the template when the update ends. Keeping the old Gateway
  until a later step would need a retain policy that the project specification cannot express, and a retained
  Gateway would lose its role, targets and Lambda in the same update, so it would not be a rollback target.

So the replacement is built in stages (the first deployment's order), and the old Gateway is deleted by the
first of them. There is no `retire-old-gateway` command because there is nothing left to retire after that
deploy. The protection moves in front of it: the first stage refuses to run on a working tree with uncommitted
changes, without a complete snapshot of the `iam` release that was taken of the Gateway about to be deleted (the
same Gateway id, account and Region), without a passing backend login proof at the current commit or the
interceptor Lambda, when the stage disagrees with the live Gateway, or when the plan is not one that adds the
new Gateway and removes the old one.

**The four stages.** `render_agentcore_config.py` reads the CLI's deployed state and renders the first stage
the state does not yet list. `release_identity.py deploy` checks that the rendered stage, the state's stage
and the live Gateway agree, reads the plan, deploys once and reads the Gateway back by name. Before anything
else it refuses a missing or stale staged Concierge copy or Workflow bundle with exit 2, before the confirmation
check (restage with `scripts/stage_concierge_runtime.py` and `scripts/stage_workflow_runtime.py`; the render does both).
It also refuses, in the dry run as well, a render whose Runtimes differ from `agentcore.template.json` in name or
`runtimeVersion` (render again): a render made before a Python pin moved would otherwise ship the new code on the old
runtime.

| Stage | The render holds | What the deploy does | Why it is separate |
| --- | --- | --- | --- |
| 1 `gateway` | The new Gateway, `MeridianCatalog`, no `MeridianHolds`, no Cedar engine, both Runtimes with the Cognito authorizer | Creates the Gateway, its role and target; moves both Runtimes to the Cognito authorizer; deletes the old Gateway, its role, its targets, the old holds Lambda, the Cedar engine and its rules | The old holds Lambda holds the function name until CloudFormation deletes it. Its log group is kept, so `holds-logs` deletes it before stage 2 |
| 2 `targets` | Adds `MeridianHolds` and the Gateway id on the Runtimes | Creates the holds Lambda, its log group and role and the target. The tool refuses while the old log group exists and prints the `holds-logs` command | Cedar validates a rule against the tools that exist, so the tool comes before the rule |
| 3 `governance` | Adds the Cedar engine, the rules and the association | Creates the engine and rules and attaches the engine in `ENFORCE` mode | The rules name the Gateway, whose id is known only after stage 1 |
| 4 `complete` | Adds the engine id on the Runtimes | Updates both Runtimes | The engine id is known only after stage 3 |

**The retained holds log group.** The CDK creates the holds Lambda's log group with an explicit name,
`/aws/lambda/meridianv2-MeridianHolds`, and keeps it when the function is deleted (the plan shows it as
retained). Stage 2 creates the function again together with a log group of that very name, and CloudFormation
refuses to create a log group that exists, so stage 2 would fail after the irreversible stage 1. The same
collision hits stage 2 of a rollback to `iam`. The tool therefore deletes that one group between the two
stages, with no log export (the contents are the old function's debug logs; export them first if you want
them):

```bash
venv/bin/python scripts/release_identity.py holds-logs
venv/bin/python scripts/release_identity.py holds-logs --apply --i-understand-this-changes-aws
```

`holds-logs` derives the group's exact name from the project name and the target name, never a pattern, and
lists by that prefix only to find a group whose name is exactly that one (a group that merely starts with it is
never touched). It refuses (exit 2) while the holds function still exists, since stage 1 has not deleted it.
It deletes the group, reads back that it is gone (exit 1 with `DRIFT` if it stays), and is a clean no-op when
the group is already absent. The stage 2 preflight reads the same group and refuses, in the dry run as well,
while it exists, with the command to run. The operator profile needs `logs:DescribeLogGroups` and
`logs:DeleteLogGroup` on that log group and `lambda:GetFunctionConfiguration` on the holds function.

**What the plan gate accepts.** `release_identity.py deploy` runs `agentcore deploy --diff --json` and reads the
resource lines (`[+]`, `[~]`, `[-]`, a type, a path and a logical id). It refuses a plan with no parsed
resource line at all, in every stage, and shows the tail of the output with pool ids, client ids and the
hosted-UI host masked. Each stage must also show its own change:

| Stage | The plan must | Removals |
| --- | --- | --- |
| 1 `gateway` | add exactly one Gateway, the mode's, and, when the other mode's Gateway is live, remove exactly one Gateway, that one | Only resources whose path or logical id carries the replaced Gateway's construct id (`GatewayMeridianAurora` or `GatewayMeridianAuroraJwt`; the first is a prefix of the second and is told apart) and the `MeridianGovernance` engine with its rules. Any other removal, a Runtime or the Memory included, refuses the deploy |
| 2 `targets` | add a `GatewayTarget` and a `Lambda::Function` | None allowed |
| 3 `governance` | add a `PolicyEngine` and a `Policy` | None allowed |
| 4 `complete` | update a `Runtime`, or add the `MeridianCatalog` target, or update a Cedar rule (`Policy`), or be a code-only Lambda hotfix: every change an update (`[~]`) of a `Lambda::Function` whose changed properties are only `Code` and `Metadata`, and nothing else | Only a target named in `RETIRED_TARGETS` (`SemanticTripSearchLambda`), and only when the same plan updates a `Runtime`; nothing else |

Two live plans of the `complete` stage are saved, masked, in `tests/fixtures/real_plans/` (Release 2 Deploy A and
Deploy B, 28 October 2026), and the gate's tests run against them. In that format a changed field is a
`[~] .Field:` line (leading dot, trailing colon) with its old and new values one level deeper as `[-]` and `[+]`
lines. The plans of stages 1 to 3 have not been captured; the other tests use fixtures built from the resource
types, construct paths and logical ids of the synthesized template, which show the gate's logic. Read the first
live plan of each of those stages by eye (with approval, since `agentcore deploy --diff` calls AWS) and expect
the gate to refuse it with the masked output tail if the layout differs.

After stage 4 the interceptor is attached with `release_identity.py gateway`. The template cannot declare it,
and a deploy that updates the Gateway can detach it, so `check` is the drift read after any later deploy.

**Who learns the new Gateway.**

| Consumer | How it follows | When |
| --- | --- | --- |
| Both Runtimes' `AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL`, `MERIDIAN_GATEWAY_ID`, `MERIDIAN_POLICY_ENGINE_ID` | The CDK and the render write them in the stage deploys; `check` compares each with the live Gateway | Stages 1, 2 and 4 |
| Backend and hosted service (`AGENTCORE_GATEWAY_URL` in `.env` and in the service) | `sync_agentcore_env.py --write` after each deploy, then `publish.py` | After stage 4 |
| SSM parameters under `/meridian/aurora/` | None names the Gateway. `/meridian/aurora/secret_arn` moves to the gateway login after stage 2, when the new holds role can read that secret | After stage 2 |
| Holds Lambda and its role | New function, new role (a new role id), created in stage 2; run `bind_gateway_workload.py` again so the database grant names the new role | After stage 2 |
| `meridian-catalog-tools` | The function is outside the stack; the new Gateway role is granted `lambda:InvokeFunction` on it by the stack | Stage 1 |
| Policy engine association | The engine is deleted in stage 1 and created again in stage 3; the Gateway is attached to it in the same deploy | Stage 3 |
| Interceptor | `release_identity.py gateway --to jwt`: the invoke grant on the Gateway's role, then the attach | After stage 4 |
| `MeridianTravelerPinInvoke` on the old Gateway's role | Written by an earlier window. Remove it before stage 1 (`gateway --to iam --apply ...`), because CloudFormation may fail to delete a role that carries a policy it does not know (assumed, not observed) | Before stage 1 |
| `.env` Gateway id and URL | Stale from stage 1 on; `check` says so and names the sync command | After stage 1 |

**Outage.** The Runtimes change authorizer in stage 1, and the backend still signs with IAM until it is
published, so signed-in traffic fails from stage 1 until the publish step. That window exists in any
release of this mode (a Runtime accepts IAM or JWT callers, never both). The replacement adds no second
outage: the Gateway is also unavailable between the deletion in stage 1 and the attach after stage 4, inside
the same window. The durations are not measured; plan for four stack updates, one interceptor attach and a
publish. Between stage 2 and stage 3 the holds tool exists without its Cedar rules, and until the attach it
has no traveler pin; the backend cannot reach the Runtimes in that period, so only a caller who already holds a
valid Cognito access token could reach them directly.

**Rollback.** `rollback` restores what the APIs can restore (the site, the service, the secret parameter and
the catalog Lambda). It never restores the Gateway or the Runtimes' environment from the snapshot when the
Gateway was replaced, because that environment holds the deleted Gateway's URL. Rolling back to `iam`
is the same staged build in the other direction: `MERIDIAN_AGENTCORE_AUTH=iam`, render, deploy, four times,
which creates a new `iam` Gateway. It has a new id and a new URL, so every consumer in the table above
follows again (Runtimes by the deploy, the backend and the service by `sync_agentcore_env.py --write` and
`publish.py`, the holds role by `bind_gateway_workload.py`). Revoke the interceptor grant first
(`gateway --to jwt --only revoke --apply ...`) for the same reason as before stage 1.

### The window order

Every command is a dry run until it gets `--apply --i-understand-this-changes-aws`; read the plan, then
ask. Use `check --skip-service` for every read until the service moves (step 14).

1. Save the state that stage 1 deletes, at the commit you will release:
   `venv/bin/python scripts/release_identity.py snapshot --service-arn "$SERVICE_ARN"`, and the backend login
   proof in `iam`: `venv/bin/python scripts/prove_backend_login.py --mode iam --apply
   --i-understand-this-changes-aws`.
2. Set `MERIDIAN_AGENTCORE_AUTH=jwt` in `.env`, then `venv/bin/python scripts/release_identity.py check
   --skip-service`. It lists drift for the Gateway name, both Runtimes and the rules. Nothing live changes.
3. `venv/bin/python scripts/release_identity.py interceptor`, then with the flags. It deploys the
   interceptor Lambda and its log-only role (a second run changes nothing).
4. Nothing to move for the catalog Lambda: it runs as its own login, `meridian_catalog`, from
   creation, and `lambdas` checks that it still does.
5. Clear the old Gateway's role: `venv/bin/python scripts/release_identity.py gateway --to iam`, then with
   the flags. It removes `MeridianTravelerPinInvoke` and detaches any interceptor; nothing else changes.
6. Stage 1. Render and validate, then deploy through the tool. The outage starts here.

   ```bash
   venv/bin/python scripts/render_agentcore_config.py
   (cd meridian_agentcore && npx -y @aws/agentcore@0.31.2 validate --json)
   venv/bin/python scripts/release_identity.py deploy
   venv/bin/python scripts/release_identity.py deploy --apply --i-understand-this-changes-aws
   venv/bin/python scripts/sync_agentcore_env.py --write
   ```

   The preflight refuses (exit 2, nothing deployed) unless the rendered Gateway is the mode's, the render,
   the deployed state and the live Gateway agree on the stage (the live Gateway's targets and policy engine are
   read too), the working tree is clean, the old Gateway's role is clear, a complete snapshot of the `iam`
   release exists that was taken of the old Gateway (the dry run prints its commit and age), the proof receipt is
   at this commit, the interceptor Lambda is deployed, and the plan from `agentcore deploy --diff --json` adds
   the new Gateway and removes the old one and what belongs to it, and the CLI reports the pinned release
   (0.31.2). It then runs `npx -y @aws/agentcore@0.31.2 deploy -y`, waits for the new Gateway, reads it back by name and reports the old
   Gateway as `DRIFT` if it still exists, with the manual steps. The next steps are printed whatever the
   read-back found, because the deployed state has moved on. A failed deploy sends you to the stack status in
   CloudFormation first; a stack in `UPDATE_ROLLBACK_FAILED` needs `aws cloudformation
   continue-update-rollback` before anything else.

   Then delete the old holds function's retained log group, which stage 2 would otherwise collide with (see
   the design note above; the logs are not exported):

   ```bash
   venv/bin/python scripts/release_identity.py holds-logs
   venv/bin/python scripts/release_identity.py holds-logs --apply --i-understand-this-changes-aws
   ```

7. Stage 2: `venv/bin/python scripts/render_agentcore_config.py`, then `release_identity.py deploy` and
   `deploy --apply ...` as in step 6. The preflight refuses while the old holds log group exists (it names the
   `holds-logs` command), and if the plan removes anything or does not add the holds target and Lambda.
8. Bind the new holds role: `venv/bin/python scripts/bind_gateway_workload.py`.
9. Move the holds Lambda and its parameter to the gateway login. The write refuses until the new holds
   role can read that secret, which stage 2 grants.

   ```bash
   venv/bin/python scripts/publish_gateway_parameters.py --gateway-login
   venv/bin/python scripts/publish_gateway_parameters.py --gateway-login --apply --i-understand-this-changes-aws
   venv/bin/python scripts/release_identity.py lambdas --restart-holds --apply --i-understand-this-changes-aws
   venv/bin/python scripts/release_identity.py lambdas --expect gateway
   ```

10. Stage 3: render, `release_identity.py deploy`, `deploy --apply ...`. The render prints the Cedar rule line.
11. Stage 4: render, `release_identity.py deploy`, `deploy --apply ...`, then `sync_agentcore_env.py --write`.
    The render prints `Configuration complete.`
12. Attach the interceptor: `venv/bin/python scripts/release_identity.py gateway --to jwt`, then with the flags.
13. `venv/bin/python scripts/release_identity.py check --skip-service` prints `OK` for every hop.
14. Publish the roles, the service and the site (`scripts/publish.py`, plan first). The outage ends. Then
    `venv/bin/python scripts/release_identity.py check --service-arn "$SERVICE_ARN"`.
15. Run the three smoke scripts as a seeded user, then `scripts/identity_proof.py` (see below).

What is verified and what is assumed:

| Statement | Status |
| --- | --- |
| No property of `AWS::BedrockAgentCore::Gateway` (including `Name` and `AuthorizerType`) causes replacement, so only a new construct id replaces the Gateway | Verified in the CloudFormation resource specification bundled with the installed AgentCore CLI (`@aws-cdk/aws-service-spec` 0.1.166) |
| Two Gateways with the same target names cannot be synthesized; the holds Lambda name is the same in both modes | Verified by the CDK tests, against `@aws/agentcore-cdk` 0.1.0-alpha.47 |
| The CDK wires `AGENTCORE_GATEWAY_<NAME>_URL` on every Runtime and adds `InvokeGateway` only for `AWS_IAM` | Verified in the installed construct code |
| The CLI rebuilds `deployed-state.json` from the stack outputs, so the replaced Gateway's entry disappears | Read in the installed CLI source; not yet seen after a real deploy. The render ignores a stale engine id before the last stage, but read the file after stage 1 |
| `agentcore deploy --diff --json` prints CDK diff lines (`[+] AWS::BedrockAgentCore::Gateway ...`) before the JSON status | Seen live for the `complete` stage (Release 2 Deploy A and Deploy B, saved masked in `tests/fixtures/real_plans/`); not yet seen for stages 1 to 3. The plan gate fails closed in every stage and prints the masked output tail, so read the first live plan of those stages by eye |
| A Cedar rule is validated against the tools that exist, so the tool precedes the rule | Documented in the runbook from the first deployment (two deploys for a tool and its rule) |
| CloudFormation can fail to delete a role that carries an inline policy it did not create | Assumed, not observed; the pre-clean costs one idempotent command |
| The new Gateway can be created with `CUSTOM_JWT` through the stack, and the engine attaches in `ENFORCE` mode in stage 3 | Assumed: the CDK synthesizes it, no deploy has done it |
| An update that omits `interceptorConfigurations` detaches the interceptor, and the interceptor is attached with one `UpdateGateway` | Measured on the throwaway Gateway (check C5) for the detach; the attach by `UpdateGateway` is the harness's own call |
| Stage durations and the size of the outage | Not measured |

### Read the release back

`scripts/release_identity.py check` only reads. It compares the Gateway, both Runtimes, the Cedar
rules, the identity stack, the backend login proof, the interceptor Lambda's environment and the
App Runner service with the mode in `.env`. Name the service with `--service-arn ARN`, or leave it
out on purpose with `--skip-service`. A `check` with neither prints `NOT CHECKED` and exits 4 even
when every hop matches, so use `--skip-service` for every read before the service moves (steps 1 to 8 of the
window) and `--service-arn` after it. The backend login proof is compared only in
`jwt` mode, so `check --expect iam` shows no proof line even when no receipt exists.
`interceptor`, `interceptor-delete`, `lambdas --restart-holds`, `gateway`, `deploy`, `holds-logs` and
`rollback` are dry runs unless they get `--apply --i-understand-this-changes-aws`. The backend
login proof comes from `scripts/prove_backend_login.py --apply
--i-understand-this-changes-aws`. Exit codes of `release_identity.py`:

| Code | Meaning |
| --- | --- |
| 0 | ok: every hop matches, or a dry run |
| 1 | drift: one `DRIFT` line per problem |
| 2 | could not run or compare: a bad setting, another account, an AWS error, a foreign resource, or an ordering precondition of `gateway` or `deploy` that is not met |
| 3 | usage error, or an apply refused because the confirmation flag is missing |
| 4 | every hop matches but the App Runner service was not named, so it is not fully checked |

Exit codes differ by tool, because 2 was taken before they were aligned. Read the row of the tool
you ran; `-` means the tool never returns it:

| Tool | 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- |
| `release_identity.py` | ok, or a dry run | drift, or a hop not restored | could not run or compare | usage error, or apply without the confirmation flag | hops match, service not checked |
| `publish_gateway_parameters.py` | written and read back, or a dry run | a parameter did not read back as written | could not run (setting, account, AWS error, or with `--gateway-login` the holds role cannot read the gateway secret yet) | usage error, or apply without the flag | - |
| `prove_backend_login.py` | passed | a check failed, the backend never answered, a crash or an interrupt | - | refused (precondition, usage, missing flag) | - |
| `run_gateway_harness.py` | pass | a check failed or stayed unknown, or an unexpected error | resources left behind | refused (also any usage error) | - |
| `publish.py` | published, or a plan | a refusal or a failure | - | usage error, or `--apply`/`--stage` without the flag | - |

### Move the Lambdas to the gateway login

In a deployment that predates the gateway login, one Lambda still connects as the master login until
this step: the `MeridianHolds` Lambda (through the SSM parameter `/meridian/aurora/secret_arn`). A new
deployment publishes the gateway login from the start (step 7 of the deploy runbook). The catalog Lambda is created on its own
`meridian_catalog` login and never moves, and `lambdas` checks it against that login at every stage.
Every command below is a dry run until it gets `--apply
--i-understand-this-changes-aws`, checks that the credentials belong to the account and Region of
`AURORA_CLUSTER_ARN` before it builds any other client, and prints no secret value.

The order matters. The holds Lambda's role belongs to the AgentCore stack and gets the read on the
gateway login's secret only from the first jwt deploy (step 4 of the window). The
`--gateway-login --apply` of `publish_gateway_parameters.py` therefore reads the holds role first
and refuses, with nothing written, until it can read that secret: moving the parameter earlier
would leave every new holds cold start unable to connect.

```bash
python scripts/publish_gateway_parameters.py --gateway-login
python scripts/publish_gateway_parameters.py --gateway-login --apply --i-understand-this-changes-aws
python scripts/release_identity.py lambdas --restart-holds --apply --i-understand-this-changes-aws
python scripts/release_identity.py lambdas --expect gateway
```

`publish_gateway_parameters.py` writes the three parameters and reads each back (the secret
parameter with the same comparison `lambdas` makes). The holds Lambda keeps its cached value until
`lambdas --restart-holds`. The operator profile needs `lambda:UpdateFunctionConfiguration` on
the holds function and `ssm:PutParameter` on `/meridian/aurora/*`.

### Run the smoke scripts as a seeded user

In `jwt` mode `smoke_workflow_runtime.py`, `smoke_gateway_tools.py` and `smoke_production_turn.py`
sign in as a seeded user through `scripts/agentcore_caller.py`. It mints the user's access token
with `scripts/cognito_tokens.py`, reads the password from Secrets Manager, prints nothing secret and
binds the token for the one call block. `smoke_production_turn.py --traveler trv_demo_decoy` signs in
as the decoy user; any other traveler id signs in as Jordan Morgan. In `iam` mode nothing is minted.

### Capture the signed-in app

`scripts/identity_capture/capture_session.py` signs the seeded users in for a screen capture without
anyone typing a password. The rule: tokens are minted only into an inherited pipe, never to standard
output. The launcher creates a private pipe, gives the write end to `mint_session.py` and the read end
to `capture.mjs` as `--token-fd N`, and the tokens never reach standard output, standard error, argv,
the environment or a file. `mint_session.py` refuses to run without `--token-fd`, and refuses a
descriptor that is 0, 1 or 2, a terminal, or not a pipe. Do not wrap it in a shell pipe: an agent
shell's standard output is a pipe too, which is how tokens were once printed by mistake. To test the
plumbing, run `venv/bin/python scripts/identity_capture/capture_session.py --check`. It validates the
settings and the pipe, calls neither Secrets Manager nor Cognito, and starts no browser.

### Expired and missing tokens

An access token lasts one hour. A request with an expired token is answered with 401, the code
`token_expired` and a `WWW-Authenticate: Bearer` challenge that names `invalid_token`. A request
that reaches an AgentCore client with no token is answered with 401 and the code
`sign_in_required`. Neither is a 503. After `token_expired`, sign in again and repeat the request; a
paused workflow resumes from its last saved step with the new token.

### Save the configuration, then roll back

Before the window, `python scripts/release_identity.py snapshot --service-arn ARN` reads (and
changes nothing) the Gateway, both Runtimes, the App Runner service, the site's viewer function and
response headers policy, the roles stack, the holds and catalog Lambdas and the active Cedar rules,
and writes them to `.local/release-b2/snapshot-<UTC time>.json` (mode 0600, with a SHA-256 over the
content). It holds no secret value: a plain value under a credential-looking name is replaced with
`<redacted>`, a token-shaped or access-key-shaped string with `<token>`, and the paths are listed
under `redacted`. The command exits 1 and saves nothing when a hop already reports a finding (a
release under way, or drift); `--accept-baseline` saves that state on purpose.

`python scripts/release_identity.py rollback` is a dry run: it prints, for every hop, each
difference from the snapshot with both values in full. `rollback --apply
--i-understand-this-changes-aws` restores them. Without `--snapshot FILE` it uses the newest intact snapshot
of the release that is not live (the live release is the mode of the Gateway found by name; a snapshot of the
live mode is never chosen on its own, and with none left, or when both modes' Gateways exist, it refuses and
asks for `--snapshot FILE`). It prints the time and commit of the one it uses, loudly when a newer file was
skipped. Every rollback command it prints passes `--snapshot FILE`, so a repeat run restores the same file
whichever mode is live by then. Each write is read back, up to 60 checks 5 seconds apart, until the hop equals the
snapshot. A hop whose saved copy was redacted anywhere is refused, not restored, and a failed hop
skips only the steps that need it: the Runtimes are skipped when the Gateway step failed, and the
Lambdas when the secret parameter step failed. Running it again is safe; a hop that already matches
is only read, and nothing is ever deleted. `rollback-result.json` records each step and any holds
restart still owed, so a later run does it.

Restore order, which is the true reverse of the release:

1. the site (viewer function and response headers policy), then the App Runner service
2. the roles stack (checked; a manual command is printed)
3. the Gateway and both Runtimes (see below: by API only when the Gateway was not replaced)
4. the AgentCore stack (checked; commands printed): the Cedar rules and the `InvokeGateway`
   statement on both Runtime roles
5. the secret parameter, then the holds Lambda (restarted) and the catalog Lambda

The release replaces the Gateway, so after stage 1 the Gateway the snapshot saved no longer exists.
The rollback looks up the snapshot mode's Gateway by its saved name. If it exists under the saved id,
the Gateway and Runtime steps can be restored through the API as before. If it carries another id (the
stack rebuilt it) or does not exist, the Gateway, both Runtimes and the service are reported as not
restorable here and are never written: their saved environment holds the deleted Gateway's URL, and
sending it would break the Runtimes. Their comparison ignores what a rebuild legitimately changes (the
role, the engine ARN, `AGENTCORE_GATEWAY_*_URL`, `MERIDIAN_GATEWAY_ID`, `MERIDIAN_POLICY_ENGINE_ID`).

Rolling back to `iam` after the release is the staged build in the other direction. The remedy the
rollback prints is, from a checkout of the snapshot's commit:

1. Remove the interceptor grant from the live `jwt` Gateway's role:
   `python scripts/release_identity.py gateway --to jwt --only revoke --apply --i-understand-this-changes-aws`.
2. Repeat until the render prints `Configuration complete.` (four stages: gateway, targets, governance,
   complete): `MERIDIAN_AGENTCORE_AUTH=iam python scripts/render_agentcore_config.py`, then
   `python scripts/release_identity.py deploy --to iam --apply --i-understand-this-changes-aws`. Between
   the first deploy (stage gateway) and the second (stage targets) run
   `python scripts/release_identity.py holds-logs --apply --i-understand-this-changes-aws`, which deletes
   the log group the deleted holds function left behind; the second deploy refuses until it is gone.
3. `python scripts/bind_gateway_workload.py` (the holds Lambda and its role were recreated), then
   `python scripts/sync_agentcore_env.py --write`, then `scripts/publish.py` in the saved mode so the
   backend and the service name the new Gateway.
4. `venv/bin/python scripts/release_identity.py check --expect iam --service-arn "$SERVICE_ARN"`, then
   `rollback` again; it exits 1 until every hop matches.

The `iam` Gateway that comes back has a new id and URL, a new role and a new policy engine. The stage 1
deploy of a rollback needs a complete snapshot of the `jwt` release first, taken of the live `jwt` Gateway
(`snapshot --service-arn "$SERVICE_ARN"`, with `--accept-baseline` if hops report findings), for the same
reason the release needs one of `iam`: the deletion is not reversible. That snapshot is newer than the `iam`
one, but it is a snapshot of the live release, so `rollback` does not choose it and restores the `iam` snapshot
taken before the release (or the one you name). Taking a `jwt` snapshot reads the Gateway that
`AGENTCORE_GATEWAY_URL` names, so run `sync_agentcore_env.py --write` first if `.env` is stale, and it needs the
`MERIDIAN_COGNITO_*` settings and `MERIDIAN_GATEWAY_ENFORCEMENT` from `.env` (they stay set in `iam` mode). The
mode of a snapshot comes from the Gateway, not from `MERIDIAN_AGENTCORE_AUTH`, which the `snapshot` command does
not read. The first-stage `deploy` finds the snapshot itself in `.local/release-b2/` (it looks for a complete
snapshot of the replaced release taken of that Gateway); `deploy` takes no `--snapshot`, so the printed
`deploy` commands run as printed. After the rebuild the `iam` release is live, so a repeat `rollback` needs
`--snapshot FILE`, which every printed command carries.

The jwt release removes the `InvokeGateway` statement from both Runtime roles, and the IAM render
puts it back for the new IAM Gateway. `check --expect iam` reads both roles. `snapshot` in `iam`
mode records a role that already lacks the grant as a baseline finding, so a snapshot never demands
more than the release started with.

Between steps 1 and 3 the service is back in `iam` while the Runtimes still expect `jwt`, so
signed-in traffic through the service fails until the rebuild and the publish finish. The release has
the mirror window, so this is the same size; keep the maintenance window open until the command
reports "Rollback complete".

The roles stack, the AgentCore stack and the distribution's behaviors are not changed by this
command. Each is printed with the snapshot's commit and the commands to run from a checkout (`git worktree
add`) of that commit, with the account, Region and service ARN taken from your own `.env` as shell
variables. Do not run them from the current checkout: it would deploy the new release again. After
them, run `rollback` again to re-attach the interceptor and verify; the exit code is 1 until every
hop matches. The Aurora logins and the Cognito pool work with both modes and are not rolled back.
The restore uses the service APIs and has been exercised only against recording fakes, not against
a live deployment.

### Release 2: one agent, the catalog target and loyalty_balance

Release 2 replaces the Phase 3 supervisor and its specialists with one Strands agent, moves catalog search to
a `MeridianCatalog` Gateway target served by the `meridian-catalog-tools` Lambda, and adds the governed
`loyalty_balance` tool to `MeridianHolds`. The live order:

1. Gate A, run in Release 2: [create the catalog login](#create-the-catalog-login) (migrations 022 and 023,
   the login, its live test), then create the catalog Lambda and its role with its direct-invoke smoke
   (section 2 of the [deploy runbook](AGENTCORE_DEPLOY_RUNBOOK.md#2-create-the-catalog-lambda)). Nothing
   attaches to the Lambda yet.
2. The deploy window. First save the configuration:
   `venv/bin/python scripts/release_identity.py snapshot --service-arn "$SERVICE_ARN"` (add `--accept-baseline`
   if hops report findings; see [Save the configuration, then roll back](#save-the-configuration-then-roll-back)).
   Then Deploy A, from the Part I commit's tree, adds the `MeridianCatalog` target and the
   `loyalty_balance` tool to the holds target; Cedar is unchanged, so the live Runtime keeps using
   `SemanticTripSearchLambda`. Then the interceptor Lambda update. Deploy B, from the
   final tree, changes the Cedar rules, removes `SemanticTripSearchLambda` and updates the Runtimes. Then
   publish, the smoke scripts and proofs, and last, after the soak you choose, retire the old Lambda and its
   role.

Rollback. Deploy A adds the catalog target and `loyalty_balance` and updates the holds target and function; it
removes nothing. Its additions are inert until Deploy B: the Part I Cedar rules permit no `MeridianCatalog` tool
and not `loyalty_balance`, and the policy engine is in `ENFORCE` mode, so the Gateway denies them by default.
Leaving them in place is the safe default.

To go back further, use the snapshot saved before Deploy A. It is a snapshot of the live `jwt` release, so
`rollback` does not choose it on its own; name it, as a dry run first:
`venv/bin/python scripts/release_identity.py rollback --snapshot FILE`, then the same command with
`--apply --i-understand-this-changes-aws`. It restores the Runtimes' artifact and environment, the Gateway
settings, the App Runner service, the site and the Lambda environments, and prints the commands for the roles
stack and the Cedar rules. It restores no Lambda function code (the holds, interceptor and catalog functions)
and no Gateway target.

To revert the holds function code, deploy a new commit that reverts it and keeps the catalog target. The plan
gate passes that plan as a code-only Lambda hotfix: at the complete stage it accepts a plan whose every change
is an update (`[~]`) of an `AWS::Lambda::Function` whose changed properties are only `Code` and `Metadata`. A
function that also changes `Environment` (the hold budget ceiling is there), `Role`, `Handler`, `Timeout` or
any other property, or whose property lines the plan does not show, is not a code-only update. Any other line
in the plan takes the hotfix away: an add, a removal, a Runtime, the Gateway, the engine or a rule, a role, an
IAM policy, a Lambda permission, a log group, or a Gateway target, because a target change carries a tool schema
or description change, not code. The normal expectations then apply, so the plan needs a Runtime or Cedar rule
update or the catalog target add.
Do not redeploy a tree from before the catalog target:
it removes the `MeridianCatalog` target, and the gate refuses that removal on purpose (it allows only the
retired `SemanticTripSearchLambda`).

Deploy B is undone by restoring the saved rendered config and deploying the Part I commit's tree through
`release_identity.py deploy`. The plan gate accepts that plan because it adds the retired target back and
updates the Cedar rules and the Runtimes, which a complete-stage plan may do; the old Lambda must still exist,
so retire it last.

## Prove the decoy is refused

`scripts/identity_proof.py` records, in a receipt, that a second signed-in user is refused at four layers and
that Jordan is allowed at each. The second user is the decoy: Jordan Lee, bound to `trv_demo_decoy`, with a valid
Amazon Cognito access token and a real traveler row. Run it in `jwt` mode (see
[Switch the AgentCore identity mode](#switch-the-agentcore-identity-mode)), from a clean checkout of the
released commit. In `iam` mode, the default, the command refuses to run. It signs in as both users
with tokens minted in memory from Secrets Manager, so it prints and stores no token and no password.
The plan has 21 probes. All 21 passed against the maintainers' `jwt` deployment on 2026-10-28 and
2026-10-31; receipts stay out of the repository, so run the proof against your own deployment for yours.

```bash
python scripts/identity_proof.py                                              # the plan only, no AWS call
python scripts/identity_proof.py --apply --i-understand-this-changes-aws      # the proof
python scripts/identity_proof.py --apply --i-understand-this-changes-aws --jordan-only
python scripts/identity_proof.py --render .local/identity-proof/latest.json   # rebuild the pages only
```

`--base-url` names the hosted site (the default comes from the release record) and `--output-dir` names a folder
inside `.local/`. The plan has 21 probes, in the order they run. Each row has an expectation: a probe that aims the
decoy at Jordan's data must end `refused`, and every other probe must end `allowed`. Two probes are decoy controls
(`runtime.decoy_pings_workflow` and `gateway.decoy_reads_package`): they send the decoy's own valid token with no
traveler named, so the decoy must be let in. They show that a refusal at the same layer came from the traveler check
and not from a layer that turns the decoy away for any request.

| Probe | Layer | Who | What it sends |
| --- | --- | --- | --- |
| `backend.decoy_reads_jordan_memory` | Backend | decoy | `GET /api/memory/<Jordan's traveler id>` |
| `backend.decoy_orders_for_jordan` | Backend | decoy | `POST /api/chat/order` naming Jordan's traveler id |
| `backend.decoy_me_is_decoy` | Backend | decoy | `GET /api/me`: the decoy's own identity, allowed |
| `backend.jordan_me_is_jordan` | Backend | Jordan | `GET /api/me` |
| `backend.jordan_reads_own_memory` | Backend | Jordan | `GET /api/memory/me` |
| `database.decoy_sees_jordan_rows` | Amazon Aurora | decoy | Pin the decoy, step down to `meridian_app`, count Jordan's rows |
| `database.jordan_sees_own_rows` | Amazon Aurora | Jordan | The same transaction with Jordan pinned |
| `runtime.decoy_tampers_workflow` | Runtimes | decoy | `MeridianWorkflow` start with Jordan's traveler in the payload |
| `runtime.decoy_tampers_concierge` | Runtimes | decoy | `MeridianConcierge` turn with Jordan's traveler in the payload |
| `runtime.decoy_pings_workflow` | Runtimes | decoy | `MeridianWorkflow` ping with the decoy's token, allowed |
| `runtime.jordan_pings_workflow` | Runtimes | Jordan | `MeridianWorkflow` ping |
| `runtime.jordan_runs_workflow` | Runtimes | Jordan | A review-only `MeridianWorkflow` start, its holds given back afterwards |
| `runtime.jordan_opens_concierge` | Runtimes | Jordan | A `MeridianConcierge` turn, first event only |
| `gateway.jordan_reads_package` | Gateway | Jordan | `get_package_details` for one package |
| `gateway.decoy_reads_package` | Gateway | decoy | `get_package_details` for one package with the decoy's token, allowed |
| `gateway.decoy_holds_for_jordan` | Gateway | decoy | `create_courtesy_hold` with `travelerId` set to Jordan |
| `gateway.jordan_places_hold` | Gateway | Jordan | `create_courtesy_hold`, released afterwards |
| `gateway.decoy_reads_jordans_loyalty` | Gateway | decoy | `loyalty_balance` with `travelerId` set to Jordan |
| `gateway.jordan_reads_own_loyalty` | Gateway | Jordan | `loyalty_balance` |
| `gateway.decoy_releases_jordans_hold` | Gateway | decoy | `release_hold` with `travelerId` set to Jordan, for a hold that does not exist |
| `gateway.jordan_releases_unknown_hold` | Gateway | Jordan | `release_hold` for a hold that does not exist, answered `hold_not_found` |

The database probe opens its own Data API transaction. It does not use the workload grant, so a zero count is
row-level security's answer and nothing else. No probe depends on the decoy being refused its own records at the
backend or the Workflow: the backend and Runtime probes that must end `refused` all name Jordan's traveler and are
refused by the identity check before the grant. The Gateway holds role is the one workload the decoy stays unbound
for (`tests/test_decoy_has_no_workload_binding.py`), which is what makes `gateway.decoy_holds_for_jordan` end at the
holds grant.

### Read the receipt

The run writes, under `.local/identity-proof/`: `receipt-<UTC stamp>.json` and `latest.json` (mode 0600), and the
pages `receipt-<stamp>.summary.html` and `receipt-<stamp>.full.html`. Before it signs in, it replaces `latest.json`
with a failed receipt, so an older passing receipt cannot outlive a run that dies. The receipt holds no token and no
account id (`account` is always `<acct>`); the writer refuses to write one that does. It records the commit, the
Region, the last four characters of the pool id, the site host, the mode and the cleanup result. Each outcome
records the probe, layer, actor, expectation, result, `refused_by`, a scrubbed detail, small evidence values and the
time. `design` says which Gateway enforcement shipped: `both` (the interceptor replaces `travelerId` with the token's
traveler; Cedar denies a mismatch if the interceptor is detached), `cedar` or `interceptor`. `refused_by` is one of:

| Value | Meaning |
| --- | --- |
| `backend_identity_check` | The API compared the request's traveler with the token's and refused with 403 |
| `workload_grant` | The API reached the database grant check, which has no binding for the caller's workload |
| `runtime_traveler_check` | The Runtime read the traveler from the token and the payload named another |
| `gateway_cedar` | Cedar denied a traveler argument that differed from the token's |
| `gateway_interceptor` | The interceptor refused the call |
| `gateway_workload_grant` | The interceptor replaced the traveler, the Holds Lambda ran, and its grant check refused the decoy (a new `traveler_access_audit` deny row) |
| `database_rls` | The decoy's scope saw none of Jordan's rows |

The summary has one row per layer with the decoy's result, Jordan's result and the refuser. A decoy refusal counts
only beside a passing Jordan control at the same layer, because a layer that refuses everyone proves nothing about the
decoy. Without one the decoy shows `unproven` and no refuser is named, and the receipt fails. The same holds for the
Runtimes and the Gateway when a decoy control is missing or did not pass: the receipt lists the gap "the decoy was not
let in where it should be" and the layer shows `unproven`.

The Gateway rules are strict. A refusal counts only with evidence of its layer: a new deny row for the decoy in
`traveler_access_audit` together with the Lambda's own `traveler_not_authorized` answer (the Lambda was reached and its
grant check refused; a deny row beside any other failure is an `error`), the interceptor's
`Identity Check Failed: ` text, or Cedar's `Tool Execution Denied` text or JSON-RPC code `-32002`. A 401 or 403, a 5xx, a
timeout, a validation error, a failure with none of that evidence, a refusal from a layer the shipped `design` does not
have, and evidence that contradicts itself (layer text together with a new deny row) are all `error`. There is no
fallback label for a refusal that names no layer. Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Every probe met its expectation, every layer has a decoy refusal beside a passing Jordan control, the Runtimes and the Gateway let the decoy in, nothing was left behind |
| 1 | A probe failed or errored, a layer has no probe, a leftover remains, or the run crashed or was interrupted |
| 3 | Refused to start: a guard, a usage error, a missing flag or an output folder outside `.local/` |

The guards are the mode (`jwt`), the Cognito settings, a clean `meridian/` tree, the AWS account and Region of the
deployment, and an `https` site address (or `http` on localhost). The Gateway address must be `https`, with no localhost exception.

Before the first probe, a preflight reads the tables the proof counts without a traveler scope
(`traveler_access_audit`, `journey_threads`, `hold_requests`, `workflow_snapshots` and `journey_executions`). It stops
the run unless the Aurora role sees their rows: a table with forced row-level security, or one the role neither owns nor
bypasses, would answer with zero rows and no error, and the deny-row attribution and the leftover check would pass for
the wrong reason. It must pass on the live cluster before the proof counts for anything.

Jordan's controls write a review-only Workflow run and one courtesy hold. Cleanup gives each hold back through the
Gateway release tool by exact id, as its traveler. It reads the booking back first, sends a second call only after a read-back
shows it still held, and sends at most two calls for a hold. A booking it cannot read is unknown and never counts as
released. It touches only a booking that the call's own answer or its journey reference ties to this run. Any other
booking that appears while a probe runs is reported and never touched, and it fails the receipt, as does any booking a
decoy probe makes. Then it gives back the holds of the run's threads, and every step runs even if an earlier one failed.
Nothing is deleted: the bookings, hold requests, journeys and action audit rows stay. A hold still held or of unknown
state, or a cleanup problem, fails the receipt.

Some residue stays on purpose and is listed under `notes` in the receipt. Audit rows (`traveler_access_audit` and the
agent audit log) are append-only. The Concierge probes write AgentCore Memory events for Jordan's traveler, under the
session ids the receipt names, and this command does not delete them; they expire with the memory's 30-day event expiry.

Run the proof, and the captures that use it, only with approval to change the
deployment: they sign in as the seeded users and write audit rows.

## Publish the web app

`scripts/publish.py` publishes the frontend to S3 behind CloudFront and the
backend image to an existing AWS App Runner service, using the CDK app in
`infra/bin/meridian-web.ts` (stacks `MeridianWebRoles`, `MeridianWebBackend`
and `MeridianWeb`). It needs the exact account, Region and App Runner service
ARN. Without `--apply` it builds the frontend, synthesizes the stacks and shows
their diffs; with `--apply` it deploys them. `--apply` and `--stage` change AWS, so each also
needs `--i-understand-this-changes-aws` and is refused with exit 3 without it. Abbreviated flags
(`--app` for `--apply`) are refused:

```bash
python scripts/publish.py --account <account-id> --region <region> --service-arn <app-runner-service-arn>
python scripts/publish.py --account <account-id> --region <region> --service-arn <app-runner-service-arn> --apply --i-understand-this-changes-aws
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
copies or persists credentials. After `--apply`, record the authenticated hosted
validation:

```bash
MERIDIAN_HOSTED_AUTH='{"username": "...", "password": "..."}' python scripts/warm_demo.py --hosted --verify
```

It runs the hosted warm-up (health and Amazon Aurora, catalog, traveler profile under
row-level security, one chat turn for each of phases 1 to 4, saved journeys). The four chat turns
write conversation, message, interaction and preference rows and audit rows for the seeded
traveler, which stay (see [Host the backend on Amazon ECS Express Mode](#host-the-backend-on-amazon-ecs-express-mode), "What the warm-up writes"). When every
step passes it sets `status` to `verified` in the release record and adds `verifiedAt`,
`verifiedCommit` (the Git HEAD of the tree that ran it) and `checks` (the step names). When a
step fails it sets `verification_failed` with `failedCheck` (the first failing step) and the
`checks` that passed, and exits 1. Exit codes: 0 verified, 1 a failed step or an unfinished
release record, 2 a usage error (`--verify` without `--hosted`, or with `--as` other than the default
user, because the verdict is for the default traveler). A network error part-way through is a failed step. The record keeps `image` and
`previousImage` and never holds credentials or tokens. Without `--verify` the warm-up leaves the
release record alone, but its chat turns still write the rows above. Confirm current asset hashes and image identity separately; the warm-up does not
check them.
[App Runner no longer accepts new customers](https://aws.amazon.com/apprunner/),
so this path applies to accounts that already use it. A new account hosts the backend on
Amazon ECS Express Mode: see [Host the backend on Amazon ECS Express Mode](#host-the-backend-on-amazon-ecs-express-mode).

The App Runner instance role is a workload and needs its own grant to the sample
traveler. Run this once after the `MeridianWebRoles` stack exists; without it,
Phase 4 and Phase 5 requests on the hosted site fail with
`aws_iam subject is not authorized for traveler`:

```bash
python scripts/bind_web_backend_role.py            # dry run, writes nothing
python scripts/bind_web_backend_role.py --apply
python scripts/bind_web_backend_role.py --traveler trv_demo_decoy --apply   # Jordan Lee
```

The backend and the `MeridianConcierge` Runtime call Claude Sonnet 5.5 through its
global inference profile, `global.anthropic.claude-sonnet-5-5`. Each role is granted
exactly the Amazon Bedrock models it calls. For Sonnet 5.5 that is the three-part policy
AWS documents in
[IAM policy requirements for global cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html):
the inference profile in the stack's Region, the foundation model in that Region, and the
Region-agnostic foundation model, the last two only through that profile, each for
`bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`.
`infra/lib/bedrock-model-access.ts` grants the App Runner instance role Sonnet 5.5 that
way, plus `bedrock:InvokeModel` on `cohere.embed-v4:0` and `cohere.rerank-v3-5:0` in the
stack's Region. `meridian_agentcore/agentcore/cdk/lib/model-access.ts` grants the
`MeridianConcierge` role Sonnet 5.5 the same way and the `MeridianWorkflow` role only the
two Cohere models, and removes the AgentCore construct's grant on every model. Both refuse
a chat `BEDROCK_MODEL_ID` that is not a global inference profile id.

Before a release, run `python scripts/smoke_bedrock_model.py`. It is a dry run unless
given `--apply` and `--i-understand-this-changes-aws`; with them it sends one small
Converse request for each thinking and effort setting and reports each as `USABLE`,
`ACCEPTED`, `REJECTED` or `INCONCLUSIVE`. `REQUEST_FIELDS` in
`backend/bedrock_model.py` and in the Runtime's `model/load.py` holds adaptive thinking at
effort medium, the `adaptive thinking, effort medium` candidate. Run the smoke before each
deploy and confirm that candidate is `USABLE`; change both constants only to a setting the
smoke accepted. Thinking tokens count against the 16,000-token output budget. A `BEDROCK_MODEL_ID` override still
sends these fields, so it must name a model that accepts them.

A Git push runs CI only; it does not deploy the hosted app or the AgentCore
resources.

A publish carries two additions beyond the new frontend and backend. When
`AURORA_BACKEND_SECRET_ARN` is in `.env`, the roles stack attaches
`MeridianBackendAuroraAccess` to the live App Runner instance role, and the
service receives `AURORA_BACKEND_SECRET_ARN` as an environment variable. In an
`iam` build the backend does not read that variable, so behavior is unchanged.
In a `jwt` build the stack sets the container's `AURORA_SECRET_ARN` from it
(and refuses a value equal to `AURORA_SECRET_ARN`), so the backend connects as
`meridian_backend`. State both in the release note, because "no live change"
holds for a Git push but not for a publish.

`scripts/validate_demo.py` runs the full sample contract (catalog, phases, holds,
confirmation and cleanup) against a backend. It uses real services. Its cleanup gives back
its own active holds through the Gateway, keeps every row and names the confirmed booking it
leaves behind (`--delete-from-disposable-database` deletes the rows instead). The release needs the
Gateway configuration (`sync_agentcore_env.py --write`), master Aurora access for the read-backs and, in
`jwt` mode, the seeded user's Cognito secret, because the Gateway call carries that user's token. Without
them a run whose checks passed still exits 1 and prints "no governed release possible" for each
hold, and the holds stay held until they expire or you run `release_demo_bookings.py`. Against a hosted backend it needs
`--allow-hosted-demo-writes`, and `MERIDIAN_HOSTED_AUTH` must supply the site's
basic-auth credentials at run time. Resolve that value from Secrets Manager
when you run the command; do not store it in a file.

To tear down an App Runner hosted app (for Express Mode, use
[Tear down the Express backend](#tear-down-the-express-backend) instead), run
`npx cdk destroy MeridianWeb --exclusively` from `infra/`,
delete the App Runner service, then run
`npx cdk destroy MeridianWebBackend MeridianWebRoles --exclusively`. Delete the
`meridian/web/api-token` secret if you created one. The backend images CDK pushed stay in the
bootstrap stack's Amazon ECR repository; delete the `CDKToolkit` stack only if nothing else in the
account and Region deploys with CDK, and empty its S3 bucket and ECR repository first if the delete
stops on them.

The container starts through `backend/launch.py`, which opens the port before
the application finishes loading; [AGENTCORE_LEARNINGS.md](AGENTCORE_LEARNINGS.md)
explains why App Runner needs this.

## Bring the App Runner service under CloudFormation

The hosted backend's App Runner service (`meridian-web`) was created outside CloudFormation.
The `MeridianWebService` stack (`infra/lib/meridian-web-service-stack.ts`) describes it as an
`AWS::AppRunner::Service` with `DeletionPolicy` and `UpdateReplacePolicy` `Retain`, so it can be
rebuilt from code. `AWS::AppRunner::Service` supports resource import, drift detection and the
IaC generator
([resource type support](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import-supported-resources.html)).
App Runner no longer accepts new customers, so this rebuilds the service only in an account that
already uses it.

The import is a one-time change to AWS. Do it only with the account owner's approval for each
step. An import creates, updates and deletes nothing: CloudFormation records the existing service
in the stack and does not call `UpdateService`. The rules
([import considerations](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/import-resources-manually.html)):
the imported resource needs a `DeletionPolicy`; CloudFormation does not check that the template
matches the live service, so step 3 does; the import change set cannot add, change or remove any
other resource.

The stack exists only when the synth gets `-c instanceRoleArn=...`, and it then requires
`-c accessRoleArn=...` and `-c serviceImageUri=...` (and `-c apiTokenSecretArn=...` in the `iam`
release). There is no default image: the synth fails without one, so no deploy can pick an image by
accident. Every other publish is unchanged. Keep the values in `.local/` (git-ignored), never in a
tracked file:

- `.local/apprunner-import.env`: `instanceRoleArn` and `accessRoleArn` (the `InstanceRoleArn` and
  `AccessRoleArn` outputs of `MeridianWebRoles`, equal to the live service's roles),
  `serviceImageUri` (the live `ImageIdentifier`), and for the `iam` release `apiTokenSecretArn`.
- `.local/apprunner-import-mapping.json`:
  `{"<logical id>": {"ServiceArn": "<service arn>"}}`. The logical id is `BackendService` plus a
  hash; read it from the synthesized template.

1. **Read the live service** (read-only): `aws apprunner describe-service --service-arn ...`. Copy
   the image identifier into `serviceImageUri`. Pass the live image, not the one this checkout
   would build: a different image in the template would deploy on the first update.
2. **Synthesize** from `infra/`, with the same `.env`, Region and release mode as the live service:
   `npx cdk synth MeridianWebService -c instanceRoleArn=... -c accessRoleArn=...
   -c serviceImageUri=...` (add `-c apiTokenSecretArn=...` in the `iam` release).
3. **Compare field by field** (read-only `DescribeService` and `ListTagsForResource`):
   `python scripts/check_apprunner_import.py --region <region> --service-arn <arn>
   --image-uri <serviceImageUri>`. It must print `MATCH` and exit 0. It refuses a template older
   than 30 minutes or synthesized for another image. It checks name, image, roles, port, start
   command, every environment variable and secret reference (a duplicate name is refused), size,
   health check, network, observability, encryption and tags, and prints field paths only. Any
   difference means the template is wrong: fix the code or the context, never the service. The
   auto scaling configuration is an accepted match: the template names none, and the script
   requires the live one to be the account's `DefaultConfiguration` revision 1.
4. **Import** from `infra/`: `npx cdk import MeridianWebService --resource-mapping
   ../.local/apprunner-import-mapping.json` plus the same `-c` values. The prompt must list the
   service and nothing else. The `AWS::CDK::Metadata` resource is not a resource to import; if the
   prompt or the change set lists anything besides `AWS::AppRunner::Service`, answer no.
5. **Check for drift**: `aws cloudformation detect-stack-drift --stack-name MeridianWebService`,
   then `describe-stack-resource-drifts`. Expect `IN_SYNC`. Then run `cdk diff MeridianWebService`
   with the same context: expect no differences. A difference must not be deployed until it is
   understood.
6. **Confirm the service did not change**: `describe-service` shows the same `UpdatedAt`, the
   status is `RUNNING`, and no new operation appears in `list-operations`. Run
   `python scripts/published.py`.

**Variables nothing reads.** The live service carries three variables that no file in the
repository reads: `AGENTCORE_GATEWAY_SEARCH_TOOL` and two checkpoint settings with the
`LANGGRAPH_` prefix. `LEGACY_LIVE_ENVIRONMENT` in the stack file names them; the framework lives
only in `examples/langgraph/`. They reached the service through
`publish.py`, which keeps the live variables under the new ones. The stack lists them in
`LEGACY_LIVE_ENVIRONMENT` so the import is an exact match. Remove them later, never during the
import: empty that object in a reviewed change, run the check, read `cdk diff`, and deploy
`MeridianWebService` once, with the live image and approval. That is an `UpdateService` and a
deployment. Confirm first that the image the service runs does not read them: the repository
shows no reader, but the running image is not this checkout.

**Updates after the import.** `publish.py` remains the update path until a reviewed follow-up moves
its `update_service` step to the stack. It changes the image and environment through the API, so
the stack drifts after every publish. After any `publish.py --apply`, run drift detection on
`MeridianWebService` and read the result. Do not run `cdk deploy MeridianWebService` or
`cdk deploy --all` with a `serviceImageUri` other than the one the service runs now: it is an
`UpdateService` with that image. To bring the stack back in line after a publish, set
`serviceImageUri` to the image `describe-service` shows and run `cdk diff` first.

**Rollback.** Both policies are `Retain`, so removing the resource from the stack never touches the
service. If step 4 or 5 goes wrong, `aws cloudformation delete-stack --stack-name
MeridianWebService` after the import has finished (or after `IMPORT_ROLLBACK_COMPLETE`) leaves the
service running as it was; run it only with approval. A failed import rolls back to
`IMPORT_ROLLBACK_COMPLETE` and leaves the service unmanaged, as before. Retain also means deleting
the stack does not delete the service: to remove it, delete it in App Runner by hand.

## Host the backend on Amazon ECS Express Mode

App Runner no longer accepts new customers, so an account that never used it hosts the backend on
[Amazon ECS Express Mode](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-getting-started.html).
The backend is one stateless container. Express Mode runs it on AWS Fargate behind an Application
Load Balancer it creates and manages, and CloudFront routes `/api/*` and `/health` to it, as it
does for App Runner.

This is a separate path. `scripts/publish.py` and the App Runner stacks are unchanged and remain the
default: the Express stacks exist only when the CDK app gets `-c backendHost=express`, and no
other stack's template changes with or without it (`infra/tests/express.test.cjs` proves that).
`publish.py` does not manage the Express path; use the commands below.

**What the switch adds.** `-c backendHost=express` adds two stacks and points the CloudFront
origin of `MeridianWeb` at the Express service:

| Stack | Holds |
| --- | --- |
| `MeridianWebExpressRoles` | The task role (the same grants as the App Runner instance role, less the separate master-login and shared-token grants. Its one secret grant is the container's own Aurora login: the `meridian_backend` secret in the `jwt` release, and the master login in the `iam` release, where the container connects as the master), the task execution role and the infrastructure role Express Mode requires. Kept apart so IAM settles before the service exists, as `MeridianWebRoles` is for App Runner |
| `MeridianWebExpress` | The `AWS::ECS::ExpressGatewayService` `meridian-web-express` and its CloudWatch log group |
| `MeridianWeb` | As before, with the API origin set to the service's host (`ServiceHost`, the `Endpoint` URL without `https://`). It is an error to also set `MERIDIAN_BACKEND_HOST`, which names an App Runner host |

The service runs the same image as the App Runner path (both build `meridian/Dockerfile` for
linux/amd64 with the same asset properties, so the tag is the same) with the same environment and
secret references. `LEGACY_LIVE_ENVIRONMENT` is App Runner only and is not carried over. It is pinned
to 1 vCPU and 2 GB, container port 8000, health check path `/health` (the backend's public
liveness route), 1 to 4 tasks scaling on 60 percent average CPU, and a log group kept 30 days.

**Prerequisites.** Everything the App Runner path needs, except the App Runner service: the
Aurora cluster, the AgentCore Runtimes, the identity stack (`jwt` release), a complete
`meridian/.env`, a bootstrapped CDK environment and a container engine. Also, in the target Region:

- A default VPC with at least two public subnets in two Availability Zones, each with at least 8
  free IP addresses. Express Mode uses them for an internet-facing load balancer and for the tasks,
  which get public IP addresses. This is the simplest correct choice for a new account, which has
  a default VPC unless someone deleted it; the stacks set no subnets or security groups. An
  account without one must create a default VPC first, or the stack needs explicit subnets, which
  it does not support today. Private subnets would make the load balancer internal, and CloudFront
  could not reach it.
- No `MERIDIAN_BACKEND_HOST` in the shell or `.env`.

**Deploy order.** Run from `infra/`. Read the account and Region from the credentials you use,
and set the Region the rest of the release uses:

```bash
export MERIDIAN_WEB_REGION=<region> MERIDIAN_AGENTCORE_AUTH=jwt
export MERIDIAN_COGNITO_HOSTED_UI_DOMAIN=<HostedUiDomain output of MeridianIdentity>
unset MERIDIAN_BACKEND_HOST
npm ci && npm run build

# 1. Build the signed-in site. The values are the MeridianIdentity outputs; keep them out of files.
(cd ../frontend && npm ci && VITE_API_BASE_URL= VITE_API_ORIGIN= VITE_REQUIRE_SIGN_IN=1 \
  VITE_COGNITO_DOMAIN=<HostedUiDomain> VITE_COGNITO_CLIENT_ID=<AppClientId> npm run build)

# 2. Read what would be created. No AWS resource changes.
npx cdk diff MeridianWebExpressRoles MeridianWebExpress -c backendHost=express --exclusively

# 3. The roles. Wait a minute for IAM to settle before the service.
npx cdk deploy MeridianWebExpressRoles -c backendHost=express --exclusively
sleep 60

# 4. The image and the service. Express Mode creates the load balancer, certificate and
#    security groups, so this takes several minutes.
npx cdk deploy MeridianWebExpress -c backendHost=express --exclusively \
  --outputs-file ../.local/MeridianWebExpress-outputs.json
```

Bind the task role to each seeded traveler, the same step as for App Runner, against the Express
roles stack. Both releases need it: before the backend sets a traveler scope, Aurora checks its role
in `traveler_identity_bindings`, and an unbound role gets 403 on every traveler route (the first
trial deploy, 15 Nov 2026, showed exactly that):
`python scripts/bind_web_backend_role.py --stack MeridianWebExpressRoles --apply`, then again with
`--traveler trv_demo_decoy`. In the `iam` release also add
`-c apiTokenSecretArn=<ARN of meridian/web/api-token>` to every command above.

**Validate before any traffic moves.** The service answers at its own HTTPS endpoint, the
`ServiceEndpoint` output, a URL such as `https://<name>.ecs.<region>.on.aws` (the `ServiceHost`
output is the same without the scheme; the CloudFront origin uses that). In the `jwt` release that
endpoint refuses callers without a token, so a direct probe is safe. Run the warm-up against it with
the credential its release needs ([Run the smoke scripts as a seeded user](#run-the-smoke-scripts-as-a-seeded-user)):

```bash
cd ..    # meridian/
# jwt release: the script signs in as a seeded user and sends that access token.
MERIDIAN_AGENTCORE_AUTH=jwt python scripts/warm_demo.py --base-url <ServiceEndpoint>
# iam release: the direct endpoint wants the shared API token as a bearer. Resolve it from
# Secrets Manager for this one command; never put it in a file or an argument.
MERIDIAN_API_TOKEN="$(aws secretsmanager get-secret-value --secret-id meridian/web/api-token \
  --query SecretString --output text)" python scripts/warm_demo.py --base-url <ServiceEndpoint>
```

It must end with `Warm.`, with every step `ok`. Without the credential the `iam` call is refused
with HTTP 401 at the first step, the health check, and the run stops there. That is a missing
credential, not a sick service.

**What the warm-up writes.** It is not a health probe. The health step and the catalog, profile and
journey reads only read, and a health probe on its own (`/health` for the load balancer, `/api/health`
for the Aurora check) never writes. The four chat turns, phases 1 to 4, do write for the seeded
traveler: conversation and message rows, trip interactions and saved preferences, and the access
checks add append-only audit rows (`traveler_access_audit` and the agent audit log). A Phase 4 turn
also commits its audit row and then stores its memory in separate short transactions. The warm-up
never places a hold, confirms a booking or starts a recovery workflow, and nothing it writes is
deleted afterwards. Add `--conversation-ids-file <file>` to record each turn's conversation id.
Expect these rows after every warm-up, `--verify` or not.

If the service is not healthy, read the log group `/aws/ecs/meridian-web-express` and the service's
events in the Amazon ECS console. The task starts slowly (see the gaps below); allow up to five
minutes after the stack reports success before treating a failed health step as a failure.

**Cut CloudFront over.** From `infra/`, with the same environment variables:

```bash
npx cdk diff MeridianWeb -c backendHost=express --exclusively
npx cdk deploy MeridianWeb -c backendHost=express --exclusively \
  --outputs-file ../.local/MeridianWeb-outputs.json
```

The diff must show only the API origin domain (and the stack description) changing when the account
already has `MeridianWeb`; in a new account it is the first deploy of the site. CloudFront finishes
the update in a few minutes. Requests in flight on the old origin complete there. The backend is
stateless, so no data moves.

`publish.py` writes the release record that `warm_demo.py --hosted` reads. For this path, write it
from the outputs. In an account that already has a record from an App Runner release, copy
`.local/hosted-release.json` aside first, because this replaces it:

```bash
cd ..    # meridian/
jq '{status: "deployed_pending_verification", site: .MeridianWeb, backend: "ecs-express"}' \
  .local/MeridianWeb-outputs.json > .local/hosted-release.json && chmod 600 .local/hosted-release.json
python scripts/warm_demo.py --hosted --verify
```

The record is local and git-ignored. `--verify` then adds `verified` and the checks, as in
[Publish the web app](#publish-the-web-app). In the `jwt` release the script signs in as the
seeded user; in the `iam` release set `MERIDIAN_HOSTED_AUTH` to the site's credentials for the call,
as there.

**First-time hosting: the callback and the edge credentials.** The CloudFront site URL, the
`SiteUrl` output of `MeridianWeb`, exists only after the cutover, so what depends on it
comes after the cutover and the release record below:

| Release | After `MeridianWeb` is deployed |
| --- | --- |
| `jwt` | Register the site's HTTPS return address with the Cognito app client: redeploy the identity stack with `-c callbackUrls=http://localhost:5173/showcase,<SiteUrl>/showcase` (the command is in [Sign-in and who is calling](#sign-in-and-who-is-calling)). Until you do, the hosted sign-in flow ends in `redirect_mismatch`. There is no edge credential to provision: the `jwt` viewer function holds none. |
| `iam` | No Cognito callback applies. The stack creates the KeyValueStore `meridian-web-access` (its ARN is the `AccessStoreArn` output) empty, and the viewer function reads two keys from it: `basic`, the base64 of `user:password` for the presenter's Basic credential, and `token`, the shared API token it injects as the bearer for API paths. Add both before the first visit, with the token taken from the `meridian/web/api-token` secret. The repository has no script for this, so use `aws cloudfront-keyvaluestore put-key` and keep both values out of files. |

Then test as a stranger would, in a fresh browser session with no saved cookies or tokens (a
private window): open the `SiteUrl`, sign in (`jwt`) or answer the Basic prompt (`iam`), and load
the showcase. Check the hosted site with the same script: `python scripts/warm_demo.py --hosted`
(`jwt`, as a seeded user) or with `MERIDIAN_HOSTED_AUTH` set to the Basic credential (`iam`). This
fresh-session check has not been run for the Express path in a disposable account.

**Update the service.** Rebuild with the same switch and deploy `MeridianWebExpress`. A new image
tag, or changed environment, becomes a new service revision, which Express Mode rolls out with a
canary deployment and rolls back on 5XX alarms. Deploy `MeridianWebExpressRoles` first when
`meridian/.env` changes an ARN the task role grants.

**Roll back.** Deploy `MeridianWeb` again without the switch, with the App Runner host, then restore the
release record you copied aside:

```bash
cd infra
MERIDIAN_BACKEND_HOST=<App Runner service URL, host only> \
MERIDIAN_COGNITO_HOSTED_UI_DOMAIN=<HostedUiDomain output of MeridianIdentity> \
npx cdk deploy MeridianWeb --exclusively
```

Keep the hosted UI domain set: `MeridianWeb` builds its Content-Security-Policy from it, so a
rollback without it would drop the Cognito host from the policy and break sign-in.

CloudFront returns to the App Runner service within minutes; App Runner was never changed. To
remove the Express path after a rollback to App Runner, first switch `MeridianWeb` away from it
(CloudFormation refuses to delete an export `MeridianWeb` still imports), then run
`npx cdk destroy MeridianWebExpress MeridianWebExpressRoles -c backendHost=express --exclusively`.
Keep `--exclusively`: without it the CDK CLI also destroys every stack that depends on the ones you
name, and `MeridianWeb` depends on the Express stacks, so the command would delete the live site
(this happened on 15 November). Deleting the
stack deletes the service, and Express Mode deletes the load balancer, security groups and
certificate it created when no other Express service shares them. The log group is removed with the
stack. The image stays in the CDK assets repository.

### Tear down the Express backend

Use this when Express Mode is the only host you created. There is no App Runner service to roll
back to, so the App Runner teardown in [Publish the web app](#publish-the-web-app) does not
apply: it would try to destroy stacks you never deployed. One task and one Application Load
Balancer run all the time and bill by the hour, so do not leave this half done.

The order follows the stack definitions. `MeridianWeb` takes the service's host from
`MeridianWebExpress`, which takes its roles from `MeridianWebExpressRoles`, and CloudFormation will
not delete an export another stack still imports. So the consumer goes first and the roles last.
Run from `infra/`, in the same shell values you deployed with: `MERIDIAN_WEB_REGION`,
`MERIDIAN_AGENTCORE_AUTH`, `MERIDIAN_COGNITO_HOSTED_UI_DOMAIN`, no `MERIDIAN_BACKEND_HOST`, and in
the `iam` release `-c apiTokenSecretArn=<ARN of meridian/web/api-token>` on every command. The
Express stacks exist in the CDK app only with `-c backendHost=express`, and every command keeps
`--exclusively`:

```bash
npx cdk destroy MeridianWeb -c backendHost=express --exclusively
npx cdk destroy MeridianWebExpress -c backendHost=express --exclusively
npx cdk destroy MeridianWebExpressRoles -c backendHost=express --exclusively
```

1. `MeridianWeb` is the site that consumes the service. It removes the CloudFront distribution and
   the site bucket (its objects are deleted with it). CloudFront takes several minutes.
2. `MeridianWebExpress` deletes the Express service, and Express Mode deletes the load balancer,
   security groups and certificate it created when no other Express service shares them. Its log
   group `/aws/ecs/meridian-web-express` goes with the stack.
3. `MeridianWebExpressRoles` deletes the task, execution and infrastructure roles last, because the
   service stack imports them.

Then verify, because a failed or interrupted delete leaves the task and the load balancer billing:

```bash
aws ecs list-services --cluster default --region <region>
aws elbv2 describe-load-balancers --region <region> --query 'LoadBalancers[].[LoadBalancerName,CreatedTime]'
```

`meridian-web-express` must not be listed, and no load balancer that Express Mode created for it
may remain (it is named by Express Mode, so match its creation time and tags in the Amazon EC2
console). If a stack stays in `DELETE_FAILED`, read its events and fix the cause before you
destroy the roles. What stays: the image in the CDK assets repository, the `meridian/web/api-token`
secret in the `iam` release, and the `MeridianBackendAuroraAccess` policy, which you delete only
after the roles stack is gone, in
[Roll back the service logins and sign-in](#roll-back-the-service-logins-and-sign-in).

This order has not been rehearsed in a disposable account. It comes from reading
`infra/lib/meridian-web-app.ts`, `meridian-web-express-stack.ts` and
`meridian-web-express-roles-stack.ts`.

**What Express Mode cannot set, compared with App Runner.** These are limits of the
`AWS::ECS::ExpressGatewayService` resource at the pinned `aws-cdk-lib`, not omissions:

| App Runner setting | On Express Mode |
| --- | --- |
| TCP health check, 5 second interval, 1 healthy check to pass | An HTTP check of `/health` on the target group: 30 second interval, 5 healthy checks to pass, 2 failures to remove. Only the path is settable. A new task takes about 2.5 minutes to take traffic, after a 300 second grace period for slow starts |
| CPU architecture | Not a CloudFormation property. Express Mode defaults to X86_64, which matches the linux/amd64 image. Confirm with the task definition's `runtimePlatform` after the first deploy |
| Default VPC and a managed network | The same default, but the account needs the default VPC described above; subnets and security groups are not set by these stacks |
| Auto scaling configuration with request concurrency | Scales on average CPU (60 percent, 1 to 4 tasks). Request count per target is available through the Express API, not set here |
| Provisioned instance with pause | No pause and no scale to zero. One task and one Application Load Balancer run all the time, so an idle service costs more than App Runner |
| 120 second request limit | None. The load balancer idle timeout is 60 seconds, and the backend sends a keepalive every 10 seconds on streams, so streams stay open. The browser's 110 second ceiling is unchanged |
| Cluster, platform version, deployment strategy | The `default` cluster is created if absent, Fargate platform version LATEST, canary deployments. None is set by these stacks |
| Public endpoint | Also public, as App Runner's was: the endpoint can be called without CloudFront. In the `jwt` release the API routes need a verified token and only `/health` answers without one. Attach AWS WAF to the load balancer to restrict it further |

Work that outlives a request (the turn-summary audit row, a Memory unit) has the same guarantee as
on App Runner: none. A deploy or scale-in stops tasks after a 30 second `SIGTERM` window.

## Recover in another Region

This is a plan, not a feature. **The repository implements nothing cross-Region.** Every resource it
deploys (the Aurora cluster, the audit export bucket and key, the Cognito pool, App Runner, the
Lambdas, the AgentCore Runtimes, Gateway, Memory and Cedar policy engine, and the secrets) lives in
one Region, and the publish and deploy tools pin that Region. What follows is what an operator would
build and rehearse to recover the application in a second Region, and what they would find missing.
Nothing here has been run. Numbers are AWS's published figures with a link; they describe the
services, not a measurement of this application.

### What lives where

| State | Where it lives | Survives a Region loss only if |
| --- | --- | --- |
| Travelers, preferences, conversations, bookings, holds, journeys, snapshots, identity bindings, workload grants, audit tables | Aurora | the cluster is in a global database, or restored from a copied snapshot or backup |
| Logins, their passwords, row policies and functions | Aurora (roles and grants are database objects) | same as above |
| Login secrets, the master secret | Secrets Manager, per Region | the secrets are replicated to the second Region |
| Audit export objects and the hash chain | S3 bucket with Object Lock, and its KMS key, per Region | the bucket is replicated and the replica's key exists |
| Sign-in: users, pool, app client | Amazon Cognito user pool, per Region | the pool uses multi-Region replication |
| Runtimes, Gateway and its targets, Cedar policy engine | AgentCore, per Region | they are deployed again from the repository in the second Region |
| Memory (session and semantic context) | AgentCore Memory, per Region | nothing preserves it by default; treat it as lost |
| The site and the API host | S3 and CloudFront (global), App Runner (per Region) | the backend is deployed in the second Region and CloudFront is pointed at it |

Aurora holds every record the application reads back. The Memory store is a second copy of a
conversation (the conversation text itself is in `conversation_messages`), so losing it degrades what
the Concierge recalls and does not lose a record. The deploy artifacts (code, templates, migrations)
are in Git.

### Aurora

Use an Aurora global database
([overview and failover paths](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html)).

- **What AWS documents.** Replication is asynchronous from the primary to read-only secondary
  clusters. RTO for Aurora Global Database "can be in the order of minutes". RPO is "typically
  measured in seconds" and depends on replication lag when the failure happens. On Aurora
  PostgreSQL, `rds.global_db_rpo` sets and tracks an upper bound on RPO, and AWS warns that it might
  slow commits on the primary's writer. AWS Database Blog posts state tighter figures (RPO under 5
  seconds, RTO under one minute); use the documentation's wording for planning.
- **Switchover** (planned): the previous term was "managed planned failover". It synchronizes the
  secondary with the primary first, so RPO is 0, and the database is unavailable for a short time
  while the two clusters swap roles. Use it for a planned move or a rehearsal, and to move back
  after a failover
  ([AWS Resilience Hub and ARC block](https://docs.aws.amazon.com/r53recovery/latest/dg/aurora-global-database-block.html),
  [switchover-global-cluster](https://docs.aws.amazon.com/cli/v1/reference/rds/switchover-global-cluster.html)).
- **Failover** (unplanned): detach the chosen secondary from the global database. That stops
  replication at once and promotes it to a standalone cluster with a writer. Writes that had not
  replicated are lost, and the old primary must not be re-attached as it was, to avoid split-brain.
  After the old Region returns, build a new secondary from the promoted cluster
  ([manual failover steps](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html)).
  Choose the secondary with the least lag when there is more than one.
- **The Data API on a secondary.** Data API can be enabled on both clusters, but a secondary has
  no writer until it is promoted, and Data API needs the writer for every query, reads included, so
  every application call fails against a secondary
  ([Data API limits](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.limitations.html)).
  The application cannot read from the secondary Region before promotion. Enable the Data API on
  the secondary cluster before an incident, and use the promoted cluster's ARN afterwards.
- **What the repository does today that blocks this.** `infra/lib/meridian-aurora-stack.ts` creates
  the cluster with `manageMasterUserPassword: true`, and AWS documents that an RDS-managed master
  secret is not supported for a cluster that is part of a global database
  ([limitations](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-secrets-manager.html)).
  The master password would have to become self-managed (and stored in a replicated secret) before the
  cluster joins one. The cluster is encrypted with `alias/aws/rds`; the secondary needs a KMS key in its
  own Region. Check the engine version (18.6) and Serverless v2 against the
  [supported Regions and engines](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.configuration.requirements.html)
  before planning.
- **Secondary sizing.** A secondary with no readers costs storage only ("headless") and a longer
  recovery, because instances must be created after promotion. A secondary with a reader recovers
  sooner.
- **Rotation.** A password rotation (see
  [Rotate workload passwords automatically](#rotate-workload-passwords-automatically)) changes roles
  in the primary and reaches the secondary through replication, and the rotating secret replicates
  too (below), so the pair stays usable after a failover.

### Secrets Manager

Replicate each secret the workloads read to the second Region (`ReplicaRegions`, or
`replicate-secret-to-regions`). The replica has the same ARN apart from the Region. When rotation is
on for the primary secret, Secrets Manager rotates it in the primary Region and propagates the new
value to the replicas. A replica can be promoted to a standalone secret
([replication](https://docs.aws.amazon.com/secretsmanager/latest/userguide/replicate-secrets.html)).

What an operator must still do: every `AURORA_*_SECRET_ARN` that the tools and stacks read from
`meridian/.env` names the primary Region, and the web stack checks that the backend secret ARN is in
the deploy Region. A second Region needs its own `.env` with the replica ARNs, and each login's
managed policy (`scripts/provision_service_logins.py`) must name the replica. The secrets hold the
cluster-independent credentials, so they work against the promoted cluster; the cluster ARN is what
changes.

### Audit export bucket

The bucket has Object Lock, versioning, SSE-KMS with a single-Region customer-managed key, and a
policy that denies deletes and unconditional writes
([Audit export](#audit-export)). To keep the evidence in the second Region, replicate it with S3
Replication.

- Both buckets need Object Lock and versioning. S3 replicates the objects with their retention, and
  the replica takes the source's retention, overriding the destination bucket's default
  ([Object Lock and replication](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock-managing.html),
  [what is replicated](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-what-is-isnot-replicated.html)).
  The replication role needs `s3:GetObjectRetention` and `s3:GetObjectLegalHold` on the source in
  addition to the usual permissions. Put the same governance or compliance mode on the destination
  bucket, because a replica is only as protected as its own bucket policy.
- The objects are SSE-KMS, so the replication rule must opt in to replicate encrypted objects and name
  a KMS key in the destination Region. The bucket's key is not a multi-Region key.
- **Lag.** Replication is asynchronous. With S3 Replication Time Control, S3 replicates 99.99 percent
  of new objects within 15 minutes, backed by an SLA
  ([features](https://aws.amazon.com/s3/features/replication/)). Without it, most objects replicate
  within 15 minutes and some take longer
  ([knowledge center](https://repost.aws/knowledge-center/s3-crr-replication-time)). The exporter
  writes an object every five minutes at most, so the replica can trail by several objects.
- **Interaction with the hash chain.** The chain's state is only the bucket: the latest object names
  the last exported row and gives the hash the next object carries. If the destination holds the
  chain up to sequence N, an exporter started in the second Region against the destination
  continues at N+1 from the promoted Aurora's rows after the watermark, which is correct if the
  promoted cluster has those rows. Two gaps remain. Rows exported to the source but not yet
  replicated by S3 are re-exported with the same sequence numbers the lost source objects had, so the
  chain stays valid but its bytes differ from the lost source objects. Rows that Aurora did not
  replicate before the failure are in the source bucket (if it survives) but not in the promoted
  cluster, so a verifier that compares the bucket with Aurora reports them. Record chain heads
  outside both buckets, as the verifier expects, and keep the heads from before the failure.
- **Object Lock cost.** Replicas keep their retention, so a failover that leaves two buckets doubles
  the locked storage until the dates pass. Under `COMPLIANCE` the replica cannot be deleted early
  either.
- The destination bucket's policy must deny deletes the same way. The `If-None-Match` condition in
  the source policy applies to `s3:PutObject`, which the exporter uses; check that the replication
  role's `s3:ReplicateObject` writes are not blocked by the destination's copy of that policy before
  relying on the replica.
- The exporter Lambda, its schedule and its alarms are deployed again in the second Region by the
  audit export stack, pointed at the replica bucket (`AUDIT_EXPORT_BUCKET`) and the promoted cluster.
  The stack as written creates a new bucket and key, so reusing the replica needs a change to it.

### Amazon Cognito

A user pool is Regional. A new pool in the second Region has different user pool and app client IDs,
a different issuer and different `sub` values, so every user would sign up again and every row in
`traveler_identity_bindings` (keyed by `subject_id`) would need replacing. The practical path is
multi-Region replication for user pools: a replica pool with the same user pool ID, replicated user
profiles, credentials, app clients and configuration, one-way from the primary, and access and
refresh tokens accepted in both Regions
([user guide](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-multi-region.html),
[AWS Security Blog](https://aws.amazon.com/blogs/security/architecting-resilient-authentication-with-amazon-cognito-multi-region-replication/)).

- It needs the Essentials or Plus feature plan (the identity stack uses Essentials) and a user pool
  encrypted with a **multi-Region customer managed KMS key**, which is a change to the existing pool.
  It has its own cost.
- The replica is read-only for configuration and user attributes. Sign-in, token issue and
  revocation work; sign-up and profile updates do not until the primary returns. Replication is
  eventually consistent.
- Lambda triggers are configured per replica. The pre-token-generation Lambda
  (`infra/functions/pre_token_generation`) must be deployed in the second Region, reading Aurora
  through the Data API with the identity login's replica secret, and attached to the replica pool.
  Without it, the access token lacks the `traveler_id` claim and the Runtimes and Gateway refuse
  the caller.
- The Gateway, both Runtimes and the backend verify tokens against the pool's issuer. With a
  replica, check which issuer the tokens carry (the documentation recommends configuring the
  updated issuer for consistent validation) and that the second Region's Gateway authorizer accepts
  it. This is the part of the plan with the most unknowns for this application.

### Amazon Bedrock AgentCore

AgentCore Runtime, Gateway, Memory and Policy are Regional, and across Regions "you own the
deployment, the health detection, the routing, and the failover decision"
([AgentCore disaster recovery](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/disaster-recovery-resiliency.html)).
That page lists for each component what the customer owns. For Meridian:

| Component | In the second Region |
| --- | --- |
| Runtimes `MeridianConcierge` and `MeridianWorkflow` | Deploy from `meridian_agentcore` to the second Region (the CLI target names the Region). New Runtime ARNs and endpoints. Check the Runtime's quotas and set the platform version explicitly, as the [AgentCore deploy runbook](AGENTCORE_DEPLOY_RUNBOOK.md) describes |
| Gateway `meridian-aurora`, targets `MeridianHolds` and `MeridianCatalog`, inbound authorizer | Deploy again. A new Gateway has a new ID and URL. The Lambda targets, their roles, and the SSM parameters that carry the cluster and secret ARNs are Regional too |
| Cedar policy engine `MeridianGovernance` | Create an equivalent engine and attach it to the new Gateway in `ENFORCE` mode. The repository renders the policies from a template, so this is a deploy, and `scripts/verify_agentcore.py` checks the result |
| Memory `meridian_session` | Create it again. It starts empty. AgentCore documents Memory replication as the customer's job, not a feature; export what you need or accept a cold start |
| Workload grants | They name IAM principals and live in Aurora. New Runtime roles in the second Region need new bindings with the `bind_*` scripts |
| Observability | Set up log delivery, Transaction Search and alarms in the second Region |

Cross-Region inference for the model (`global.anthropic.claude-sonnet-5-5`) is not disaster recovery:
it changes where an inference request runs, not where data is stored
([same page](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/disaster-recovery-resiliency.html)).
Confirm that every AgentCore component the project uses is available in both Regions of the pair
before choosing them.

### CloudFront and the backend host

CloudFront is a global service, but its origins are not. The site is a Vite build on S3 and the API is
the FastAPI backend on App Runner, both in the primary Region (`MERIDIAN_WEB_REGION`).

- The site can be rebuilt and published from Git into a bucket in the second Region, or the bucket
  replicated.
- The backend is deployed again in the second Region (`MeridianWebRoles`, `MeridianWebBackend`,
  `MeridianWebService`) with the second Region's Runtime ARNs, Gateway URL, Cognito settings and
  login secret ARN.
- CloudFront origin groups fail over only for GET, HEAD and OPTIONS requests
  ([origin groups](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistS3AndCustomOrigins.html),
  [networking blog](https://aws.amazon.com/blogs/networking-and-content-delivery/building-multi-region-active-active-architectures-with-cloudfront-vpc-origins-and-advanced-routing/)).
  The API takes POST requests, so an origin group alone does not move it. Change the API origin's
  domain, or put a Route 53 failover record in front of it, as part of the runbook.
- The Cognito app client's callback and logout URLs name the CloudFront domain. They stay valid when
  the distribution does not change.

### Order of a recovery

1. Decide it is a Region event and stop writes to the old primary (a failover has no clean stop if
   the Region is gone).
2. Aurora: detach and promote the secondary, or switch over if both Regions are healthy. Enable the
   Data API on the promoted cluster if it was not. Note the time and the replication lag from
   CloudWatch to state the RPO actually lost.
3. Check the logins on the promoted cluster: the secrets (replicas), `tests/test_migration_031_live.py`,
   `tests/test_migration_033_live.py` and, if rotation is on, `tests/test_migration_034_live.py`.
4. Cognito: direct sign-in to the replica pool (or the new pool and its bindings).
5. AgentCore: deploy the Gateway, targets, Cedar engine and both Runtimes; run
   `scripts/verify_agentcore.py` and the smoke scripts with approval.
6. Backend and CloudFront: deploy the backend in the second Region and move the API origin.
7. Audit export: deploy the exporter against the promoted cluster and the replica bucket, and run
   `scripts/verify_audit_export.py` with `--compare-aurora`.
8. Rehearse: every step above is untested in this repository. Do a switchover to the second Region
   and back on a non-production global database before relying on the plan, and time each step.

### What this plan does not cover

- Recovering from corruption or a bad migration, which a global database replicates. Use point-in-time
  restore and snapshots for that, in the same Region or copied across.
- Bedrock model availability and quotas in the second Region, and the Cohere models the retrieval
  phases use.
- Cost. A second Region holds a cluster (or storage), a replica bucket, replica secrets and replica
  pool, and idle Runtimes if you keep them warm.
- Any AWS figure that changed after this was written; read the linked pages when you plan.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `AgentCore platform not configured` | The backend has no runtime, gateway or memory ID. Run `python scripts/sync_agentcore_env.py --write` and restart the backend. Production does not fall back to direct Aurora access. |
| `runtimeSessionId ... valid min length: 33` | The runtime session ID is too short. Restart the backend on current code; `_build_runtime_session_id` in `backend/agentcore/runtime.py` builds a valid ID. |
| Gateway reports `no targets were configured` | A target is missing. Check that `meridian-catalog-tools` exists, render the config and run `agentcore deploy -y`. |
| Production returns no packages | Run `python scripts/verify_agentcore.py` (expect eleven tools) and watch `agentcore logs --runtime MeridianConcierge --follow` while you repeat the prompt. |
| A hold returns `traveler_not_authorized` | The holds Lambda role has no grant. Run `python scripts/bind_gateway_workload.py`. |
| Hosted Phase 4 or 5 returns `aws_iam subject is not authorized for traveler` | The App Runner instance role has no grant for that traveler. Run `python scripts/bind_web_backend_role.py --apply`, with `--traveler trv_demo_decoy` for Jordan Lee. |
| Every hold is denied, including confirmed ones | If `verify_agentcore.py` shows the policy engine `MISSING`, render (it must print `Configuration complete.`) and deploy. If it shows `ACTIVE` and `ENFORCE`, read the denied span's `arguments`: the ceiling is the traveler's saved per-person budget times the party size, and packages above it are refused. |
| The runtime replies but the trace has no gateway spans | The runtime runs older code. `agentcore status` shows the version; `agentcore deploy -y` publishes `app/MeridianConcierge`. |
| `agentcore` fails with a cloud assembly schema version error | The CLI that ran is not the pinned release (unscoped `npx agentcore` can resolve an older cached CLI). Run `npx -y @aws/agentcore@0.31.2`. |
| `/api/health` reports `workflow_runtime_configured: false`, or Phase 5 reports AgentCore is not configured | The backend has no `AGENTCORE_WORKFLOW_RUNTIME_ARN`. Deploy `MeridianWorkflow`, run `python scripts/sync_agentcore_env.py --write` and restart the backend. See [Run the workflow Runtime](#run-the-workflow-runtime). |
| Sign-in returns to the sign-in screen with "Sign-in was not completed" | The user has no active `cognito` binding, so the trigger refused the token. Run `python scripts/seed_cognito_users.py --apply`. |
| The API returns 401 "A valid Meridian sign-in is required." | The token failed a check: expired, another app client, an ID token, or no `traveler_id` claim. Sign in again; the backend log names the reason code. |
| The API returns 503 "Sign-in verification is temporarily unavailable." | The backend could not fetch the pool's signing keys. Check its outbound network and `MERIDIAN_COGNITO_REGION`. |
| The API returns 401 with code `sign_in_required` | No usable caller token reached the API, or in `jwt` mode a request reached an AgentCore client with no caller token. Send the Cognito access token in `Authorization: Bearer`. The browser tries one refresh, then signs the traveler out and shows the sign-in screen; it does not resend the request. |
| The API returns 401 with code `token_expired` | The caller's access token ran out, possibly during a long workflow. Sign in again and repeat the request; a paused workflow resumes from its last saved step. See [Switch the AgentCore identity mode](#switch-the-agentcore-identity-mode). |
| `stop-session` returns 409 | The journey has no paused or running workflow session, or its session was already stopped. Read the journey before trying again. |
| `identity_proof.py` reports a decoy probe `allowed` where it expected `refused` | A layer let the decoy through. Stop and treat it as a security finding. The `Detail` column says which layer. Do not change the probe. |
| `identity_proof.py` reports a Gateway probe as `error` with "a failure with no refusal evidence" | The call failed without the interceptor's text, Cedar's text or code, or a new deny row, so it does not show that identity was the reason. Read `shape` and the detail in the receipt. A 401, a 5xx or a timeout is a Gateway or network fault to fix first. |
| `identity_proof.py` shows `unproven` for a layer | The decoy was refused but the Jordan control at that layer did not pass, so the refusal proves nothing. Fix the control and run again. |
| `identity_proof.py` stops with an unscoped read that could return zero rows | The preflight found a table the proof reads without a scope that the Aurora role cannot see. Fix the role or the policy before running. |
| `identity_proof.py` reports a booking that appeared and cannot be tied to the run | Something else wrote a booking during a probe. It was not deleted. Check it by id and remove it yourself if it is a test row. |
| `identity_proof.py` reports an access-denied error on a Runtime row for Jordan | The Runtime's authorizer rejected a valid token. Check that the Cognito app client is in the Runtime's allowed clients and that `Authorization` is in its request header allowlist. |
| `identity_proof.py` prints `CLEANUP:` lines or leftovers above 0 | A hold the run placed is still held or its state is unknown. List threads that start with `phase5-proof-idp` and their holds, then give each back with `scripts/release_demo_bookings.py` or the Gateway release tool, by id. Rows are never deleted. |

## Waits, retries and readback

The calls that can run long stream, and the browser waits on activity, not on a
fixed clock. Phase 4 chat, Phase 5, a hold and a booking use server-sent events
(`/api/chat/stream`, `/api/chat/order/stream`, `/api/chat/book/stream`). The
backend sends a keepalive comment every 10 seconds while it works, below
CloudFront's 60-second origin response timeout. The browser ends its wait after
30 seconds with no chunk (an idle timeout) or 110 seconds in total (a hard
ceiling), whichever comes first. The ceiling sits under App Runner's
120-second total request limit, which streaming does not extend. Reads and the
earlier phases keep a plain 55-second wait.

The server ends a stream at the same 110-second ceiling, with a final event that
tells the page to read the outcome back, and closes it. It never cancels the
work, because cancelling in the middle of a governed write could leave it half
done. What survives once the browser or the server has closed the request:

- A placed hold or booking is durable in Aurora.
- The final turn-summary audit row and the Memory unit are best effort. They run
  with no open request, App Runner gives no guarantee to work that outlives a
  request, and a deploy or scale-in in that window can drop them. A confirmed
  booking or placed hold with no final `agent_audit_log` row is therefore possible after a
  call that ran past the ceiling or a browser that left early. This is separate from
  `booking_action_audit`, which the updated holds Lambda commits in the business transaction.
  Check the booking, action audit and service logs together when reconciling such a call.
- The traveler's state stays true regardless, because the page reads what Aurora
  committed, not what the stream said.

A partial reply is marked incomplete and never retried automatically. The
runtime client uses a 45-second socket read timeout with one attempt; this is
not an end-to-end workflow deadline, and a multi-step workflow can outlive the
browser's wait.

When the browser stops waiting it reads the outcome back from Aurora instead of
showing a timeout. After a hold or a booking it reads the receipt once (10
seconds at most) and shows it when Aurora has it; otherwise it keeps the
message that the outcome could not be confirmed. After a Phase 5 turn it keeps
reading the journey, and the traveler sees the result when the worker's lease
has ended. A resume that meets a running worker answers 409 and the page keeps
reading. Reading stops after five minutes. Stopping the wait does not undo a
committed transaction.

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

## Load test

`scripts/load_test.py` sends a small, steady stream of requests that change no hold, booking or
preference to the hosted site and reports how long they took. Use it to get measured latency and
error numbers for your own deployment. This repository publishes only the low-rate baseline below,
not a capacity figure: results depend on your Region, Aurora size and Bedrock quotas.

What it measures:

| Scenario | Calls | Model cost |
| --- | --- | --- |
| `catalog` | `/api/health`, `/api/me`, `/api/products`, `/api/packages` (two requests). It writes no rows | None |
| `retrieval` | Phase 3 chat turns, which run the hybrid search (vector and keyword) over Aurora | Bedrock |
| `chat` | One read-only turn each for Phases 1, 2 and 3 | Bedrock |

What it deliberately does not do: create, confirm or release a hold or booking, start a Phase 5
workflow, or write a preference. Every request passes an allowlist of methods and paths before it
is sent, and a test fails if a write route in the application becomes reachable. Chat turns still
write audit rows only in one case. A Phase 1 turn writes none. A Phase 2 or Phase 3 turn writes one
`traveler_access_audit` row each time the model calls the `loyalty_balance` tool, which opens a
traveler-scoped session (Phase 2 runs that tool in the MCP server's child process). The `catalog`
scenario writes none. It once also called `/api/memory/{traveler}` and `/api/journeys`, which
open a traveler-scoped session and commit an access-audit row on each call, so a default run
added about 34 rows; those two requests were removed.

Arrivals are open loop: requests start at `--rate` per second whether or not earlier ones have
finished, so a slow stack shows up as high latency, not a lower rate. `--concurrency` caps the
requests in flight; an arrival that finds every slot busy is counted as `shed` and not sent. The
run stops early, exits 1 and says why when the error rate passes `--max-error-rate` (default 5%,
judged after 10 requests, and again at the end of a shorter run) or `--max-streak` (default 3) requests in a row fail with 429, 5xx, an
authorization error, no answer or a bad reply. Defaults are 2 requests per second for 60 seconds with 4 in
flight. Rates above 20 per second, durations above 600 seconds, more than 16 in flight or more
than 200 turns need `--allow-high-load`.

Run it from `meridian/` with the seeded traveler's sign-in available, as for `warm_demo.py`. The
first command is a dry run that prints the plan and sends nothing:

```sh
venv/bin/python scripts/load_test.py --hosted --scenario catalog
venv/bin/python scripts/load_test.py --hosted --scenario catalog \
  --apply --i-understand-this-changes-aws
```

Chat turns invoke Amazon Bedrock and are billed to the account, and each turn takes seconds, so
`retrieval` and `chat` require `--max-turns` and are best run slowly, for example
`--rate 0.2 --duration 100 --max-turns 20 --concurrency 2`. The script refuses to run when `CI`
is set.

Reading the result: the table and the JSON report in `.local/load-test-<scenario>-<time>.json`
give, for each endpoint and in total, the request count, errors by status (`network` is no
answer, `bad_reply` is a reply that is not the expected shape, has a failed step or has no
text, or a reply check that raised), the p50, p95 and p99 latency
in seconds, and the achieved rate. Percentiles use nearest rank over the successful (2xx)
requests only; errors are counted separately. The report also records the Git commit, the target
host with any account id masked, the scenario, the rate, the duration and the concurrency. A run
that stopped early has partial numbers: do not compare it with a full run. Run the same scenario
at a few rates, and wait for the previous run to finish, before reading anything into a trend.

For database transaction latency, see the [three-way hold benchmark](HOLD_TRANSACTION_BENCHMARK.md). It times the same two-place hold through the Data API, RDS Proxy, and a direct PostgreSQL connection.

First runs, 15 Nov 2026, against the hosted `jwt` stack (App Runner, 1 vCPU and 2 GB; Aurora
serverless with a 1 ACU floor and a reader; Runtimes on platform V1). They are a latency baseline
at low rates, not a capacity test: nothing here says how many travelers the stack can serve.

| Scenario | Rate and duration | Requests | Errors | p50 | p95 | p99 |
| --- | --- | --- | --- | --- | --- | --- |
| `catalog` (7 endpoints, including `/api/memory/{traveler}` and `/api/journeys`) | 2 per second, 60 s, 4 in flight | 120 | 0 | 0.22 s | 0.46 s | 0.54 s |
| `retrieval` (Phase 3 turns) | 0.2 per second, 100 s, 2 in flight | 20 | 0 | 3.62 s | 4.02 s | 4.04 s |
| `chat` (Phases 1 to 3) | 0.2 per second, 100 s, 2 in flight | 20 | 0 | 1.45 s | 3.97 s | 4.49 s |

The `catalog` numbers come from the run that still included `/api/memory/{traveler}` and
`/api/journeys`, which write audit rows and are not in the scenario now, so a new `catalog` run is
not comparable with that row.

In the `chat` run, Phase 1 turns took 0.17 s at the median, Phase 2 1.45 s and Phase 3 3.51 s,
which follows the work each phase does for a turn. To find a ceiling, raise the rate step by
step on a stack no one else is using, and watch Aurora ACU use, Data API throttling, Lambda
concurrency and the AgentCore quotas while it runs.
