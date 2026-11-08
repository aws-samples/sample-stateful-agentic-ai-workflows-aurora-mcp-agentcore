# DAT307-R1 implementation review and readiness

Reviewed against **Build stateful agentic AI workflows with Aurora, MCP, and AgentCore**, Level
300. The application covers all five phases and contains the mechanisms the abstract describes.
The fixes in this branch close confirmed failure, inventory, privilege and evidence gaps. It is
ready for a local technical rehearsal with the checks below passing; the upgraded AWS release is
**not yet verified**. Complete the [coordinated release](DAT307_RELEASE.md) and capture fresh live
receipts before describing these guarantees as demonstrated on the deployed application.

## Scope and evidence

Static inspection follows the live UI paths, agent definitions, Gateway schemas, interceptors,
Cedar, database roles, SQL functions and deployment templates. Regression tests use blocked
network access. Local PostgreSQL tests execute the real migrations and business functions,
including concurrent transactions; they are stronger than call-log mocks but do not exercise
Aurora Data API transport or AgentCore's hosted policy engine.

Existing screenshots, Gateway event fixtures and recordings are historical evidence. Their
original content remains intact. Updated tests against a recorded event shape verify today's
handler, not acceptance of today's response by a live service. Application spans are annotations;
a successful target reply is an inference that Gateway permitted the call. No application field
should be described as a service-issued Cedar receipt.

## Local validation results, 7 November 2026

- Python suite: **6,414 passed, 16 skipped, 374 live-database tests deselected**. The 25 local
  PostgreSQL tests ran with PostgreSQL 18 and pgvector in an owned socket-only cluster.
- Frontend: **760 unit tests** and **64 browser/accessibility checks** passed; lint, typecheck
  and production build passed. The final evidence-wording change also passed its focused tests.
- AgentCore CDK: **18 existing synthesis tests** and **4 new Lambda auth-mode tests** passed;
  TypeScript build and formatting checks passed.
- Both isolated Runtime dependency contracts passed, including entry-point imports and
  the Concierge's IAM/JWT client construction. Their full manifests and the backend lock audit
  reported no known vulnerabilities. Node audit gates passed with existing low/moderate tooling
  advisories retained in [DEPENDENCIES.md](DEPENDENCIES.md).
- Both v6 decks passed OOXML validation and full 41-slide PowerPoint PDF export. Edited visible
  slides were inspected, and movie frames verified the changed build slides. Media and timing
  XML are unchanged; speaker notes retain their original structure with targeted corrections.

The local Python interpreter was 3.13.14; CI/deployment use the repository's 3.13.18 pin. No AWS
calls, live migrations, deployments or current-release recordings were performed in this pass.

## Five-phase implementation map

| Phase | Live entry and agent | Tools and database | Deployment and evidence |
| --- | --- | --- | --- |
| 1. SQL | `backend/routers/chat.py:sql_search`; reference definition in `backend/agents/phase_01_sql/` | Parsed filters; parameterized reads of `trip_packages` through `backend/db/rds_data_client.py` | FastAPI backend; SQL/parameter spans. The UI path is deterministic, not model-generated SQL. |
| 2. MCP | `chat.py:mcp_search`, `_call_domain_tool`; reference definition in `phase_02_mcp/` | `backend/mcp/concierge_server.py`: search, compare, price range, inventory, conversion and loyalty; catalog and traveler profile reads | Local stdio MCP server. `test_phase2_domain_selection.py`, `test_span_timings.py` and MCP tests cover argument/result handling. |
| 3. Retrieval | `phase_03_retrieval/agent.py`, shared `concierge_definition.py` | MCP reads plus local hybrid/detail tools; `backend/retrieval/hybrid.py`: embeddings, pgvector, full-text, merge, hydrate, filters and rerank | Backend Strands agent and Bedrock; catalog Lambda mirrors retrieval for later phases. Hybrid and catalog parity tests, plus real SQL exclusion tests. |
| 4. Governed application | `phase_04_production/production_agent.py` invokes `meridian_agentcore/app/MeridianConcierge/main.py` | Runtime tool calls through Gateway to `meridian_catalog` and `meridian_holds`; profiles, preferences, grants, conversations, bookings and audits; AgentCore Memory for session context | `agentcore.template.json`, CDK, JWT interceptor and Cedar. Runtime hooks and exact-dependency MCP contract checks; deployed acceptance remains a release gate. |
| 5. Durable workflow | `backend/agentcore/workflow_runtime.py` invokes `MeridianWorkflow/main.py`; `runtime_entry.py`, `runner.py`, `graph.py`, `nodes.py` | Gateway search/holds/releases; `workflow_snapshots`, journeys, executions, review pauses, stable hold requests and committed-result lookup | Separate Runtime with service-login secret, Aurora snapshot storage and worker leases. Graph, runner, ownership, saved-step and transaction tests. |

