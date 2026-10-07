# Evaluation: a database-verified signed scope

Status: evaluation note, nothing built. Date: 2026-10-07. Decision: do not ship for the
reference app; rely on the existing layers. Confidence levels and the evidence labels are
explained at the end.

Evidence labels used below: **documented** (an AWS page, linked), **repo** (read in this
repository), **inferred** (reasoning or upstream PostgreSQL behavior that was not checked
against an AWS page).

## The question

Today the application sets `app.current_traveler_id` for each transaction, from a value it
supplies after its own authorization (repo: `examples/rls_for_agents.sql`,
[STATEFUL_ARCHITECTURE.md](STATEFUL_ARCHITECTURE.md)). Aurora trusts that value. Should the
database instead verify a short-lived signed scope (traveler, expiry, audience) before RLS
applies, so a login that can set any value cannot choose the traveler?

## What the logins can do today

- `meridian_backend`, `meridian_gateway` and `meridian_workflow` are NOBYPASSRLS, NOINHERIT,
  own nothing, and reach `meridian_app` only through `SET ROLE` (repo: migrations 015 and 018).
- Setting a custom parameter such as `app.current_traveler_id` needs no privilege
  (inferred, upstream PostgreSQL). Each login can therefore open a transaction, set any
  traveler, step down to `meridian_app`, and read or write that traveler's rows.
- The grant check against `traveler_identity_bindings` runs in application code before the
  scope is set (repo). The database cannot repeat it, because a Data API session shows the
  login, not the calling IAM role (inferred). This is the real gap: authorization is
  enforced in the caller, and RLS enforces whatever traveler the caller names.
- The only cross-traveler read a login holds is the `backend_admin_count` definer function,
  which returns counts (repo: migration 018). The master login bypasses RLS and is out of
  scope here.

The threat is a compromised or buggy workload (the App Runner backend, the holds or search
Lambdas, the `MeridianWorkflow` Runtime) that names a traveler it was never authorized for.
Reaching the database as one of these logins needs the login secret and `rds-data` permission,
so the attacker already holds that workload's IAM role or code execution inside it.

## What it would add beyond the existing layers

- RLS limits a scoped session to one traveler. It does not decide which traveler.
- The Gateway interceptor and the Cedar rule force `travelerId` to the verified token claim,
  but only on the tool path. The backend's direct queries and the workflow Runtime's snapshot
  reads and writes never cross the Gateway.
- A signed scope makes the database refuse a traveler that no trusted signer vouched for.
  A compromised workload could then act only for travelers whose unexpired tokens it
  receives, instead of for every traveler.
- It does not help against a compromised signer, the master login, or a bug in the signer
  that signs the wrong traveler. It moves the root of trust, it does not remove it.

## Candidate designs

