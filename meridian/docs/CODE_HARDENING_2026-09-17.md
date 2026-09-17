# Meridian code hardening - September 17, 2026

Local remediation of the [chalk-talk audit](CHALK_TALK_AUDIT_2026-09-17.md), based on `c9fd62f0d23964ee57fb7883d65aaa55653586a9`. This report records validation before source publication. No infrastructure change or hosted deployment was part of that validation; a subsequent source commit or push does not establish hosted parity.

The full capability ladder, managed concierge, direct hold, booking confirmation, and durable recovery remain available. The delivery guide now budgets 40 minutes for slides, code walkthrough, and demonstration within the 60-minute session. Optional branches remain available for discussion.

## Correctness and recovery

- Phase 4 availability respects the traveler-context switch. Context off returns before memory access; context on uses managed Runtime and Gateway.
- Recovery displays only its own Phase 5 results and one explicit journey/thread. SQL results cannot become recovery evidence, and an arbitrary latest journey cannot supply success indicators. Saved recoveries are an explicit choice.
- The browser allocates the recovery address before dispatch. A stopped wait, lost response, or reload retains that address. Readback precedes resumption. Duplicate starts are rejected both before and after acquiring the execution lease, protecting the read/claim race.
- Chat, hold, and confirmation have a 55-second browser deadline, elapsed waiting, and a Stop waiting control. Runtime uses one SDK attempt with a 45-second socket read timeout. These bound the client wait, not the complete server workflow; stopping does not cancel a transaction.
- Direct holds persist intent identifiers before dispatch. Reload and retry use authenticated, traveler-scoped receipt reads. A committed hold is recovered before a new write is considered. Read failure does not trigger a speculative write. Storage failure prevents dispatch when the retry identity cannot be saved.
- Confirmation first reads the recorded booking, so an acknowledgement lost after confirmation does not cause another confirmation request. Recorded prices, quantities, status, and expiry remain authoritative. Readback timestamps carry explicit UTC offsets.
- Workflow completion prose comes directly from saved operational state. It includes the recorded hold outcome and distinguishes package inventory from flight seats. A second model no longer rewrites the receipt, offers to create an already-created hold, or adds another wait after workflow completion.

## Access and UI polish

Catalog routes, API schema/docs, and detailed health now use the application's HTTP principal boundary. Public `/health` returns process liveness only. A revoked traveler grant returns a safe 403, including on receipt reads. Readback uses existing database permissions; no grants were broadened.

Removed timer-driven trace completion and irrelevant SQL memory steps. Recovery distinguishes waiting, saved state, unknown outcomes, and observed receipts. Recalled preferences are labelled as context for review rather than proof that the selected hotel or itinerary satisfies them.

Top-level view changes reset the workspace position after the transition. The narrow-screen continuity rail stacks below the workspace, and navigation labels scroll within their own space instead of overlapping service status. Solution briefing retains the full architecture and adds readable Trusted context, Governed action, and Durable recovery views.

## Validation

- Backend: **459 passed**, 6 skipped, 110 database tests deselected. This is the isolated automated suite, not a claim that all database-marked tests ran.
- Frontend: **219 passed across 36 files**. Final copy-only changes were checked again: 35 component tests passed.
- The first source-publication CI run exposed a storage-failure test mock that did not intercept jsdom's `Storage` proxy. The mock now targets the method owner and asserts it was called. All 219 tests passed again with jsdom storage; all 9 booking-flow tests also passed with the newer-Node storage fallback. Application behavior was unchanged by this test correction.
- TypeScript/production build, frontend lint, backend Ruff, and whitespace checks passed.
- Regression coverage includes context-off availability, managed dispatch, deadlines covering response-body reads, caller cancellation, unknown-outcome retry blocking, lost hold/confirmation responses, reload restoration, storage failure, expired receipts, revoked grants, duplicate starts, and stale journey isolation.
- Browser checks covered actual SQL-to-recovery navigation, Stop waiting with retained identity, restart/readback, a recorded recovery hold, receipt restoration after full reload, view positioning, focused architecture, and narrow layouts. No console warnings/errors were present at the final browser check. A physical projector/back-row check is still a separate rehearsal.

## Fresh live evidence

The local candidate used the configured Aurora database and managed AWS services; the hosted release was not changed.

| Check | Observed result |
| --- | --- |
| Phase 4 availability, context off | Returned the context-off guard with no facts or managed invocation |
| Phase 4 availability, context on | Runtime and Gateway search/detail tool traces; response in 25.25 seconds |
| Browser recovery | Saved shortlist, replacement-process resume, one hold for two travelers, recorded total $3,898 |
| Direct 12-hour hold | One recorded hold for one traveler, $1,949; response in 19.61 seconds |
| Direct-hold reconciliation | Same booking ID, terms, amount and original expiry; read in 1.63 seconds |
| Booking receipt read | Recorded two-traveler recovery hold, explicit UTC timestamps; read in 1.53 seconds |
| Injected lost Gateway response on final workflow code | Real hold committed; replacement worker resumed the same checkpoint; one hold, same request/booking IDs and unchanged expiry |
| Cedar refusal in that rehearsal | Unconfirmed and over-budget calls both denied |

One earlier browser resume failed when lease renewal exceeded its timeout. The lease guard stopped execution; the saved journey remained readable and subsequently resumed successfully. Fresh evidence reads took about 3-4 seconds. Instrumentation did not reproduce the earlier stall, so its cause is **unresolved**, not a proven fix. The retained attempt history is evidence that recovery worked after the failure, not a latency guarantee. Keep a cold/warm run on the presentation network in preflight.

The final lost-response rehearsal is an explicit injected acknowledgement loss after a real Gateway commit. It is not presented as an observed AWS outage. Its fixture cleaned itself up. The separately created browser-recovery and direct-hold fixtures were archived and removed with exact ID, owner, creation-date, and inactive-execution checks. Existing journeys/bookings were not modified. Temporary preview servers were stopped and the browser viewport was restored. The pre-existing servers on ports 8013/5176 were left running; restart that backend before using the updated frontend with the new receipt routes. Authorization audit records and isolated managed-memory test sessions remain as normal test evidence.

Supporting evidence and test output are under `meridian/.local/code-hardening-2026-09-17/` (ignored by Git).

## Remaining delivery gates

1. Release this reviewed candidate and verify both frontend/backend identity, origin authentication, and response headers through CloudFront. The earlier audit found hosted source drift; local fixes do not update that release.
2. Rehearse the complete 40-minute spoken route with slides, source, and demo on the intended network and projector. The timing table is a plan, not a measured presentation duration.

No software can be certified “bulletproof” by one audit. The observed source defects are corrected, failure paths have regression coverage, and the critical recovery claim has fresh live evidence. Hosted parity and room-scale delivery remain explicit gates.
