# Meridian code and demo review - September 13, 2026

Reviewed from clean `main` at `9a83fc1`. This report records the source
corrections and validation included in its source release. No hosted
application, Runtime, Lambda, policy, or IAM deployment was performed.

The review covered HTTP and traveler authorization, booking and hold paths,
workflow execution and replay, browser state, service artwork, presentation,
dependencies, and the CloudFront configuration. The core recovery path passed
fresh live failure rehearsals. The new findings were concentrated in direct
hold retries, asynchronous browser state, and presentation hardening.

## Findings and corrections

| Priority | Finding | Correction and evidence |
| --- | --- | --- |
| P1 | A direct hold before the first chat had no conversation ID. If its response was lost, another click could create a new conversation and a second hold request. | The browser retains an identity for the exact package, duration, and party across retries and phase resets. A regression verifies that a failed first attempt and its retry send the same identity. |
| P2 | Requesting another hold after a known expiry reused the active conversation's original request identity. | A known expired booking starts a new intent. Losing that renewal's response retains the renewal identity on retry. Both cases are covered together. |
| P2 | A late memory read could turn context back on after the presenter disabled it or returned to SQL. | Memory reads now have cancellation, a generation check, and a deadline. Phase changes and disabling memory invalidate pending reads. Both late-response cases are covered. |
| P2 | Updating or deleting a preference left the opening travel brief and displayed budget on their previous values until polling ran. | Successful mutations immediately refresh authoritative profile and budget data. The drawer serializes edits, and stale failures cannot restore facts after context is disabled. Update and delete regressions verify the displayed ceiling. |
| P2 | A successful SQL response could set the global status to live even when traveler reads had failed. | Only the readiness check can mark all live data ready. Successful chat can request a fresh readiness check; it cannot override an unavailable profile. |
| P2 | Hold and confirmation failures were displayed behind the trip dialog. The wording also suggested restarting the backend even when a write might have committed. | The dialog exposes the error directly and explains the unknown outcome. Retrying a hold checks the retained request; retrying confirmation checks the same booking. |
| P2 | The CloudFront distribution had no response security policy. | Both static and API behaviors now share CSP, anti-framing, MIME-sniffing protection, referrer policy, and HSTS. A synthesis test checks every behavior. The production bundle was exercised under the actual CSP with live API reads and no browser errors. Deployment remains outstanding. |
| P3 | Light-mode recovery status and package labels retained pale colors intended for dark backgrounds. The dark primary button measured 4.16:1 contrast. | Recovery text uses the existing light-theme tokens. The dark action color uses the established darker blue. All five views passed the final automated WCAG A/AA scans in both themes. |
| P3 | The recovery continuity rail said “No execution yet” for a journey with completed executions but no abandoned worker. | It now says “No worker interruption recorded.” A pause and resume remain distinct from a demonstrated worker interruption. |
| P3 | The secondary kiosk used an old raster architecture diagram, obsolete event details, and port 8000 in its setup snippet. | It now shares the briefing's vector diagram and AWS assets, links to the current source and setup guide, and shows the configured local port 8013. |
| P3 | The kiosk QR image encoded the former repository name, while its visible link used the current name. | Regenerated the QR for the current repository URL and independently decoded the resulting PNG with macOS Vision to verify an exact match. |
| P3 | The kiosk displayed the Phase 4 “Production” template label even for other phases or a failed request. Cached playback was not clearly identified. | The header derives its phase from the selected route and distinguishes loading, unavailable, and recorded responses. AgentCore is included in the stack context. |
| P3 | The kiosk had inconsistent typography and a QR decoration that caused horizontal overflow on mobile. | It uses the bundled Geist families and solid text emphasis. The decoration is contained; both reviewed kiosk tabs fit the narrow viewport. |
| P3 | The design documentation and preview metadata retained earlier navy colors, four tabs, and a removed room-check control. | Both documents now reflect the implemented palette and five-view navigation while preserving the established design direction. |

The implementation is primarily in
[`useMeridianShowcase.ts`](../frontend/src/showcase/hooks/useMeridianShowcase.ts),
with changes to the memory and trip dialogs, recovery styles and continuity
rail. Hosting changes are in
[`meridian-web-stack.ts`](../infra/lib/meridian-web-stack.ts).

## Service icons and demo flow

The briefing's original AWS SVGs load correctly with preserved proportions.
The seven service assets cover AgentCore, Bedrock, Lambda, App Runner, Aurora,
CloudFront, and S3. The shared service marks also load correctly. The kiosk now
uses this same diagram instead of a separate raster image. The supplied AWS
artwork itself was preserved.

Retain the current presentation sequence:

1. Concierge: show the catalog, saved traveler context, and per-traveler and
   whole-party budget.
2. Capability ladder: use the working query and boundary query for each phase.
3. Recovery desk: pause at a persisted checkpoint, then resume the same thread.
4. System evidence: show the execution records, saved state, and business
   receipt separately.
5. Session takeaways and questions. Use Solution briefing for architecture
   discussion.

For the recovery beat, explicitly distinguish a graceful pause/resume from a
worker crash. A journey with two successful same-worker attempts does not
demonstrate worker replacement. The continuity wording now respects this
distinction.

The local preview is `http://127.0.0.1:5176/showcase`, connected to Meridian's
existing backend on port 8013. Port 5173 belonged to Pellier and was preserved.

