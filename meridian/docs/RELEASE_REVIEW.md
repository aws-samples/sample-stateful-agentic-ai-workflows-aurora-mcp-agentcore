# Meridian release review - September 12, 2026

The L300 review was committed and pushed to `main` at `5ddee61`. These are
source and local-runtime results; this pass did not deploy the hosted
application or AgentCore resources.

## README follow-up

The documentation cross-check found a second write path: Phase 3's supervisor
could delegate a model-selected `process` action to a legacy specialist that
inserted confirmed booking rows directly. That writer and the reference SQL
agent's writer were removed. Phase 3 retains read-only pricing; the supervisor
refuses write actions even if a specialist later regains such a method.

Readiness now allows 45 seconds for scoped catalog/profile reads, and periodic
polling does not cancel an active check. A successful profile read on the flight
connection took about 19 seconds, exceeding the previous 12-second deadline.
The check still times out and reports unavailable data if it cannot complete.

Both READMEs, the agent guide, and the repository map now describe the five
views and governed booking path. Four current screenshots show Concierge,
Solution briefing, Prepared data, and the initial Recovery desk.

Follow-up validation:

- Backend: 422 offline tests passed, 6 skipped, 110 database tests deselected.
  Ruff passed. Regression tests cover rejected write delegation, retained
  read-only pricing, and the SQL/pricing tool allowlists.
- Frontend: 200 tests in 35 files passed; lint, TypeScript, and production build
  passed. New cases cover slow successful readiness and its failure deadline.
- Screenshots: local app, light theme, 1600 × 1000 fullscreen; the preparation
  image is a section capture. Health, catalog, and profile returned HTTP 200.
  No API fixtures, generated mockups, or business writes were used.

Further live backend and AWS rehearsal was deferred until after landing at the
presenter's request. This follow-up does not change the deployment or
business-flow proof boundaries below.

## Initial review changes

- All clicked holds now use Runtime, Gateway policy, and the holds Lambda,
  regardless of the selected ladder phase. The direct SQL fallback was removed.
- Journey listing checks the workload grant and uses the restricted traveler
  scope. Missing or revoked authorization fails closed.
- Cancellation rolls back pending checkpoint writes and drains in-flight Data
  API statements before cleanup. Independent checkpoint reads use bounded
  concurrency; pending-write order is preserved.
- Recovery handoff requires a matching persisted receipt and retains its price,
  duration, party, and total. Trip details show those recorded terms.
- Failed evidence refreshes label the retained observation; changing journeys
  clears old data. IAM and target errors are not mislabeled as Cedar denials.
- Complete local AgentCore configuration skips redundant CLI discovery, and
  transaction-local scope settings use one Data API round trip.
- Solution briefing and the L300 runbook explain the three failure windows and
  the distinction between contextual memory, checkpoints, and business records.
  Dogwood remains an [assessed extension](DOGWOOD_POLICY_ASSESSMENT.md).
- The CDK lockfile uses `js-yaml` 3.15.2. GitHub confirmed Dependabot alert #62
  fixed after the push.

## Validation at `5ddee61`

- Backend: 416 offline tests passed, 6 skipped; 110 database tests deselected.
  Ruff passed for backend, scripts, and tests.
- Frontend: 198 tests in 35 files passed; lint, TypeScript, and production build
  passed. CDK build, one synth test, and formatting passed.
- Frontend and CDK npm audits reported zero findings. The pinned Python
  requirements audit reported zero known vulnerabilities.
- Three selected live Aurora saver tests passed: large pending-write round
  trip, append-failure rollback, and cancellation after an accepted segment
  followed by a successful retry. Fixtures removed their temporary rows.
- A live query confirmed the restricted role, pinned traveler/agent/provider,
  row security enabled, and active RLS.
- Browser checks covered all five views at desktop, projector, and mobile
  sizes. Receipt handoff and confirmation review used an explicit UI fixture
  with no business writes; failed-refresh labeling was also checked.
