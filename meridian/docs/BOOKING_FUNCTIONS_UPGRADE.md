# Upgrade to the bounded booking functions (migrations 026 to 029)

Migrations 026 to 029 change booking permissions, caller correlation and failure handling
together. Booking writes move to three `SECURITY DEFINER` functions that only the
`meridian_gateway` login may execute, and each hold, confirmation and release commits a
`booking_action_audit` row in the same transaction. Local checks establish behavior in the
source tree. They do not establish that a deployed Gateway accepts the revised schema or that
an existing Aurora cluster has the new grants.

All commands below that contact AWS, including listing and rolled-back migration checks, require
approval for that step under [the repository instructions](../AGENTS.md#3-hard-rules). Use a
sandbox and a clean, committed revision. Keep live receipts under `.local/` and record the
revision they exercised.

## Existing installations

First confirm migrations through 025 are applied. Earlier installations must complete their
existing compatibility releases before this sequence. Capture a database backup and the deployed
revision, verify which database secret each service uses, and pause new booking traffic during
the coordinated target, schema and policy transition. Migration 028 adds foreign keys to
`bookings`, `hold_requests`, `journeys`, `travelers` and `traveler_access_audit`, which waits for
long-running transactions on those tables. Reads alone do not validate write permissions.

Use the master secret only in the administrative process. Never point a deployed workload at it.
Run commands from `meridian/` with its virtual environment active.

```bash
python scripts/apply_migrations.py --pending
python scripts/apply_migrations.py --dry-run --only 026_serialize_booking_transitions.sql
python scripts/apply_migrations.py --only 026_serialize_booking_transitions.sql
```

Review each dry run before its apply, then read `--pending` again. `--only` refuses a file that is
not the first pending migration. Repeat this pattern for each named migration below; do not run
the script bare. Rolled-back dry runs still execute DDL and acquire locks.

| Order | Change | Check before proceeding |
| --- | --- | --- |
| 1 | **026** `serialize_booking_transitions` | Allocation and confirmation take the same inventory lock; missing agent scope is refused; late confirmation fails after expiry. |
| 2 | **027** `prepare_booking_write_cutover` | `meridian_gateway` can execute all three bounded business functions, including release. Existing application grants remain temporarily for compatibility. |
| 3 | **028** `booking_action_audit` | Audit table, foreign keys, RLS and append-only service grants exist. A hold and its audit insert can run in one transaction. |
| 4 | Deploy the updated tool schemas and application components | Holds Lambda gets the matching `MERIDIAN_AGENTCORE_AUTH` setting. Stage both Runtime bundles; deploy backend, Runtimes and interceptor. For the signed-in configuration use JWT mode with `MERIDIAN_GATEWAY_ENFORCEMENT=both`; deploy the binding policy with `sub` and `traveler_id` checks after its input schema exists. Until every part is deployed, pinned calls are refused. |
| 5 | Read back the deployment and exercise both identities | Verify active authorizers, interceptor headers, policy engine in ENFORCE, target code revision, service secrets, successful actions, refusals and atomic audit rows. |
| 6 | **029** `restrict_booking_writes` | Apply only after no old Lambda is serving traffic. Direct DML and business-function execution through `meridian_app` are now revoked. |
| 7 | Re-run acceptance checks using real service logins | All three actions succeed through Gateway. Direct booking DML fails. Backend and Workflow roles cannot invoke the booking functions. |

Migration 029 is deliberately separate: applying it before the updated Lambda breaks old write
paths. The Runtime and Gateway schemas expose `verifiedSubject` and `authorizationRef` as
trusted binding fields. The interceptor overwrites both; Cedar independently compares the
subject and traveler to the authenticated principal. Do not treat the subject audit as verified
end-user evidence until this entire JWT chain is deployed and checked.

Use the [AgentCore deployment runbook](AGENTCORE_DEPLOY_RUNBOOK.md) for the schema-first policy
deployment and [service-login procedures](OPERATIONS.md#service-logins) for credentials. Do not
provision, rotate or broaden unrelated roles as part of this upgrade.

## New installations

Initialize only an empty database, then apply pending migrations individually in order. No old
application is serving traffic, so 026 to 029 can all precede the first deployment. Seed once,
provision service credentials, bind the intended workloads, deploy the chosen authentication
mode and run the same acceptance matrix. The schema and seed commands refuse existing data.

## Acceptance matrix

Capture actual database rows after each scenario, not just the tool return or the UI's message.

| Scenario | Required result |
| --- | --- |
| Valid Jordan token, Jordan action | One booking; one committed action audit row with Jordan's subject, the workload grant and service request IDs. |
| Valid second-traveler token naming Jordan | Backend and Runtime reject the mismatch, or Gateway pins the request to the second traveler. Jordan's rows remain invisible; the holds workload grant denies the second traveler unless explicitly granted. |
| Missing traveler or subject, invalid signature, issuer, client or expiry | Fail before protected tool execution. Missing fields cannot fall back to Jordan. |
| Forged traveler, subject or authorization reference | Trusted layers overwrite the fields; detached-interceptor Cedar tests still reject a claim and argument mismatch. |
| Default-denied action or excessive quantity or hold time | Gateway refusal; no booking or action audit row. Capture the Gateway error and service log. |
| Confirmation after waiting past expiry | `hold_expired`; no over-allocation. |
| Concurrent hold and confirmation | Serialized inventory decision; no capacity oversubscription. |
| Commit succeeds but reply is lost | Unknown outcome to caller; retry reads the original booking, request ID and expiry. One booking. A repeated target call appends a replay audit; a workflow committed-result lookup makes no new target call or action row. |
| Audit insert fails | Entire business transaction rolls back. |
| Snapshot persistence fails after the hold commit | Retry may repeat the step; committed-result lookup prevents a second hold. |
| Stale worker after replacement owns the lease | `execution_lease_lost`; no action by the stale worker. |
| Empty reviewed shortlist or missing inventory row | No substitute package and no hold. |
| Service login attempts direct INSERT, UPDATE or DELETE on booking tables | Permission denied after 029; no change. |

The caller-subject policy has offline Cedar coverage; its acceptance by AgentCore Policy and its
service log correlation need a live check on the target deployment.

## Rollback

Before 029, old code remains compatible with the preparatory grants, but the audit guarantees
apply only to actions handled by the new code. After 029, redeploying the old Lambda alone is
not a safe rollback. Stop booking traffic and either roll forward to a fixed Lambda or prepare
an explicitly reviewed permission-restoration migration. Do not edit applied migrations,
restore broad grants silently, or drop the audit table to make old code run.

## Reset a test booking

Capture the rows you need before cleanup. The reset lists records by default:

```bash
python scripts/release_demo_bookings.py --booking-id <selected-booking>
```

After inspecting the selected row and approving the deletion:

```bash
python scripts/release_demo_bookings.py --booking-id <selected-booking> \
  --apply --i-understand-this-changes-aws
```

This deletes the selected booking, lines, hold identity and associated action audit rows in one
administrative transaction. It is not the user-facing `release_hold` action. Saved journey and
snapshot history remains, so start a new conversation after resetting. Never resume an old saved
workflow whose business records were administratively deleted.
