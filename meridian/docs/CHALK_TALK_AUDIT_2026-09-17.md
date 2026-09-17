# Meridian chalk-talk audit - September 17, 2026

Audited clean `main` at `c9fd62f0d23964ee57fb7883d65aaa55653586a9` for a **60-minute chalk talk with 40 minutes of core content**. This supersedes the 45-minute content recommendation for this audit. It is a presenter-led session, not a participant workshop or booth demonstration.

This is the original audit snapshot. The subsequent [code hardening report](CODE_HARDENING_2026-09-17.md) records the implemented fixes, fresh validation, and remaining delivery gates; use it for the current remediation status.

**Assessment:** the core recovery implementation has convincing fresh evidence, and the UI has a coherent visual direction. Fix the memory-control and journey-evidence defects before the next complete rehearsal. Reconcile the hosted release before using CloudFront on stage. The content fits 40 minutes if the optional paths are deliberately removed; speeding through every existing demonstration would weaken the central lesson.

This is an audit and proposed delivery plan. Application source was not changed, committed, pushed, or deployed. The new deliverable is this report; supporting run evidence is saved locally under `meridian/.local/chalk-talk-audit-2026-09-17/`.

## Findings, in priority order

### 1. P1 - An availability question bypasses the traveler-context switch

**Reproduced against the live local backend.** Send Phase 4 `What durations are available for Tokyo Culture & Cuisine?` with `memory_enabled: false`. The response returned **12 saved traveler facts**, used the dietary preference and saved Tokyo dates, and took the local PackageAgent/Bedrock-polish path. Its trace contained no AgentCore Runtime or Gateway invocation.

The availability branch runs before the Phase 4 memory-off guard, and recalls preferences whenever `phase == 4`. This contradicts the switch and the documented managed Phase 4 path. It does not demonstrate a cross-traveler authorization bypass: the HTTP binding and scoped database read still apply.

**Fix:** apply the Phase 4 context decision before any specialist dispatch. Prefer keeping this local availability shortcut in Phase 3 and routing Phase 4 through its managed Runtime path. Add coverage for availability requests with context off, and for the actual execution path with context on.

**Acceptance:** context-off availability reads no preferences or conversational memory; returns no memory facts; and performs no memory writeback. Phase 4 responses and their traces identify the path that actually ran.

Source: [chat router](../backend/routers/chat.py), lines 2174-2243; the context-off guard is at 2261. Existing coverage in [workflow-transition tests](../tests/test_chat_workflow_transition.py) checks a recall question, not this availability branch. Evidence: `phase-4-3.json` and `live-probes.json` in the local audit folder.

### 2. P1 - Recovery can combine unrelated results with historical journey proof

**Reproduced in the browser.** Run the Phase 1 city-trips query, then open Recovery desk. The page said “Recovery ready to start,” while its briefing called **Barcelona Architecture Week** the best result of a live recovery search. The continuity rail simultaneously displayed a previous Tokyo journey with a persisted checkpoint and completed resume. Starting a new recovery also left that old journey's success visible while the new request was running.

Two sources are being combined: shared chat/recommendation state and a journey reader that defaults to the most recent saved journey when no workflow thread is supplied. The historical record is real, but it is not evidence for the displayed SQL request or the new in-flight recovery.

**Fix:** bind the briefing, checks, receipt, and continuity rail to one explicit recovery identity. Do not reinterpret an arbitrary latest chat as a recovery. Keep “open a saved journey” distinct from “start a new recovery”; clear or clearly label historical proof while a new journey is being established.

**Acceptance:** SQL → Recovery has no recovery result until one is started or explicitly restored. A fresh recovery never displays an earlier journey's green checks. A URL containing a saved journey still restores that exact journey after refresh.