**Verification primitives on Aurora PostgreSQL 18.** `pgcrypto` 1.3 is listed for 18.3, 18.4
and 18.6 (documented:
https://docs.aws.amazon.com/AmazonRDS/latest/AuroraPostgreSQLReleaseNotes/AuroraPostgreSQL.Extensions.html).
The same table lists `plperl`, `pltcl`, `plv8`, `plcoffee`, `plls` and `pg_tle`, and lists no
PL/Python (documented, same page). `pg_tle` supports trusted languages such as JavaScript,
Perl and PL/pgSQL (documented:
https://aws.amazon.com/blogs/aws/new-trusted-language-extensions-for-postgresql-on-amazon-aurora-and-amazon-rds/).
`pgcrypto` provides `hmac()` and `digest()` (inferred, upstream docs). It offers PGP
public-key encryption, not raw RSA, ECDSA or Ed25519 signature verification (inferred,
upstream docs). The AWS page found for column encryption lists the algorithm families
(documented:
https://docs.aws.amazon.com/dms/latest/sql-server-to-aurora-postgresql-migration-playbook/chap-sql-server-aurora-pg.security.columnencryption.html).

**A. HMAC-SHA256 with a shared key in the database.** A key table owned by the migration
owner, with no grants to any workload login. A SECURITY DEFINER function
`enter_scope(token text)` splits the token, recomputes `hmac(payload, key, 'sha256')`, compares
in constant time (inferred: pgcrypto offers no constant-time compare, so compare digests of
both values), checks `exp` and `aud`, then calls `set_config('app.current_traveler_id', ..., true)`.
This alone is not enough: the login can call `set_config` again afterwards. The policy must
check something the login cannot forge, for example a second setting
`app.scope_mac = hmac(traveler || txid, key)` written by the definer function and validated
by a STABLE function inside the policy, wrapped as `(SELECT scope_ok())` so the planner
evaluates it once per statement rather than per row (inferred, needs a plan check).

**B. Asymmetric signature, public key in a table.** The verification key would be harmless to
leak, which is the attraction. The cost is that no documented primitive verifies an ECDSA or
RSA signature in SQL. Options are a pure-JavaScript verifier in `plv8` or `pg_tle`, or the
`aws_lambda` extension calling a verifier Lambda (documented as supported: same extension
page). The first is hand-rolled cryptography in a database function. The second adds a network
hop to every scope entry and makes Lambda availability a database dependency (inferred).
Rejected for this app.

**Who signs.** The Gateway interceptor already holds the verified claim, so it is the
natural signer for the tool path. The backend signing after Cognito verification adds little,
because a compromised backend would sign any traveler. A small signer Lambda with narrow IAM
invoke permission and the key in Secrets Manager is the cleanest separation, and it is also
the one extra component to deploy and monitor. The workflow Runtime needs a token too, so
either the backend passes one down with the Bearer token or the Runtime calls the signer.

**Token contents and lifetime.** `traveler_id`, `aud` (a fixed database audience), `workload`
(the login the token is for), `iat`, `exp`, `jti`, `kid`. A lifetime of 60 to 120 seconds
covers one tool call or one workflow node. Replay inside that window is possible for the
named traveler and workload only. Single-use `jti` tracking would add a write to every scope
entry and is not recommended.

**Key rotation.** Two rows keyed by `kid`, the verifier accepts current and previous, the
signer switches first, the old key is deleted after the longest token lifetime. The key
exists in two places (Secrets Manager and the key table), so rotation is a two-system change
with a window where they disagree.

## Costs

- **Latency.** A Data API call outside a transaction commits on its own (documented:
  https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_ExecuteStatement.html),
  so a pinned scope already needs `BeginTransaction`, the statements, then commit, and a
  transaction times out after three minutes without calls (documented:
  https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_BeginTransaction.html).
  The signed scope replaces the existing `set_config` statement with one function call in the
  same transaction, so it adds no Data API round trip. The in-database cost of one HMAC is
  expected to be far below the Data API call latency (inferred, unmeasured). The signer adds
  one Lambda invocation per request or node unless tokens are reused within their lifetime.
- **Complexity.** A key table, a definer function, a changed policy on every RLS table, a
  signer, a token format, two-system rotation, and tests for every failure case. Every policy
  in `examples/rls_for_agents.sql` and the later migrations changes.
- **Failure modes.** Clock skew rejects valid tokens. A rotation mismatch rejects all scopes
  at once and the failure looks like an RLS denial with zero rows, which is hard to tell from
  an empty result (inferred). A signer outage stops every database access, including the
  read-only presenter views.
- **Blast radius of the key.** An HMAC key forges any traveler. It would sit in Secrets
  Manager, in the signer, and in a table readable by the migration owner, which is the master
  login that already bypasses RLS. That adds a forge-anyone capability to the signer, in place
  of three logins that can each name any traveler today. It narrows the set of workloads that
  can do this, and creates one new secret that can.

## Recommendation

Do not ship it for this talk's reference app; rely on the existing layers (Cognito token
verification at each hop, interceptor plus Cedar on the tool path, `meridian_*` least-privilege
logins, RLS, and the `aws_iam` workload binding as a second check). Confidence: high that it
should not ship in the reference app, because it touches every policy, adds a signer and a
second rotating secret, and the project-B work already closes the identity gap at the HTTP,
Runtime and Gateway layers that the audience can see.

For a production system that holds real traveler data, defer rather than reject. Confidence:
moderate that HMAC verification in `pgcrypto` is worth building once there is a workload
that must not be trusted with every traveler, and low on the exact policy shape until the
plan and latency are measured.

## Smallest prototype that settles the open questions

A scratch database (a local PostgreSQL 18, or a throwaway AWS Aurora cluster after owner
approval), one table with the current RLS policy, and these checks:

1. `CREATE EXTENSION pgcrypto` works for the migration owner and `hmac()` is callable from a
   definer function that a NOINHERIT login can execute but whose key table it cannot read.
2. The policy with `(SELECT scope_ok())` plans as a single evaluation (`EXPLAIN`), and a
   1,000-iteration loop of begin, enter scope, select, commit shows the added time against
   plain `set_config`.
3. Refusals: forged signature, expired token, wrong audience, a valid token for traveler A
   followed by `set_config` to traveler B in the same transaction, and a token replayed in a
   different transaction.
4. Rotation with two `kid` values, and the error a caller sees when the key table is empty.

If check 2 shows a visible cost over the Data API round trip, or check 3 leaves any path
open, the answer stays defer.

## Claims not verified against AWS documentation

Unprivileged `set_config` of custom parameters, `hmac()` and the absence of asymmetric
verification in `pgcrypto`, the `SET ROLE` mechanics, planner behavior of the policy
function, the Data API hiding the IAM caller from the database, and all latency figures are
inferred. No AWS call other than documentation search was made.