Confirm the exact paths with [STRUCTURE.md](../STRUCTURE.md) and the
[code walkthrough](CODE_WALKTHROUGH.md). The phase folders contain teaching definitions as well
as executable paths; the live router is authoritative for Phases 1 and 2. Each phase adds a
capability, but it does not literally execute all earlier agent definitions.

## Prioritized findings and fixes

All items below were confirmed by tracing code. “Fixed” means changed and locally testable in
this branch; migration and cloud-dependent behavior still require the release gates.

| ID / severity | Trigger and impact | Source and fix | Evidence |
| --- | --- | --- | --- |
| F1 **High**, fixed | The commit succeeds but its acknowledgement or Runtime reply is lost. Generic failure text could complete the workflow or describe a refused action, encouraging a new request identity. | [meridian_holds/lambda_function.py:244](../meridian_agentcore/agentcore/gateway_targets/meridian_holds/lambda_function.py#L244) returns explicit unknown outcomes for commit exceptions; [production_agent.py:349](../backend/agents/phase_04_production/production_agent.py#L349) preserves uncertainty; [nodes.py:716](../backend/agents/phase_05_workflow/nodes.py#L716) raises `HoldOutcomeUnknown` for unclassified failures. Retry uses the original intent and committed-result lookup. | `test_action_audit_postgres.py`, lost-reply cases in `test_production_hold.py`, `test_workflow_runner.py` and `test_holds_lambda.py`. |
| F2 **High**, fixed | An empty reviewed shortlist or a missing availability row could fall back to a different candidate or an unverified duration. | [nodes.py:236](../backend/agents/phase_05_workflow/nodes.py#L236) preserves the reviewed candidate set even when empty; [nodes.py:327](../backend/agents/phase_05_workflow/nodes.py#L327) empties missing inventory; `packages.py:duration_for_party` treats explicit empty counts as unavailable. | `test_workflow_nodes.py`, `test_workflow_packages.py`; no hold call for missing inventory. |
| F3 **High**, fixed | Confirmation and allocation use different locks, or confirmation relies on transaction-start time after waiting. A late confirmation can conflict with reallocated capacity. Missing agent scope can also pass a SQL NULL comparison. | Migration [026_serialize_booking_transitions.sql:42](../scripts/migrations/026_serialize_booking_transitions.sql#L42) fails closed on NULL agent scope; lines 90 and 184 serialize both transitions on the same inventory lock and use wall-clock expiry after lock acquisition. | `test_booking_transactions_postgres.py:23` and concurrent confirmation/allocation tests execute real transactions. |
| F4 **High**, fixed in staged rollout | A service that can switch to `meridian_app` can directly mutate booking rows, bypassing bounded business functions. | Migrations 027 and 029 move business-function execution to the Gateway login and revoke direct booking DML; `meridian_holds/lambda_function.py:227` performs bounded role switching within the same transaction. | Real PostgreSQL tests deny direct DML for app/backend/workflow/gateway and deny business functions to backend/workflow. Apply 029 only after deploying the new Lambda. |
| F5 **Medium**, fixed with live correlation pending | A process dies before final turn audit persistence; the booking survives without a durable caller/action link. Successful spans can also overstate their policy evidence. | Migration [028_booking_action_audit.sql:1](../scripts/migrations/028_booking_action_audit.sql#L1) and `_record_action` at [lambda_function.py:319](../meridian_agentcore/agentcore/gateway_targets/meridian_holds/lambda_function.py#L319) commit the action row with the booking. Interceptor binds subject and correlation reference; Cedar checks subject equality. Trace labels distinguish inference from Gateway refusal responses. | Real PostgreSQL lost-ack and audit-insert failure tests; identity and Cedar tests; exact Runtime contract. Native service correlation requires fresh AWS evidence. |
| F6 **Medium**, fixed | Vector retrieval can reintroduce a full-text exclusion; a catalog row disappearing between retrieval and hydration can leave stale data. | [backend/retrieval/hybrid.py:103](../backend/retrieval/hybrid.py#L103) extracts explicit exclusions and `_hydrate` at line 306 applies them after merging both sources; missing catalog rows are dropped. Catalog Lambda mirrors the behavior. | `test_hybrid_retrieval.py`, `test_hybrid_constraints_postgres.py` and catalog parity tests. Natural-language suitability remains a relevance judgment. |
| F7 **Medium**, fixed | Phase 2 silently substitutes default locations, flagship packages or an invented conversion amount for underspecified requests. | [backend/routers/chat.py:593](../backend/routers/chat.py#L593), `:609`, `:618` require explicit amounts, named package IDs or an explicit trip-type comparison. Unknown locations prompt for clarification. Empty comparison never converts an invented price. | `test_phase2_domain_selection.py`, router tests and timing tests. |
| F8 **Medium**, fixed | Retrying an expired or released intent automatically derives a new request ID, creating a fresh hold without new consent. | Removed request-ID chaining from `meridian_holds/lambda_function.py`. The existing UI persists one conversation intent before dispatch and reads its receipt on retry. A new deliberate action receives a new intent. | Hold-ledger tests assert the original request ID and expiry across expired/released retries; committed-result tests assert one booking. |
| F9 **Medium**, fixed | Setup/reset guidance applies unrelated pending migrations, or a reset fails on the new audit foreign key. Runtime tests also use a different dependency environment from deployment. | [scripts/apply_migrations.py:151](../scripts/apply_migrations.py#L151) adds first-pending `--only`; reset removes selected dependent rows transactionally and warns about evidence deletion. `check_runtime_contract.py` checks exact Runtime pins and real MCP/Strands APIs. | Migration CLI tests, presenter lifecycle/reset tests, two isolated Runtime environments. New CI job runs the Runtime contracts independently. |

## Identity and authorization trace

1. Cognito's pre-token trigger maps the authenticated subject to one traveler in
   `traveler_identity_bindings`. The backend verifies the token and resolves `me` from the claim.
2. In JWT mode, backend-to-Runtime and Runtime-to-Gateway calls forward the access token.
   Runtime and Gateway authorizers validate it. Claim helpers inside the process decode a token
   already verified by those authorizers; they are not independent signature validators.
3. Runtime hooks pin traveler and subject arguments from the authorized turn. The Gateway
   interceptor overwrites traveler, subject and correlation reference again. It logs bounded
   binding identifiers, never the token.
4. Cedar defaults to deny. Its binding forbid rule compares both traveler and subject arguments
   with principal tags, independently of the interceptor. Action permits constrain confirmation,
   quantity, hold duration and budget arguments. The Lambda checks saved prices/budget and its own
   workload grant, so caller-supplied ceilings do not replace stored business rules.
5. The Lambda uses a restricted database login. It pins role, traveler and agent settings inside
   one Data API transaction. SQL functions validate scope, ownership, lease, amounts, inventory
   and expiry. All queries for that action, its audit row and its commit share the transaction ID.

A valid second-traveler token is not an invalid token. It can access its own permitted data.
It cannot select Jordan by forging a body field; a Gateway request may instead be rebound to
its own traveler, where the holds workload grant denies it. Missing required claims fail closed.

**Trust boundary:** direct privileged Lambda invocation and service database credentials are
administrative capabilities. Neither the Lambda nor PostgreSQL validates a forwarded JWT
cryptographically. An administrator can change grants, policies or records. IAM restrictions
and non-owner service logins are part of the guarantee, not optional deployment details. Default
IAM mode authenticates the workload and trusts its traveler input; use JWT mode for the session's
end-user identity demonstration.

## Claim-to-evidence table

| Session promise | Assessment | Evidence and remaining condition |
| --- | --- | --- |
| Five phases from SQL to durable workflow | **Supported in code** | Live router, local MCP server, retrieval agent, two managed Runtime entry points and phase guides. Teach the deterministic first two phases explicitly. |
| Aurora unifies relational, vector, text and workflow state | **Supported in code and local SQL** | Schema, hybrid pipeline and snapshot storage. AgentCore Memory also stores Concierge session context, so not all state is exclusively in Aurora. |
| Strands on AgentCore | **Supported in source and isolated contracts** | Actual Strands Graph pause/resume and MCP client under Runtime pins. A new deployed revision still needs a live invocation receipt. |
| Verified traveler identity and RLS | **Partially verified** | Backend validation, JWT deployment config, interceptor/Cedar tests and actual PostgreSQL roles. Verify deployed authorizers, principal `sub` tag support, secrets and grants. |
| AgentCore Policy governs tool calls | **Partially verified** | ENFORCE configuration, schemas and local Cedar allow/deny cases; historical Gateway fixtures. Current revised policy acceptance and denied execution require service evidence. |
| Recovery from saved steps | **Supported locally; live reproof required** | Fresh graph over retained storage resumes without repeating completed search. Lease and persistence failure tests cover replacement ownership. Re-record a current-release session stop/resume. |
| Idempotency prevents duplicate actions | **Supported for database actions** | Stable request identity, constraints, committed lookup, shared inventory locks, real PostgreSQL lost-ack and replay tests. No exactly-once claim for arbitrary external services. |
| Audit makes outcomes traceable | **Supported application mechanism; service correlation pending** | Atomic action audit plus booking rows; best-effort final turn annotations; native request IDs for log correlation. Hard kills can omit final turn rows. |
| Production deployment | **Reference deployment provided** | CDK and release tooling, least-privilege cutover and acceptance matrix. Production operational readiness is not established by synthesis or unit tests. |

## Failure and misuse matrix

| Scenario | Expected outcome | Current evidence / needed proof |
| --- | --- | --- |
| Valid other-traveler token plus forged body | Refuse mismatch or bind to token traveler; no Jordan rows | Backend, Runtime, interceptor and Cedar tests; repeat each real entry point in identity proof. |
| Missing/expired/forged JWT or absent claims | Fail closed before protected access | Token-validation and interceptor tests; verify live authorizer configuration and keys. |
| Direct service-role booking write | Permission denied after 029 | Real PostgreSQL role tests; repeat with each deployed service secret. |
| Same request concurrently | One booking; loser observes the committed result or retries the same ID | Existing hold-ledger/ownership tests and SQL constraints; repeat concurrent Data API calls. |
| Stop before business write | No booking; resume performs the remaining action once | Workflow runner and graph tests; session-stop live exercise. |
| Stop after commit before reply | Unknown response; original booking, expiry and request ID survive | Real PostgreSQL lost-ack test and Phase 4/5 recovery tests; live lost-response proof. |
| Fail while saving a step | Last durable snapshot remains; replayed action finds committed result | Storage/graph/runner fault tests; target Aurora transport fault exercise. |
| Two workers / expired lease | Only current owner may commit; stale worker refused | Lease and SQL guard tests; live replacement worker receipt. |
| Confirmation waits past expiry | Expired hold cannot become confirmed | Local concurrent PostgreSQL tests; repeat target engine/Data API scenario. |
| Audit insertion fails | Booking, lines and hold identity all roll back | `test_action_audit_postgres.py`; no independent post-commit audit dependency. |
| Policy denial | No target execution or action row; preserve actual Gateway refusal | Local Cedar and error classification; native denied-request evidence pending. |
| Missing data / empty shortlist / explicit exclusion | No substitution, fabricated balance, phantom availability or hold | Domain, loyalty, workflow and hybrid tests. |
| Admin reset | Only selected owned records and action audits removed together; rollback on failure | Presenter/reset tests; never reset until live receipts are captured. |

## Remaining risks, unverified claims and optional work

- **Unverified deployment:** no AWS resources were changed or queried during this fix pass.
  Required evidence is a clean-revision deployment receipt, read-back of authorizers/policies and
  migration state, and the acceptance matrix's stored rows and native log correlations.
- **Plausible operational risk:** token expiry, long-running turns and log retention can affect
  recovery or evidence availability. Existing failure paths preserve intent, but rehearsal must
  exercise token renewal and the account's actual log configuration. Do not label every deny as
  a Cedar decision or assume every failure creates a database audit row.
- **Validation limit:** local PostgreSQL is not Aurora, and the local Python patch version may
  differ from the repository's deployment pin. CI and the target-engine tests must pass before
  release. The dependency contract script verifies the Runtime package versions, not the AWS
  service's runtime image.
- **Optional follow-up:** add managed PostgreSQL CI coverage on the target major version, export
  immutable audit evidence if retention requirements demand it, and measure retrieval quality on
  a labeled dataset. These do not justify replacing the current workflow or authorization design.

## Rehearsal and presentation plan

**Must complete before rehearsal:** run local checks, deploy migrations 026-028 and updated
components in order, verify the new caller-binding policy, apply 029, then prove successful and
denied actions with stored rows. Preserve the original v5 decks and speaker notes. v6 should
correct the session code, distinguish inferred authorization from native evidence, and label
the separate confirmation screenshot as illustrative.

**Should complete before re:Invent:** capture fresh clips from the committed release; rehearse
stop/resume and lost-reply as separate cases; verify the target Aurora engine, model/Region
access, Cognito token renewal and reset sequence. Inspect the v6 PowerPoint render and builds on
the presentation machine. Keep a fallback recording with its source revision and date.

**Optional follow-up:** broader retrieval evaluation, target-version PostgreSQL CI, retention
exports and load testing. Present these as operational extensions rather than demonstrated
features of this reference application.

For L300, teach one boundary per beat: token identifies traveler, policy permits the tool,
transaction commits the action, snapshot remembers progress. Use the same request/booking IDs
through the recovery example. Show the trace and database receipt together, then state exactly
which assertion each proves. This keeps the advanced material accurate without narrating every
configuration field on stage.