Sources: [desktop shell](../frontend/src/showcase/DesktopMeridianApp.tsx), lines 95-122 and 561-567; [journey reader](../frontend/src/showcase/journey/useJourney.ts), lines 143-170; [recovery briefing](../frontend/src/showcase/components/RecoveryBriefing.tsx), lines 23-55 and 131-136.

### 3. P1 for hosted delivery - CloudFront does not contain the reviewed release

**Verified with current AWS reads.** The deployed S3 index still references the earlier `index-DGyQg8oy.js` bundle, matching the prior release evidence. The current local build emits `index-ChNQyGIY.js`. The live distribution has **no response-headers policy attached** to its default, `/api/*`, or `/health` behaviors, although the current source attaches one to all three.

This is a release gap, not a failing local synthesis test. In particular, the September 13 retry/UI fixes and browser-security policy must not be attributed to the hosted application merely because the current source passes CI.

**Fix:** after the code corrections, deploy a reviewed candidate, verify its frontend and backend identities, verify protected HTTP behavior and headers, and repeat the chosen recovery sequence through CloudFront.

**Acceptance:** exact candidate assets and backend image are verified; policy attachments and response headers are present; the canonical hosted journey completes. No deployment was performed during this audit.

Sources: [hosting stack](../infra/lib/meridian-web-stack.ts), lines 136-185; prior [release review](RELEASE_REVIEW.md). Fresh reads used CloudFront `GetDistribution` and S3 `GetObject`, rather than assuming historical evidence was current.

### 4. P2 - Slow requests need an explicit presenter escape path

**Source-confirmed risk; no artificial timeout was injected in the hosted service.** Chat has an abort controller but no request deadline or visible stop-waiting control. Hold and confirmation fetches have neither a signal nor a deadline. While one is pending, the loading state disables further actions. Readiness and saved-journey reads already have bounded waits, so the behavior is inconsistent.

The hosted origin read timeout is **60 seconds**, while the AgentCore client permits a **180-second** read timeout and retries. The browser chat response is assembled before returning, so it does not emit progress bytes to keep the CloudFront request alive. A slow managed call can therefore outlive the outer request.

**Fix:** define one end-to-end waiting policy, show elapsed waiting and an explicit next step, and preserve the operation identity when the outcome is unknown. For workflow or hold timeouts, re-read persisted state before retrying. Stopping the browser's wait must not claim to have canceled a committed action. A larger asynchronous execution redesign can wait unless rehearsals show it is needed.

**Acceptance:** a stalled response reaches a bounded, recoverable state; a delayed or lost hold acknowledgement still reuses the same intent; the presenter can move to the recorded fallback without refreshing away the request identity.

Sources: [showcase hook](../frontend/src/showcase/hooks/useMeridianShowcase.ts), lines 558-644 and 745-889; [API client](../frontend/src/api/client.ts), lines 128-141 and 208-237; [Runtime adapter](../backend/agentcore/runtime.py), lines 142-151; [CloudFront origin](../infra/lib/meridian-web-stack.ts), lines 159-162. AWS documents that a POST connection is dropped when the origin read timeout elapses: [CloudFront custom-origin behavior](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/RequestAndResponseBehaviorCustomOrigin.html).

### 5. P2 if retained - The separate 12-hour hold does not survive browser refresh in the UI

The pending retry identity is held in a React ref, and direct-hold receipts in component state. A full reload loses both; Aurora retains the booking. A new click can consequently start a new intent instead of recovering the first one. This known limitation is still present in current source and documented in the presenter guide; this audit did not create duplicate live holds to demonstrate it again.

**Recommended scope decision:** remove the separate 12-hour hold demonstration from the 40-minute core. Use the checkpointed 15-minute recovery hold. If the standalone action remains part of the presented product flow, persist and reconcile its identity and receipt across reloads before relying on it.

Sources: [showcase hook](../frontend/src/showcase/hooks/useMeridianShowcase.ts), lines 257-261 and 745-785; [presenter guide](PRESENTER_GUIDE.md), lines 241-252.