## Validation

| Area | Result |
| --- | --- |
| Backend | 432 offline tests passed; 6 skipped; 110 database tests deselected. Ruff passed. |
| Frontend | 207 tests in 35 files passed. TypeScript, production build, and lint passed. |
| Hosted infrastructure | 7 tests passed, including full distribution security-policy coverage. |
| AgentCore CDK | Build, synthesis test, and formatting passed. |
| Dependencies | Python audit and all three npm audits reported no known vulnerabilities. |
| Browser layout | All five views rendered at 1600 × 1000 and 390 × 844 without page-wide overflow or broken images. |
| Browser accessibility | All five views in light and dark themes passed the final automated WCAG 2 A/AA and 2.1 AA scans. |
| Secondary kiosk | Architecture and Try it live passed desktop/mobile layout, image, and automated WCAG A/AA checks. Phase and unavailable-trace labels were checked with a controlled backend error. |
| Production CSP | Compiled assets, local live API reads, and briefing navigation passed under the configured CSP. This was a local test, not hosted-content proof. |

The browser interaction pass verified root-route redirection, opening and
closing trip details, save/unsave, a visible failed-hold message, retrying with
the same identity, live SQL results, briefing section navigation, the Aurora
business-result tab, session takeaways, questions, and entering/exiting
fullscreen. It reported no page exceptions. Fullscreen hid the presenter
controls and leaving fullscreen restored them.

The direct-hold browser check deliberately intercepted the order endpoint:
the first response was a controlled 503 and the second returned a synthetic
`HLD-QA-ONLY` receipt. That check proves UI retry behavior; it created no
Aurora hold. The live failure rehearsals below are separate database evidence.

Browser checks used installed Playwright and Chromium. The Browser skill was
not available, and the native browser connector returned
`Codex auth token is unavailable`. Screenshots, browser reports, and command
logs are saved outside the repository under
`/tmp/meridian-review-20260913/`.

The kiosk browser check intercepted its auto-starting chat request with a
controlled 503 so that presentation checks did not create extra managed
conversations. Service artwork and QR images were loaded from the actual
frontend assets. The shared architecture keeps its own horizontal scrolling
region on narrow screens without widening the page.

Impeccable's design checks identified gradient text, a Georgia heading, and a
decorative page grid. All were corrected; no ignore entries or suppressions
were added. The design record and its component-preview sidecar were refreshed
from the implemented source. This was a consistency correction, not a new
visual direction.

Fresh live API observations:

- MCP comparison: 3 packages in 4.0 seconds.
- Hybrid retrieval: 5 packages in 19.0 seconds.
- Managed Concierge: 5 packages in 29.5 seconds, with Runtime, Gateway tool
  discovery, semantic search, package detail calls, and Memory session
  restoration on the trace.
- Those turns had no error-status activities. These are individual
  observations, not latency guarantees.
- The managed read-only smoke conversation is
  `review_417bf37c48cf`; it placed no hold and confirmed no booking.

Fresh live failure rehearsals:

- `lost_response_demo.py` discarded an actual Gateway acknowledgement after
  Aurora committed a hold. A different worker resumed from the prepared
  intent with the same request, booking, and original expiry. Gateway denied
  the unconfirmed and over-budget negative controls.
- `kill_and_resume_demo.py` committed a checkpoint and hold, killed its own
  worker with SIGKILL, observed takeover refusal while the lease was active,
  then resumed on a replacement worker. Aurora held one booking with the
  original expiry and nine checkpoints.
- Both scripts reported removal of their isolated rehearsal records. They
  did not confirm or release any existing booking.

## Remaining boundaries and follow-ups

- Deploy the hosted application and infrastructure from the reviewed source
  before attributing the new behavior or headers to CloudFront.
  This review proves local code, local rendering, and the tested live AWS
  calls.
- Direct-hold UI receipts and pending retry identities still live in the
  mounted app session. Aurora retains the business records. Restoring that
  direct-hold state automatically after a full browser refresh is a worthwhile
  product enhancement; the checkpointed recovery journey already has a
  separate restoration path.
- A loaded or confirmed historical recovery is useful evidence, but start a
  fresh ladder journey when demonstrating the pause/resume sequence.
- The CDK dependency emits an upstream `addDependency` deprecation warning.
  It does not fail synthesis; generated AgentCore code was left intact.
- A real projector/back-row check, Safari/Firefox testing, a twelve-hour
  wall-clock wait, and exhaustive failure schedules were not performed.
  Automated browser scans do not replace assistive-technology testing.

The reviewed authorization and recovery controls have useful independent
evidence: parameterized data access, traveler scope, policy refusals,
transactional holds, committed checkpoints, worker leases, and preserved
expiry. The main maintainability follow-up is to split the large showcase hook
and consolidate recovery CSS so future changes need fewer overlapping
overrides.

| Review dimension | Assessment |
| --- | --- |
| Correctness | Good after the listed corrections; retry and asynchronous-state regressions are covered. |
| Security | Stronger browser protections; existing traveler authorization and policy boundaries remained intact in the tested paths. Hosted rollout remains outstanding. |
| Performance | No new blocker observed. Model and retrieval latency warrant visible loading states and allowing time for a completed response during the talk. |
| Maintainability | Mixed. Typed evidence and regression coverage help; the large showcase hook and overlapping recovery styles remain worthwhile cleanup targets. |
