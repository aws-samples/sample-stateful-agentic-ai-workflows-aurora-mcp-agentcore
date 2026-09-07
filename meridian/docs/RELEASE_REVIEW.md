# Meridian release review - September 7, 2026

The code and UI checks below pass. A fresh live rehearsal is still required: the local AWS session expired during this review. Catalog and traveler reads failed while the backend's process health remained green. The app now reports that condition correctly and offers Reconnect.

## What changed

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

## Final live gate

Refresh the backend's AWS session, select Reconnect, and confirm catalog and profile reads. Then rehearse the five phase prompts, the authorization negative control, and same-thread recovery with real services. Check the price and an evidence label from the back of the actual room. Until that passes, the repo is verified for code and UI behavior; the live demo is not fully signed off.