### 6. P2 - Direct-origin access is broader than the hosting documentation claims

**Verified anonymously against the deployed App Runner origin:** `/api/products?limit=1`, `/health`, and `/openapi.json` returned HTTP 200. The traveler-memory route returned 401, and CloudFront's `/health` returned 401. The sensitive traveler boundary worked in these checks; the origin is not uniformly protected by the edge password or bearer requirement.

**Fix:** either protect catalog and schema routes at the origin as intended by the current “refuses anonymous callers” description, or explicitly document that only traveler/action routes are protected. Keep a deliberately minimal health endpoint if operationally required. Sample catalog data being readable is not evidence that traveler records or booking actions are exposed.

**Acceptance:** direct-origin route behavior matches the documented access boundary. Evidence: `anonymous-origin.json` in the local audit folder. Sources: [catalog router](../backend/routers/products.py), [HTTP authentication](../backend/http_auth.py), [application setup](../backend/main.py), and [operations guide](OPERATIONS.md).

### 7. P2 for presentation - View changes retain a distracting scroll position

**Observed at 1440 × 900.** After reading SQL results, moving to Recovery desk and System evidence left their opening headings above the visible area. The shared workspace retains its scroll position across surface changes. This makes the audience miss the setup and requires the presenter to repair the view manually.

**Fix:** reset the main workspace to its heading when changing surfaces; retain transcript position only when returning to the same conversation intentionally. Use a sticky-header-aware offset for section links.

**Acceptance:** each top-level surface opens with its title and primary action visible at 1366 × 768 and 1440 × 900, in audience mode. Sources: [desktop shell](../frontend/src/showcase/DesktopMeridianApp.tsx), especially the shared scroll container at line 387; [transcript scrolling](../frontend/src/showcase/components/ChatTranscript.tsx).

### 8. P2 for delivery - The guides still budget 45 minutes and contain unnecessary branches

[DEMO_SCRIPT.md](../DEMO_SCRIPT.md) explicitly budgets 45 minutes. The presenter guide additionally includes a managed opening request, multiple boundary prompts, a direct hold, a typed denial, a disruption transition, workflow recovery, optional confirmation, and evidence inspection. Treating every instruction as mandatory leaves little space for explanation or an audience interruption.

**Fix:** adopt the 40-minute core below, mark optional material consistently, and rehearse the transitions. Do not add another live hold just to prove a permit when the recovery hold will demonstrate the same governed write boundary.

The guide also says “four views,” “Start in Experience,” and “System proof,” while the current top-level interface has five surfaces and uses Concierge/System evidence. The operations document still devotes its day-of section to booth operation. Reconcile these into one chalk-talk operator card.

### 9. P3 - The architecture overview needs a room-scale version

The main UI typography, service assets, and light/dark styling are coherent. However, with Projector readability enabled at 1366 × 768, architecture labels still use 11-13 px CSS sizes, with measured text bounds around 15-18 px high. The projector rules enlarge selected proof components, not the SVG diagram labels. This is a readability risk, not a failed physical projector test.

**Fix:** use three progressive whiteboard views for the core: trusted context, governed action, durable execution. Keep the complete diagram for Q&A or add a focused enlargement. Do not enlarge every element until the diagram becomes harder to navigate.

Sources: [architecture styles](../frontend/src/showcase/surfaces/briefingArchitecture.css), lines 3-7; [presentation styles](../frontend/src/showcase/presentationMode.css), lines 106-126.

## Additional polish that can follow the blocking fixes

