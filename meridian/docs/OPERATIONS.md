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

## Service logins

Three Aurora logins sit next to `meridian_workflow`. None owns anything or can
bypass row-level security, and each holds only what its workload queries.

| Login | Workload | Holds |
| --- | --- | --- |
| `meridian_backend` | The App Runner backend | SELECT on `traveler_identity_bindings` and `trip_packages`, INSERT on `traveler_access_audit`, EXECUTE on `backend_admin_count`, and SET ROLE to `meridian_app` |
| `meridian_gateway` | The `MeridianHolds` and `meridian-semantic-trip-search` Lambdas | The same tables and SET ROLE to `meridian_app`, and no function |
| `meridian_identity` | The Amazon Cognito pre-token-generation Lambda | SELECT on `traveler_identity_bindings` |

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

### What is not switched yet

The logins exist, are tested against the live cluster, and have their secrets
and managed policies in place. No hosted workload uses them yet:

- The App Runner backend still connects as the master login.
- The `MeridianHolds` and `meridian-semantic-trip-search` Lambdas still connect
  as the master login.
- The shared `MERIDIAN_API_TOKEN` and the CloudFront Basic edge credential are
  still in place, and the hosted web build still has no sign-in (see
  [Sign-in settings in the web build](#sign-in-settings-in-the-web-build)).

Moving these is the coordinated release that follows (B2). Only the Amazon
Cognito trigger runs as its own login (`meridian_identity`) from its first
deploy.

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
   python scripts/provision_service_logins.py
   python scripts/provision_service_logins.py --apply --write-env
   ```

   Add `--login backend`, `gateway` or `identity` to act on one login. Running
   `--apply` again rotates the password. Between the `ALTER ROLE` and the secret
   update, anything holding the old secret fails.

3. Prove the grants against the live cluster, including what each login must
   not do:

   ```bash
   python -m pytest tests/test_service_logins_aurora.py tests/test_backend_login_aurora.py \
     tests/test_gateway_login_aurora.py tests/test_identity_login_aurora.py -m database
   ```

   The deny tests add `traveler_access_audit` deny rows for the decoy on every
   run. That is deliberate evidence, not residue.

As `meridian_backend`, the Phase 1 to 3 search agents and the Phase 2 MCP server
can read only `trip_packages` outside a traveler scope. A Phase 2 prompt that
asks about any other table is refused by AWS Aurora. The Phase 2 run under the
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
   turns that must show successful MCP tool activity with rows (the generic
   postgres-mcp server and the custom concierge server), the RLS probe, the
   session receipt and the Phase 5 stop-and-resume recovery, and writes
   `.local/release-b2/backend-login-proof.json`. An invalid receipt is written
   before anything starts, and a failed, crashed or interrupted (Ctrl-C or
   SIGTERM) run overwrites it with `ok: false`, so an older passing receipt
   never survives. A timed-out step gets SIGINT, then SIGTERM, each with a grace
   period, before SIGKILL, so its own clean-up runs. After the recovery, passed
   or failed, the run sweeps every `phase5-proof-` thread through the same purge;
   a thread found there fails the receipt. The purge counts `bookings`,
   `booking_lines`, `hold_requests`, `journeys`, `journey_executions`,
   `journey_threads`, `workflow_snapshots` and `workflow_session_stops` at zero,
   reading and deleting the two booking tables in a transaction pinned to the
   traveler and the booking agent, because they force row level security.

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
   Only the recovery step's own rows are removed.

   | Code | Meaning |
   | --- | --- |
   | 0 | every check passed and the receipt was written |
   | 1 | a check failed, the backend never answered or exited, the run crashed, or it was interrupted |
   | 3 | refused: a missing setting, a dirty tree, the wrong account or Region, a login that is not least-privilege, a bound port, a usage error or a missing confirmation flag |

[SIGNED_SCOPE_EVALUATION.md](SIGNED_SCOPE_EVALUATION.md) records why the
database does not verify a signed traveler scope itself: the logins can set any
traveler, so the application check and the workload grant remain the controls.

## Sign-in and who is calling

A signed-in person is the only source of the traveler identity. The Amazon
Cognito user pool has two seeded users: Jordan Morgan, bound to
`trv_meridian_demo`, and Jordan Lee, the decoy, bound to `trv_demo_decoy`. The
web client uses the authorization code flow with PKCE and an access token that
lasts one hour. A pre-token-generation trigger reads the user's active
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
   trigger, which the account accepted. The client allows the authorization code
   flow only for browsers, with the openid, profile and email scopes, no client
   secret, a one-hour access and ID token and a 24-hour refresh token. It also
   enables `ADMIN_USER_PASSWORD_AUTH`, which only a caller with IAM permission
   can use; the live tests use it to mint real tokens.

   `sync_cognito_env.py` writes `MERIDIAN_COGNITO_REGION`,
   `MERIDIAN_COGNITO_USER_POOL_ID` and `MERIDIAN_COGNITO_APP_CLIENT_ID` to `.env`,
   and `VITE_COGNITO_DOMAIN` and `VITE_COGNITO_CLIENT_ID` to
   `frontend/.env.development.local`. Vite reads that file only in development,
   so a hosted build has no sign-in until the coordinated release moves the two
   lines.

2. Create the users. The script generates each password, stores it in AWS
   Secrets Manager (`meridian/cognito/jordan` and `meridian/cognito/decoy`) and in
   the macOS Keychain (service `meridian-cognito`), and writes the binding rows.
   It prints no password. Without `--apply` it only reports:

   ```bash
   python scripts/seed_cognito_users.py
   python scripts/seed_cognito_users.py --apply
   ```

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

   The decoy signs in but is refused its own records. Workload bindings are
   `aws_iam` grants and are bound to Jordan Morgan only, so a signed-in decoy
   gets 403 on `me` with `aws_iam subject is not authorized for traveler
   trv_demo_decoy`, as well as on Jordan's records by id. That is the second
   check working. It stays that way until B2 decides whether to bind a workload
   to `trv_demo_decoy`, so a decoy that sees an empty or refused workspace is the
   expected result, not a broken sign-in.

3. Check the claim, the verification and the refusals against the deployed pool:

   ```bash
   python -m pytest tests/test_cognito_aurora.py -m database
   ```

How the backend decides who is calling. `require_http_principal` tries a bearer
token in this order and never retries a failed token against a weaker check:

1. The shared `MERIDIAN_API_TOKEN`, which the hosted site still uses.
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

The workload grant is a second check. The App Runner role and the Lambda roles
are bound to Jordan only, so a signed-in decoy is refused on its own records as
well.

### Sign-in settings in the web build

`VITE_COGNITO_DOMAIN` and `VITE_COGNITO_CLIENT_ID` together turn on the sign-in
screen. With neither set, the bundle is an ungated build and writes one console
line, "Meridian is running as an ungated build: sign-in is not configured."
`VITE_REQUIRE_SIGN_IN=1` makes a build fail when either variable is missing
instead of shipping without sign-in; leave it unset for local and plain builds.
The failure message names both variables.

Today no build sets `VITE_REQUIRE_SIGN_IN`, and `scripts/publish.py` does not
pass the two Cognito variables, so the hosted site stays ungated and keeps using
the shared token until the B2 coordinated release. A build that carried its own
`Authorization` header would be rejected by the CloudFront viewer function,
which is why the settings live in `frontend/.env.development.local`.

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

Undo in the reverse of the order above and stop at the step you need.

1. Users. Delete the two Cognito users and the two `meridian/cognito/*`
   secrets, delete the `cognito` binding rows
   (`DELETE FROM traveler_identity_bindings WHERE identity_provider = 'cognito'`
   as the master login), then remove the Keychain items:

   ```bash
   security delete-generic-password -s meridian-cognito -a jordan.morgan@example.com
   security delete-generic-password -s meridian-cognito -a jordan.lee@example.com
   ```

2. Identity stack. From `infra/`, destroy the stack with the same `-c` values
   you deployed with, then remove the settings it wrote:

   ```bash
   node_modules/.bin/cdk destroy -a "node dist/bin/meridian-identity.js" \
     -c domainPrefix=<unique-prefix> -c callbackUrls=<urls> --force
   sed -i '' '/^MERIDIAN_COGNITO_/d' .env
   rm frontend/.env.development.local
   ```

3. Logins (provisioning). For each of the three roles, run
   `ALTER ROLE <role> NOLOGIN PASSWORD NULL`, delete its secret with
   `ForceDeleteWithoutRecovery`, delete the non-default versions of its managed
   policy and then the policy, and remove the three `AURORA_*_SECRET_ARN` lines
   from `.env`. Do this before the migration rollback.

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

   Roll back only while no workload uses the logins. After the coordinated
   release, move the workloads back to the master login first.

## Switch the AgentCore identity mode

`MERIDIAN_AGENTCORE_AUTH` chooses how every AgentCore call is authenticated. `iam` (unset means
`iam`) signs each call with AWS credentials and is what the deployed release uses. `jwt` sends the
signed-in person's Cognito access token on every hop. Any other value is refused by name.

The code for `jwt` mode is in the repository and tested in both modes. Nothing deployed uses it.

### What is not switched until the release

- Both Runtimes still use IAM authorizers, and neither lists `Authorization` in its request header
  allowlist.
- The Gateway still uses an IAM authorizer and has no request interceptor attached.
- The Cedar policy `meridian_traveler_binding` is not in the deployed policy engine. The render adds
  it only in `jwt` mode.
- The backend and the hosted service do not set `MERIDIAN_AGENTCORE_AUTH`, so they run in `iam`
  mode. The hosted site still uses the shared `MERIDIAN_API_TOKEN`.
- The command-line proofs and checks other than the three smoke scripts still sign with AWS
  credentials and stop working against a `jwt` Runtime until they are converted.
- `StopRuntimeSession` stays IAM-signed in both modes.

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

The release then changes, in one window and with a read-back after each step: the Gateway
(authorizer, allowed clients and interceptor, through the API), then both Runtimes and the Cedar
rule through the stack deploy, then the holds Lambda, and last the backend and the hosted service with
`MERIDIAN_AGENTCORE_AUTH=jwt`. Setting `jwt` on the backend alone, or on one Runtime alone, makes
that hop send or expect a credential the next hop refuses.

### The window order

CloudFormation cannot change an existing Gateway's authorizer type: `agentcore deploy -y` fails with
"Authorizer type cannot be updated for an existing gateway" and the stack rolls back. It also
compares the template with the deployed stack template, not with the live Gateway, so moving the
live Gateway does not make a `CUSTOM_JWT` template acceptable (the second window's read-only diff
planned `AWS_IAM` to `CUSTOM_JWT`, and that entry stays after the move). The template cannot
declare the interceptor either, and an update that leaves `interceptorConfigurations` out
detaches it.

**Deliberate divergence.** In `jwt` mode the render keeps the Gateway resource exactly as the
deployed stack has it: `AWS_IAM`, no JWT authorizer block. The Gateway resource is identical in both
modes. The `UpdateGateway` API (`release_identity.py gateway`) owns the live Gateway's authorizer,
allowed clients and interceptor, so after the move the live Gateway reads `CUSTOM_JWT` while the
template says `AWS_IAM`. That is expected, not drift: `check` and `deploy` compare the live Gateway
with the mode in `.env` (the `jwt` expectation from the settings), never with the template. Both
Runtimes' authorizers and allowlists, the Cedar rule, the role statements and the holds role's
secret read are still rendered and deployed by the stack. Two rules follow:

- A future change to the Gateway's authorizer goes through the release command
  (`release_identity.py gateway`), never through the template or CloudFormation.
- Never run `agentcore deploy` bare in `jwt` mode. Use `release_identity.py deploy`, which refuses
  if the rendered Gateway is not the stack's, if the live Gateway does not report the mode, or if
  the plan from `agentcore deploy --diff --json` changes the Gateway authorizer.

So the Gateway moves first through the `UpdateGateway` API, the deploy runs, and the Gateway is
read back afterwards. Every
command below is a dry run until it gets `--apply --i-understand-this-changes-aws`; read the plan,
then ask. Use `check --skip-service` for every read until the service moves (step 9).

1. Set `MERIDIAN_AGENTCORE_AUTH=jwt` in `.env`, then `venv/bin/python scripts/release_identity.py
   check --skip-service`. It lists drift for the Gateway, both Runtimes and the rule. Nothing live
   changes.
2. Render with the Cedar rule left out and validate (the rule is validated against the Gateway, so
   it joins in step 7): `MERIDIAN_GATEWAY_ENFORCEMENT=interceptor venv/bin/python
   scripts/render_agentcore_config.py`, then `(cd meridian_agentcore &&
   /opt/homebrew/bin/agentcore validate --json)`.
3. Move the Gateway. The outage starts here: every SigV4 caller of the Gateway fails.

   ```bash
   venv/bin/python scripts/release_identity.py gateway
   venv/bin/python scripts/release_identity.py gateway --apply --i-understand-this-changes-aws
   ```

   One `UpdateGateway` call carries the `CUSTOM_JWT` authorizer, the discovery URL, the allowed
   client and the interceptor, after the invoke grant is written, and the Gateway is read back.
4. Deploy the stack with the tool, never with a bare `agentcore deploy`:

   ```bash
   venv/bin/python scripts/release_identity.py deploy
   venv/bin/python scripts/release_identity.py deploy --apply --i-understand-this-changes-aws
   ```

   The ordering preflight refuses (exit 2, nothing deployed) unless the rendered `agentcore.json`
   holds the stack's Gateway (`AWS_IAM`, no JWT block: the live Gateway is `CUSTOM_JWT` and that
   is expected), and the live Gateway already reports the mode's authorizer, the same discovery
   URL and allowed client, and the interceptor. The apply first runs `/opt/homebrew/bin/agentcore
   deploy --diff --json` (read-only) and refuses if the plan changes the Gateway authorizer or
   the plan cannot be read. Then it runs `/opt/homebrew/bin/agentcore deploy -y`
   from `meridian_agentcore/`, waits for the Gateway to be READY, and reads it back. If the deploy
   changed the authorizer or detached the interceptor, it prints what changed and sends the
   `gateway` update again, then reads back again. This deploy removes the `InvokeGateway`
   statement from both Runtime roles, adds the gateway-secret read to the holds Lambda's role and
   moves both Runtimes to the Cognito authorizer.
5. `venv/bin/python scripts/release_identity.py check --skip-service`. Only the missing Cedar rule
   is listed.
6. Move the holds Lambda and its SSM parameter to the gateway login. They wait for step 4,
   because the holds role can read the gateway secret only after that deploy; the parameter
   write refuses (exit 2, nothing written) until it can.

   ```bash
   python scripts/publish_gateway_parameters.py --gateway-login
   python scripts/publish_gateway_parameters.py --gateway-login --apply --i-understand-this-changes-aws
   python scripts/release_identity.py lambdas --restart-holds --apply --i-understand-this-changes-aws
   python scripts/release_identity.py lambdas --expect gateway
   ```

7. Render with the rule and deploy again, again through the tool (it re-reads the Gateway after
   this deploy too): `venv/bin/python scripts/render_agentcore_config.py`, then `release_identity.py
   deploy` and `release_identity.py deploy --apply --i-understand-this-changes-aws`.
8. `venv/bin/python scripts/release_identity.py check --skip-service` prints `OK` for every hop.
9. Publish the roles, the service and the site (`scripts/publish.py`, plan first). The outage
   ends. Then `venv/bin/python scripts/release_identity.py check --service-arn "$SERVICE_ARN"`.

What is verified and what is assumed:

| Statement | Status |
| --- | --- |
| The AgentCore CDK construct used here (`@aws/agentcore-cdk` 0.1.0-alpha.47) never sets `InterceptorConfigurations`, so the template cannot declare the interceptor | Verified in the installed code |
| An update that omits `interceptorConfigurations` detaches the interceptor | Measured on the throwaway Gateway (check C5) |
| CloudFormation refuses to change the authorizer type of an existing Gateway | Observed in the first window |
| CloudFormation compares the template with the deployed stack template, not with the live Gateway | Inferred from the first window's failure and the second window's diff, which plans `AWS_IAM` to `CUSTOM_JWT` against the stack template. Not yet confirmed by a deploy. The render therefore keeps the stack's Gateway authorizer and the deploy plan must show no Gateway authorizer change |
| A deploy that plans no Gateway change leaves the live authorizer and interceptor alone | Assumed, not verified, so it is never relied on: the Gateway is read back after every deploy |
| A deploy that does update the Gateway sends the template's properties, so it can detach the interceptor or revert a field | Assumed possible, so it is never relied on: the Gateway is read back after every deploy (authorizer, allowed clients, interceptor and policy engine) and re-applied when it differs |
| The deploy leaves the other Gateway fields (description, protocol configuration, exception level) alone | Not read back by `deploy`; the rollback dry run compares them with the snapshot |

### Read the release back

`scripts/release_identity.py check` only reads. It compares the Gateway, both Runtimes, the Cedar
rules, the identity stack, the backend login proof, the interceptor Lambda's environment and the
App Runner service with the mode in `.env`. Name the service with `--service-arn ARN`, or leave it
out on purpose with `--skip-service`. A `check` with neither prints `NOT CHECKED` and exits 4 even
when every hop matches, so use `--skip-service` for every read before the service moves (steps 1 to 8 of the
window) and `--service-arn` after it. The backend login proof is compared only in
`jwt` mode, so `check --expect iam` shows no proof line even when no receipt exists.
`interceptor`, `interceptor-delete`, `lambdas --restart-holds`, `semantic-lambda`, `gateway`, `deploy` and
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

Two Lambdas still connect as the master login until this step: the `MeridianHolds` Lambda (through
the SSM parameter `/meridian/aurora/secret_arn`) and `meridian-semantic-trip-search` (through its
`AURORA_SECRET_ARN` variable). Every command below is a dry run until it gets `--apply
--i-understand-this-changes-aws`, checks that the credentials belong to the account and Region of
`AURORA_CLUSTER_ARN` before it builds any other client, and prints no secret value.

The order matters. The semantic Lambda's role is outside the stack, so `semantic-lambda` can run
before the window. The holds Lambda's role belongs to the AgentCore stack and gets the read on the
gateway login's secret only from the first jwt deploy (step 4 of the window). The
`--gateway-login --apply` of `publish_gateway_parameters.py` therefore reads the holds role first
and refuses, with nothing written, until it can read that secret: moving the parameter earlier
would leave every new holds cold start unable to connect.

```bash
python scripts/publish_gateway_parameters.py --gateway-login
python scripts/publish_gateway_parameters.py --gateway-login --apply --i-understand-this-changes-aws
python scripts/release_identity.py lambdas --restart-holds --apply --i-understand-this-changes-aws
python scripts/release_identity.py semantic-lambda
python scripts/release_identity.py semantic-lambda --apply --i-understand-this-changes-aws
python scripts/release_identity.py lambdas --expect gateway
```

`publish_gateway_parameters.py` writes the three parameters and reads each back (the secret
parameter with the same comparison `lambdas` makes). The holds Lambda keeps its cached value until
`lambdas --restart-holds`. `semantic-lambda` first gives the semantic Lambda's role an inline policy
(`meridian-gateway-login-read`, one statement marked `MeridianGatewayLoginRead`) that allows
`secretsmanager:GetSecretValue` on the gateway login's secret alone; a policy of that name without
the mark is never touched. It then reads the Lambda's whole environment, changes only
`AURORA_SECRET_ARN` and sends every variable back with the revision it read, and reads both
changes back. It refuses a function whose environment cannot be read in full (a KMS error, no
variables), one whose `AURORA_SECRET_ARN` names neither login's secret, and a role in another
account. A second run changes nothing. The way back is `semantic-lambda --to master --apply
--i-understand-this-changes-aws`, which keeps the grant; add `--remove-grant` to delete the policy
the tool added. The operator profile needs `iam:GetRolePolicy` and `iam:PutRolePolicy` (and
`iam:DeleteRolePolicy` for `--remove-grant`) on that role, `lambda:UpdateFunctionConfiguration`
on the function and `ssm:PutParameter` on `/meridian/aurora/*`.

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
response headers policy, the roles stack, the holds and semantic Lambdas and the active Cedar rules,
and writes them to `.local/release-b2/snapshot-<UTC time>.json` (mode 0600, with a SHA-256 over the
content). It holds no secret value: a plain value under a credential-looking name is replaced with
`<redacted>`, a token-shaped or access-key-shaped string with `<token>`, and the paths are listed
under `redacted`. The command exits 1 and saves nothing when a hop already reports a finding (a
release under way, or drift); `--accept-baseline` saves that state on purpose.

`python scripts/release_identity.py rollback` is a dry run: it prints, for every hop, each
difference from the snapshot with both values in full. `rollback --apply
--i-understand-this-changes-aws` restores them. It uses the newest intact snapshot (`--snapshot
FILE` pins one) and prints the time and commit of the one it uses, loudly when a newer file was
skipped. Each write is read back, up to 60 checks 5 seconds apart, until the hop equals the
snapshot. A hop whose saved copy was redacted anywhere is refused, not restored, and a failed hop
skips only the steps that need it: the Runtimes are skipped when the Gateway step failed, and the
Lambdas when the secret parameter step failed. Running it again is safe; a hop that already matches
is only read, and nothing is ever deleted. `rollback-result.json` records each step and any holds
restart still owed, so a later run does it.

Restore order, which is the true reverse of the release:

1. the site (viewer function and response headers policy), then the App Runner service
2. the roles stack (checked; a manual command is printed)
3. the Gateway through the `UpdateGateway` API (authorizer back to IAM, interceptor detached),
   then both Runtimes
4. the AgentCore stack (checked; commands printed): the Cedar rules and the `InvokeGateway`
   statement on both Runtime roles
5. the secret parameter, then the holds Lambda (restarted) and the semantic Lambda

The Gateway comes first among the AgentCore hops because CloudFormation cannot change an authorizer
type either, and no stack step may run while the live Gateway still reads the token authorizer.
The Gateway resource in the IAM render is the same one the `jwt` render holds (`AWS_IAM`), so the
IAM render matches the deployed stack's Gateway. Step 4 is skipped, and says so, when the Gateway
step failed.

The jwt deploy removes the `InvokeGateway` statement from both Runtime roles, and restoring a
Runtime with `UpdateAgentRuntime` does not bring it back, so an IAM Runtime could not reach its
tools. Only the IAM render does, so step 4 reads both roles and, until they can call the Gateway,
prints the exact commands: a checkout of the snapshot's commit, `MERIDIAN_AGENTCORE_AUTH=iam python
scripts/render_agentcore_config.py`, `/opt/homebrew/bin/agentcore deploy -y` from that checkout's
`meridian_agentcore/`, then, from the current checkout, `venv/bin/python
scripts/release_identity.py check --expect iam --service-arn "$SERVICE_ARN"`, which also reads the
Runtime roles, and `rollback` again. `snapshot` in `iam` mode records a role that already lacks
the grant as a baseline finding, so a snapshot never demands more than the release started with.

Between steps 1 and 3 the service is back in `iam` while the Gateway and the Runtimes still expect
`jwt`, so signed-in traffic through the service fails until step 3 finishes. The release has the
mirror window (Runtimes in `jwt` before the service moves), so this is the same size; keep the
maintenance window open until the command reports "Rollback complete".

The roles stack, the AgentCore stack and the distribution's behaviors are not changed by this
command. Each is printed with the snapshot's commit and the commands to run from a checkout (`git worktree
add`) of that commit, with the account, Region and service ARN taken from your own `.env` as shell
variables. Do not run them from the current checkout: it would deploy the new release again. After
them, run `rollback` again to re-attach the interceptor and verify; the exit code is 1 until every
hop matches. The Aurora logins and the Cognito pool work with both modes and are not rolled back.
The restore uses the service APIs and has been exercised only against recording fakes; its first
real exercise is the rehearsal.

## Prove the decoy is refused

`scripts/identity_proof.py` records, in a receipt, that a second signed-in user is refused at four layers and
that Jordan is allowed at each. The second user is the decoy: Jordan Lee, bound to `trv_demo_decoy`, with a valid
Amazon Cognito access token and a real traveler row. Run it after the identity release (see
[Switch the AgentCore identity mode](#switch-the-agentcore-identity-mode)), in `jwt` mode, from a clean checkout of the
released commit. Until the release the default mode is `iam` and the command refuses to run. It signs in as both users
with tokens minted in memory from Secrets Manager, so it prints and stores no token and no password.

```bash
python scripts/identity_proof.py                                              # the plan only, no AWS call
python scripts/identity_proof.py --apply --i-understand-this-changes-aws      # the proof
python scripts/identity_proof.py --apply --i-understand-this-changes-aws --jordan-only
python scripts/identity_proof.py --render .local/identity-proof/latest.json   # rebuild the pages only
```

`--base-url` names the hosted site (the default comes from the release record) and `--output-dir` names a folder
inside `.local/`. The plan has 17 probes, in the order they run. Each row has an expectation: a probe that aims the
decoy at Jordan's data must end `refused`, and every other probe must end `allowed`. Two probes are decoy controls
(`runtime.decoy_pings_workflow` and `gateway.decoy_reads_package`): they send the decoy's own valid token with no
traveler named, so the decoy must be let in. They show that a refusal at the same layer came from the traveler check
and not from a layer that turns the decoy away for any request.

| Probe | Layer | Who | What it sends |
| --- | --- | --- | --- |
| `backend.decoy_reads_jordan_memory` | Backend | decoy | `GET /api/memory/<Jordan's traveler id>` |
| `backend.decoy_orders_for_jordan` | Backend | decoy | `POST /api/order` naming Jordan's traveler id |
| `backend.decoy_me_is_decoy` | Backend | decoy | `GET /api/me`: the decoy's own identity, allowed |
| `backend.jordan_me_is_jordan` | Backend | Jordan | `GET /api/me` |
| `backend.jordan_reads_own_memory` | Backend | Jordan | `GET /api/memory/me` |
| `database.decoy_sees_jordan_rows` | AWS Aurora | decoy | Pin the decoy, step down to `meridian_app`, count Jordan's rows |
| `database.jordan_sees_own_rows` | AWS Aurora | Jordan | The same transaction with Jordan pinned |
| `runtime.decoy_tampers_workflow` | Runtimes | decoy | `MeridianWorkflow` start with Jordan's traveler in the payload |
| `runtime.decoy_tampers_concierge` | Runtimes | decoy | `MeridianConcierge` turn with Jordan's traveler in the payload |
| `runtime.decoy_pings_workflow` | Runtimes | decoy | `MeridianWorkflow` ping with the decoy's token, allowed |
| `runtime.jordan_pings_workflow` | Runtimes | Jordan | `MeridianWorkflow` ping |
| `runtime.jordan_runs_workflow` | Runtimes | Jordan | A review-only `MeridianWorkflow` start, purged afterwards |
| `runtime.jordan_opens_concierge` | Runtimes | Jordan | A `MeridianConcierge` turn, first event only |
| `gateway.jordan_reads_package` | Gateway | Jordan | `get_package_details` for one package |
| `gateway.decoy_reads_package` | Gateway | decoy | `get_package_details` for one package with the decoy's token, allowed |
| `gateway.decoy_holds_for_jordan` | Gateway | decoy | `create_courtesy_hold` with `travelerId` set to Jordan |
| `gateway.jordan_places_hold` | Gateway | Jordan | `create_courtesy_hold`, released afterwards |

The database probe opens its own Data API transaction. It does not use the workload grant, which the decoy lacks by
decision (`tests/test_decoy_has_no_workload_binding.py`), so a zero count is row-level security's answer and nothing
else.

### Read the receipt

The run writes, under `.local/identity-proof/`: `receipt-<UTC stamp>.json` and `latest.json` (mode 0600), and the
pages `receipt-<stamp>.summary.html` and `receipt-<stamp>.full.html`. Before it signs in, it replaces `latest.json`
with a failed receipt, so an older passing receipt cannot outlive a run that dies. The receipt holds no token and no
account id (`account` is always `<acct>`); the writer refuses to write one that does. It records the commit, the
Region, the last four characters of the pool id, the site host, the mode and the cleanup result. Each outcome
records the probe, layer, actor, expectation, result, `refused_by`, a scrubbed detail, small evidence values and the
time. `design` says which Gateway enforcement shipped: `both` (the interceptor replaces `travelerId` with the token's
traveler and Cedar denies a mismatch), `cedar` or `interceptor`. `refused_by` is one of:

| Value | Meaning |
| --- | --- |
| `backend_identity_check` | The API compared the request's traveler with the token's and refused with 403 |
| `workload_grant` | The API reached the database grant check, which has no binding for the decoy |
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

Jordan's controls write a review-only Workflow run and one courtesy hold. Cleanup releases bookings by exact id. Before
it commits, it recounts the booking's rows in `bookings`, `booking_lines` and `hold_requests`; any row left cancels the
release and is reported. It releases only a booking that the call's own answer or its journey reference ties to this
run. Any other booking that appears while a probe runs is reported and never deleted, and it fails the receipt, as does
any booking a decoy probe makes. Threads are purged after the bookings are released, and every step runs even if an
earlier one failed. A leftover or a cleanup problem fails the receipt.

Some residue stays on purpose and is listed under `notes` in the receipt. Audit rows (`traveler_access_audit` and the
agent audit log) are append-only. The Concierge probes write AgentCore Memory events for Jordan's traveler, under the
session ids the receipt names, and this command does not delete them; they expire with the memory's 30-day event expiry.

The proof, the captures that use it and the deck slide that shows it all run after the release, in the window the
owner approves. Nothing here has been run against the deployed system yet.

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

The next `scripts/publish.py --apply` carries two additive changes beyond the new
frontend and backend. When `AURORA_BACKEND_SECRET_ARN` is in `.env`, the roles
stack attaches `MeridianBackendAuroraAccess` to the live App Runner instance
role, and the service receives `AURORA_BACKEND_SECRET_ARN` as an environment
variable. The backend does not read that variable yet, so behavior is unchanged.
State both in the release note, because "no live change" holds for a Git push
but not for the next publish.

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
| Sign-in returns to the sign-in screen with "Sign-in was not completed" | The user has no active `cognito` binding, so the trigger refused the token. Run `python scripts/seed_cognito_users.py --apply`. |
| The API returns 401 "A valid Meridian sign-in is required." | The token failed a check: expired, another app client, an ID token, or no `traveler_id` claim. Sign in again; the backend log names the reason code. |
| The API returns 503 "Sign-in verification is temporarily unavailable." | The backend could not fetch the pool's signing keys. Check its outbound network and `MERIDIAN_COGNITO_REGION`. |
| The API returns 401 with code `sign_in_required` | No usable token reached the API. The browser tries one refresh, then signs the traveler out and shows the sign-in screen; it does not resend the request. |
| The API returns 401 with code `token_expired` | The caller's access token ran out, possibly during a long workflow. Sign in again and repeat the request; a paused workflow resumes from its last saved step. See [Switch the AgentCore identity mode](#switch-the-agentcore-identity-mode). |
| The API returns 401 with code `sign_in_required` | In `jwt` mode a request reached an AgentCore client with no caller token. Send the Cognito access token in `Authorization: Bearer`. |
| `stop-session` returns 409 | The journey has no paused or running workflow session, or its session was already stopped. Read the journey before trying again. |
| `identity_proof.py` reports a decoy probe `allowed` where it expected `refused` | A layer let the decoy through. Stop and treat it as a security finding. The `Detail` column says which layer. Do not change the probe. |
| `identity_proof.py` reports a Gateway probe as `error` with "a failure with no refusal evidence" | The call failed without the interceptor's text, Cedar's text or code, or a new deny row, so it does not show that identity was the reason. Read `shape` and the detail in the receipt. A 401, a 5xx or a timeout is a Gateway or network fault to fix first. |
| `identity_proof.py` shows `unproven` for a layer | The decoy was refused but the Jordan control at that layer did not pass, so the refusal proves nothing. Fix the control and run again. |
| `identity_proof.py` stops with an unscoped read that could return zero rows | The preflight found a table the proof reads without a scope that the Aurora role cannot see. Fix the role or the policy before running. |
| `identity_proof.py` reports a booking that appeared and cannot be tied to the run | Something else wrote a booking during a probe. It was not deleted. Check it by id and remove it yourself if it is a test row. |
| `identity_proof.py` reports an access-denied error on a Runtime row for Jordan | The Runtime's authorizer rejected a valid token. Check that the Cognito app client is in the Runtime's allowed clients and that `Authorization` is in its request header allowlist. |
| `identity_proof.py` prints `CLEANUP:` lines or leftovers above 0 | Something the run made remains. List threads that start with `phase5-proof-idp` and holds the run placed, then purge and release them by id. |

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
