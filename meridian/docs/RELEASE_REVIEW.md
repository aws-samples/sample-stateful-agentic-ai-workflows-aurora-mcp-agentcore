# Meridian release review - September 10, 2026

Production (Phase 4) now runs its tool loop inside Bedrock AgentCore Runtime.
The agent discovers three tools from AgentCore Gateway over MCP with SigV4,
keeps its conversation in AgentCore Memory through the Strands session manager,
and every tool call passes the `MeridianGovernance` Cedar policy engine in
ENFORCE mode before a Lambda runs. A courtesy hold placed with the Hold button
is executed by the platform with pinned arguments and either permitted or
refused by policy; a hold typed into chat is refused because nothing confirmed
it. ADOT spans and application logs carry the trace id shown in the UI.

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

Open items from this pass: the Phase 5 workflow hold still writes to Aurora
directly (agreed to route it through the gateway next), and the CloudFront and
App Runner deployment in `infra/` is being brought up.

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