- The MCP comparison trace begins “Processing with MCP (postgres-mcp-server),” although the actual example discovers and calls the custom `meridian-concierge` server. Use the observed server name in the summary.
- Recovery's waiting text says “Ranking alternatives” during resume, although the resumed run continues at availability. Make the status specific to the pending operation; do not imply the search ran again.
- The SQL activity overview keeps a “Recalling traveler context” row in its pending state. Hide or label it as not applicable in phases that intentionally receive no memory.
- Keep hotel assistance, protection review, repeated comparisons, and the complete booking-confirmation flow out of the core. These increase clicks and invite questions beyond the recovery proof.
- The saved Tokyo plan uses October 12-19. If delivery remains the November event, label the scenario as fictional or deliberately refresh its dates before freezing the content.
- Defer broad refactoring until after the delivery path is stable. The showcase hook is 1,146 lines, the chat router 3,016, and the workflow 1,631; these are maintenance risks, but rewriting them now is not a prerequisite for a good talk.

## Recommended 40-minute core

Use this as the uninterrupted content clock; reserve the remaining 20 minutes for audience questions and operational flex. The timings are a proposed budget, not a completed timed speaker rehearsal.

| Content clock | Minutes | Show or draw | Essential point |
| --- | ---: | --- | --- |
| 0-4 | 4 | Concierge with loaded traveler brief; draw the three kinds of state | Preferences, execution progress, and a business receipt are different records. |
| 4-12 | 8 | One SQL example, one MCP comparison, one retrieval example | Grounding, reusable tools, and relevance each solve a different problem. Discuss boundaries without running every boundary prompt. |
| 12-20 | 8 | One managed recall; one authorization/policy negative control; draw the pinned-argument boundary | The application establishes traveler scope and confirmation; policy governs the tool call. Save the positive hold for recovery. |
| 20-32 | 12 | Run to checkpoint; replace the worker; resume the same journey; read the 15-minute hold | A checkpoint restores execution. Stable business intent makes a retried side effect safe. |
| 32-37 | 5 | System evidence: worker, checkpoint, policy decision, business result; draw the lost-response window | These are separate proofs. Use captured rehearsal evidence for the second failure schedule. |
| 37-40 | 3 | Session takeaways and one audience question | Aurora keeps the records; AgentCore provides the managed agent path; MCP and LangGraph connect and resume the work. |
| 40-60 | 20 | Discussion, selected implementation detail, optional booking confirmation | Answer the room's questions using the same journey and explicit proof boundaries. |

The four live comparison/managed requests should not become four long speeches. Explain the next boundary while the model call is running; the UI's trace arrives with the completed response, not as live step telemetry.

**Keep in every version:** the state distinction, one real authorization or policy decision, one checkpoint/replacement/resume proof, one business receipt, and the exactly-once caveat.

**Move to Q&A:** detailed fusion/reranking mechanics, the full RLS SQL walkthrough, every negative control, Dogwood, deployment, all service-role details, extra hotel/protection prompts, the separate 12-hour hold, and a second live fault injection.

### 35-minute version

Use 3 minutes opening, 5 SQL/MCP/retrieval, 7 governance, 12 recovery, 5 evidence, and 3 closing. Skip the live retrieval turn and use its already captured result; explain semantic plus lexical candidates and reranking in one minute. Keep the recovery and its evidence intact.

### 30-minute version

Use 3 minutes opening, 3 capability ladder, 6 governance, 11 recovery, 4 evidence, and 3 closing. Show one live SQL example and the recorded MCP/retrieval trace. Show one governance refusal. Run one recovery sequence. Explain the commit/response-loss case from the captured rehearsal instead of performing a second failure.

### Cut points and failure handling

- **At minute 12:** leave the capability ladder even if every boundary query has not run.
- **At minute 20:** move to recovery; leave deeper policy syntax for questions.
- **At minute 32:** move to the receipt and proof. Avoid hotel, protection, or new search branches.
- **At minute 37:** close the core. Do not begin another model turn.
- If an individual request reaches roughly 45 seconds without a result, switch narration to the prepared diagram. If the result is still unavailable by the request/fallback threshold, use the recorded evidence. Re-read an unknown write outcome before retrying it.
- A 15-minute hold may expire during discussion. Present its original timestamps honestly; historical evidence remains useful after expiry. Do not start a new hold just to keep the countdown green.