- The browser read the existing Aurora journey and confirmed booking records
  from September 10. This was historical readback, not a new recovery execution.

The full browser journey readback took 51 seconds on the tested connection.
Treat that as a single observation, not a latency guarantee. Preload evidence
and rehearse on the presentation network.

## Remaining proof and deployment boundaries

Run a fresh process-death rehearsal and the separate lost-response-before-
checkpoint scenario against intended rehearsal inventory before presenting
those claims for this build. Revalidate deployed Cedar behavior after deploying
the relevant source. The hard-kill script alone does not prove the lost-response
window or exactly-once execution.

No security group, public database ingress, TLS verification, IAM policy, or
database permission was changed. Local validation used the existing HTTPS Data
API connection with loopback-only application listeners.

The [README screenshots](../README.md#screenshots) show the current local
interface. Screenshots and source push are separate from hosted deployment or
live business-flow proof.

---

# Meridian release review - September 10, 2026

Production (Phase 4) now runs its tool loop inside Bedrock AgentCore Runtime.
The agent discovers three tools from AgentCore Gateway over MCP with SigV4,
keeps its conversation in AgentCore Memory through the Strands session manager,
and every tool call passes the `MeridianGovernance` Cedar policy engine in
ENFORCE mode before a Lambda runs. A courtesy hold placed with the Hold button
is executed by the platform with pinned arguments and either permitted or
refused by policy; a hold typed into chat is refused because nothing confirmed
it. ADOT spans and application logs carry the trace id shown in the UI.

Later on September 10 the journey was brought home. A held trip is confirmed
the same way it was held: the traveler confirms in the concierge, the backend
reads the booking total under RLS, the runtime executes `confirm_booking` with
the pinned confirmation and ceiling, the `meridian_booking_governance` Cedar
policy decides, and the `confirm_booking` SQL function in Aurora turns the same
booking row from `held` to `confirmed`. Catalog inventory only; no supplier, no
payment. The gateway now serves four tools and the engine holds three policies.

## Verification, September 10

- Backend: 349 offline tests pass (`pytest -m "not database"`), ruff clean.
  Frontend: 178 vitest tests pass, ESLint and `tsc` clean, production build.
  AgentCore CDK: the jest synth test passes with the two targets and the policy engine.
- `scripts/verify_agentcore.py`: Runtime READY, Gateway READY, Memory ACTIVE,
  policy engine `ACTIVE · ENFORCE`, three gateway tools, observability READY.
- `scripts/smoke_production_turn.py` against the deployed runtime: search turn
  (trace `6aa23b994d66c9fe0c16b2fa14f8ab73`), unconfirmed hold denied by
  default with the reason named, confirmed hold `HLD-1A8FF643` held, over-budget
  hold denied with the total and ceiling named. 26 spans for the first trace in
  the runtime log group's `spans` stream.
- Browser, Phase 4 on the ladder as Alex: the Tokyo recall turn shows 23 spans
  including tools/list (three tools), Memory session restored, the runtime's
  own `semantic_trip_search` and `get_package_details` calls, and Runtime turn
  complete with the trace id. Clicking Hold on Tokyo Culture & Cuisine placed
  `HLD-D4063CC1` ($4,998 for two travelers against Alex's $6,400 ceiling from
  the `budget_cap` fact); the earlier attempt with the default $4,000 ceiling
  was refused with "Denied by policy" rendered on the span.
- Phases 1, 2, 3 and 5 exercised against the backend: SQL filters, MCP compare
  and currency conversion, hybrid retrieval with rerank, and the workflow
  paused after an `AuroraDataApiSaver` checkpoint then resumed on the same thread.

- Phase 5 hold routed through the gateway: `scripts/kill_and_resume_demo.py`
  placed its hold through `MeridianHolds___create_courtesy_hold` (Cedar allow,
  the Lambda's workload subject on the span), worker one was SIGKILLed, the
  takeover waited for the lease, and the resumed worker replayed the same
  booking id with the original expiry. Aurora recorded one hold and nine
  checkpoints on the thread.

- Budget shown and budget enforced are now one number. The travel brief used to
  print the profile's `budget_max` of $3,500 with no unit while Cedar judged a
  $6,400 party ceiling derived from the saved `budget_cap` fact of $3,200 per
  traveler, so a confirmed $4,998 trip read as over budget on screen. The memory
  endpoint now returns the saved per-traveler cap, computed by the same helper
  the gateway path uses and read at the same breadth (the endpoint previously
  saw only the eight highest-confidence facts, which excluded the budget one).
  The brief and the confirmation dialog render it through one component as
  `$3,200 per traveler` with `$6,400 for 2 travelers` beneath. The seeded
  `budget_max` was aligned to the cap so the profile column and the saved fact
  cannot drift apart again.

- Booking confirmation through the governed chain: a Phase 4 hold on CTY-002
  (`HLD-8DF6B278`, 5 nights, 2 travelers, $4,998 against the $6,400 ceiling)
  was confirmed through `POST /api/chat/book`. The trace shows
  `tools/call → confirm_booking` with `travelerConfirmed: true` and
  `totalCents: 499800`, then `confirm_booking · result` with
  `cedar_decision: allow`, `cedar_policy: meridian_booking_governance`,
  `policy_mode: ENFORCE` and the Lambda's `traveler_grant: allow`; Aurora
  reported the row `confirmed` at 15:00:19 UTC. A second confirmation of the
  same booking replayed the original record. Migration 010 adds the
  `confirm_booking` function; `scripts/verify_agentcore.py` reports four tools
  and the three policies ACTIVE. Lambda, runtime, backend and frontend tests
  cover the new tool, the pinned booking contract, the route and the
  confirmation dialog.

- Published behind CloudFront with basic authentication: the site answers 401
  without credentials and 200 with them, `/health` reports the durable
  `AuroraDataApiSaver`, and the backend runs on App Runner as a service created
  by `scripts/publish.py` from a bare definition (see
  `docs/AGENTCORE_LEARNINGS.md` for why CloudFormation could not create it).
- Every governed span now names the Cedar decision, the policy that decided
  it and the enforcement mode (`cedar_decision`, `cedar_policy`,
  `policy_mode`), verified on a search, a package read, a permitted hold and
  an over-budget refusal; the identity span says AWS STS when AgentCore
  Identity is not configured.
- Solution briefing surface added to the header; warm near-black dark theme;
  plain white light theme; globe-and-meridian mark; Room check removed;
  activity stepper markers visible in both themes.

Open items from this pass: none.

---

# Meridian release review - September 7, 2026

The code, UI checks, and fresh live rehearsal below pass. The initial review found an expired AWS session in the running backend. After the session was refreshed, restarting Uvicorn restored live catalog and traveler reads. The browser again shows Meridian live. Reconnect retries service reads; restarting the backend reloads its AWS clients.

## What changed

### Closing sequence refinement

Workflow now teaches the pause and checkpoint. **Continue at recovery desk**
carries the same conversation and shortlist into the traveler’s decision view;
it sends no new chat request. The desk places the workflow hold receipt near
the itinerary and leaves detailed checkpoint inspection to the evidence views.

The separate 12-hour direct hold now has an hours/minutes/seconds countdown in
trip details, the Concierge brief, and the desk. Reopening a known active hold
does not place it again. Late hold responses retain their receipt without
overwriting a new phase’s conversation. Direct hold receipts are local to the
app session; Aurora keeps the booking after a page refresh.

System evidence leads to **Session takeaways → Open for questions**, with a
return to the same journey and a link to the sample repository. The close
works in fullscreen while presenter preparation controls remain hidden.

Validation for this refinement: **176 frontend tests in 31 files**, production
build, and ESLint pass. The browser walkthrough checked **24 screen states**
across desktop, short desktop, mobile, both themes, and fullscreen. It found
zero page errors, horizontal document overflows, or axe WCAG A/AA violations.
Controlled API responses verified same-thread resume, a single direct hold
request, and preservation of the workflow trace. Fake-clock tests cover
countdown expiry and remounts. These checks do not represent a new live Aurora
write or a 12-hour wall-clock wait. The live rehearsal below is separate.

### Earlier release audit

| Finding | Correction |
| --- | --- |
| A healthy process could hide failed Aurora reads | The live indicator now requires successful health, catalog, and traveler reads. Refreshes have a deadline, discard old responses, and preserve the conversation. |
| Recovery steps advanced on a timer | A new run waits for saved results. Only a confirmed pause allows the resume view to mark earlier steps complete. |
| Failed requests could claim that nothing changed | Recovery directs the presenter to saved progress before retrying. An interrupted request may have committed work. |
| Failed operations and identity alone could count as proof | Only successful trace events count. Phase 4 requires a runtime invocation and an allowed traveler grant with RLS scope. Unknown checkpoint backends do not count as durable. |
| Missing timings were filled with numbers | Missing durations say “timing not recorded.” The trace total sums recorded spans; overlapping spans are not a wall-clock measurement. |
| Recovery copy invented traveler details or benefits | Dietary notes use saved values. Loyalty recall does not claim a benefit was applied. Missing preferences and travel dates stay missing. |
| An evidence read could remain on “Reading” | Journey reads time out, can be retried, and are canceled when the view changes. Read failures identify older displayed evidence. |
| Two scrolling regions were inaccessible by keyboard | The workspace and continuity panel accept keyboard focus and show an outline. |
| API errors lost useful headers or exposed internal catalog failures | HTTP exceptions preserve response headers. All catalog routes return a consistent, plain 503 response while logging the cause on the server. |
| Presenter instructions described an older app | The guide now starts in Concierge, documents both Aurora savers, distinguishes process health from live access, and describes the current RLS policies. |

## Verification

- **Frontend:** 170 tests in 31 files; TypeScript and production build; ESLint with no warnings.
- **Backend:** 322 offline tests passed, 6 skipped, 109 live database cases deselected. An outbound network guard recorded zero connection attempts. Ruff passed.
- **Security and correctness coverage:** HTTP access controls, traveler grants, ownership on resume, scoped transactions, hold request identity, concurrent execution handling, checkpoint selection, saved journey readback, and late-response cancellation. These are automated code checks, not a new cloud deployment audit.
- **Browser:** all four views in dark and light themes; fullscreen with presenter controls hidden; all five phase disclosures; trip details and Escape dismissal; save/unsave; traveler and date controls; chat; recovery waiting, paused, and resumed states; evidence navigation and keyboard tabs. Layouts checked at 1512 × 982, 1366 × 768, 1024 × 768, and 390 × 844. No page errors or horizontal document overflow were found.
- **Accessibility:** the confirmation scans reported zero axe WCAG A/AA violations. Keyboard and focus checks accompanied the automated scans. This does not replace assistive-technology or actual-projector testing.
- **Scrolling:** long replies and 18 trace events stayed in separate scroll regions. The composer remained visible. Appended replies followed the bottom, while reading older messages preserved the scroll position.
- **Dependencies:** frontend and CDK `npm audit` reported zero known vulnerabilities; GitHub reported no open Dependabot alerts. Application CI also checks the pinned Python requirements and CDK build, tests, and formatting.

Browser interactions used controlled API responses because the AWS session had expired. The outage check used the actual backend; reconnection was then tested with controlled responses. Fixture-based pause/resume verifies UI behavior, not Aurora persistence.

The earlier [live Aurora verification](AUDIT_FIXES.md#verification) remains a separate result: a competing resume returned 409; after SIGKILL, a replacement worker resumed the saved checkpoint; one booking retained its ID and expiry; the stored hold window was 900 seconds. Temporary records were removed. That exercise did not wait fifteen minutes or validate every model response.

## Plain words, L300 evidence

Lead with the short explanation. Open Architecture & evidence or System evidence to show the mechanism.

| Term | Say this | Show this |
| --- | --- | --- |
| Stateful | “Remember what the traveler wants and where the work stopped.” | Saved preferences, conversation, graph values, pending nodes, and the same thread after resume. |
| Governed | “Check who may access this traveler's data, limit the rows, and record the decision.” | Workload identity, traveler grant, transaction-local role and scope, ALLOW/DENY results, and audit rows. RLS does not authenticate the human user. |
| Hybrid retrieval | “Search by meaning and by words, then rank the best matches.” | pgvector candidates, PostgreSQL full-text matches, and Cohere rerank order. |
| MCP | “Give agents named tools with clear inputs and results.” | Tool discovery, arguments, and responses. The local stdio transport is distinct from managed AgentCore integrations. |
| Durable checkpoint | “Save progress outside the worker so a replacement can continue.” | Actual saver kind, committed checkpoint ID, thread ID, and saved next step. Process memory does not qualify. |
| Retry-safe hold | “Retry the same request without creating another hold.” | A stable request ID, transactional hold creation, one booking record, and its original expiry. A worker lease limits competing executions. |
| Fifteen-minute hold | “Aurora stores when this package hold starts and expires.” | Creation time, expiry, database observation time, and a 900-second difference. This is package inventory, not an airline seat or issued ticket. |

Aurora consolidates catalog data, retrieval indexes, traveler records, and workflow state. The application still needs model access, execution services, permissions, and deployment configuration. The phase boundaries describe this sample's configured behavior, not universal limits of SQL or MCP.

## Live follow-up after the session refresh

The primary backend was restarted without changing or exposing credentials. It returned all 35 catalog trips and Alex’s profile with eight facts. A browser check confirmed that the warning cleared and no page errors occurred.

The rehearsal used a separate local backend worker, unique temporary conversation IDs, and real Aurora, MCP, Bedrock, and AgentCore calls:

| Check | Result |
| --- | --- |
| Phase 1: structured SQL | HTTP 200, five trips, no error events. |
| Phase 2: MCP comparison and currency conversion | HTTP 200, three trips, no error events. |
| Phase 3: meaning and keyword retrieval | HTTP 200, five trips, lexical candidates and Cohere reranking observed. |
| Phase 4: saved traveler context | HTTP 200, five trips; traveler grant, scoped RLS read, managed Runtime invocation, and Memory event recorded. |
| Authorization negative control | Alex allowed, Jordan denied; PostgreSQL reported RLS active. Preferences narrowed from 22 rows to 17; interactions from 11 to 10. Counts reflect the rehearsal and can change. |
| Phase 5: recovery | HTTP 200; shortlist saved to an Aurora checkpoint with availability as the next node. |
| Replacement worker | The rehearsal worker was stopped after its committed pause. A new process resumed the same thread; the saved resume receipt and successful second execution were read back from Aurora. |
| Package hold | Exactly one hold for two travelers; expiry minus creation time was 900 seconds. |

This follow-up used a graceful process restart after the pause. The earlier SIGKILL test remains the separate crash-recovery proof. Neither test waits fifteen minutes. The five initial requests took roughly 0.5, 1.4, 14.3, 18.2, and 16.2 seconds; resuming on the replacement worker and reading back its evidence took about 52 seconds in this run. These are observations, not latency guarantees.

Temporary Aurora journey, checkpoint, booking, conversation, and interaction records were removed, with zero remaining rows verified. The rehearsal’s managed Memory event and session-scoped extracted records were also removed. Access audit records were retained.

The remaining presentation check is in the actual room: confirm a price and an evidence label are readable from the back, with projector readability enabled. The software and live-service rehearsal are complete for this review.