## Fresh validation and its limits

| Area | Result on September 17 |
| --- | --- |
| Backend offline suite | 432 passed, 6 skipped; 110 database tests intentionally deselected. Ruff passed. |
| Frontend | 207 tests across 35 files passed; lint, TypeScript and production build passed. |
| Web infrastructure | 7 tests passed, including response-header attachment coverage. |
| AgentCore CDK | Build, synthesis test, and formatting passed. Upstream deprecation warnings remain. |
| Dependency checks | Python audit and all three npm audits reported no known vulnerabilities. |
| GitHub | Application CI and CodeQL show success for the audited `c9fd62f` source. |
| Browser | All five surfaces inspected; SQL request, recovery, full refresh, evidence tabs, takeaways, Q&A and briefing navigation exercised. Light/dark briefing inspected. Entering fullscreen hid preparation controls. |
| Responsive layout | All five surfaces had no page-wide horizontal overflow or broken visible images at 390 × 844. Desktop checks included 1440 × 900 and 1366 × 768. No browser warnings/errors were captured. |
| Current AWS read-only checks | Deployed frontend index and CloudFront policy attachments inspected; anonymous edge/origin behavior tested. Protected hosted content and backend image parity were not revalidated. |

Observed call times were **4.01 s for MCP**, **21.04 s for retrieval**, and **22.39 s for managed recall**. The browser workflow took **16.53 s to pause** and **40.92 s to resume**, excluding subsequent evidence readback and presenter actions. These are individual observations, not p95 values or guarantees. The resume time is another reason to protect a 12-minute recovery segment and resolve the timeout policy.

Three fresh recovery checks passed:

1. **Lost acknowledgement after commit:** the CLI discarded a real Gateway response; the replacement worker reused the request and booking with the original expiry. Unconfirmed and over-budget negative controls were denied. The script removed its isolated rows.
2. **Hard worker death after hold/checkpoint:** the CLI killed its own worker, observed takeover refusal while the lease remained active, then resumed on another worker. One hold and its original expiry remained; nine checkpoints were observed. The script removed its isolated rows.
3. **Browser and process replacement:** the UI paused a fresh journey, the audit's backend process was stopped and replaced, the browser was fully refreshed, and the same journey resumed. System evidence showed different workers and one 15-minute hold for two travelers. The hold was created by the replacement execution; the UI correctly stated that this particular run proves checkpoint recovery, not survival of a pre-existing hold. The separate CLI checks cover that latter case. The isolated browser journey and hold were archived and removed after verification.

The pre-existing backend on port 8013 accepted a connection but failed to answer `/health` within 15 seconds and the UI went offline. A fresh backend on an isolated port loaded live catalog/profile data and durable checkpoints successfully. The old process was left untouched; its exact hang cause was not diagnosed. Audit servers were stopped after use. Include a clean day-of startup and real catalog/profile read in preflight, rather than trusting a process to have remained healthy for days.

## Remaining delivery gates

1. Correct findings 1 and 2 and run their targeted regressions.
2. Rehearse the proposed core twice with a speaker and the actual transitions; one run should deliberately use the 35-minute cut plan.
3. Establish source-to-hosted parity if the hosted site is the presentation target, and verify timeout behavior there.
4. Prepare an offline package with the architecture, actual policy refusal, checkpoint/replacement readback, and same-hold replay evidence. Existing screenshots and raw records are useful inputs; a complete independently playable fallback was not established by this audit.
5. Run a physical projector/back-row check and a second-operator rehearsal with the concise runbook.

No new full live-database test-suite run, Safari/Firefox pass, automated WCAG scan, assistive-technology test, extended wall-clock expiry test, or complete timed speaker rehearsal was performed. The manual UI checks and passing unit tests do not substitute for those checks. The remaining work is focused correction and delivery preparation; a visual redesign or additional feature phase is unnecessary.
