# Meridian story-arc validation · 17 September 2026

> Superseded for current status by `READINESS_2026-09-19.md`, which closes the
> hosted-parity gate listed at the end of this report.

The core story completed against live AWS services using the local frontend and
backend: Concierge → SQL → MCP → retrieval → governed context → checkpointed
recovery → Aurora evidence → explicit catalog booking confirmation.
The rehearsal found and repaired application and presentation defects. It does
not establish readiness of the hosted release or a physical presentation room.

## Scope and environment

- Source baseline: `fd7637d`; fixes and this report are in the accompanying commit.
- Isolated local ports: frontend 5177, backend 8014. Existing presenter servers
  on 5176/8013 were left untouched.
- Live Aurora through HTTPS Data API, `AuroraDataApiSaver`, required durable
  checkpoints; real Bedrock inference, AgentCore Runtime, Gateway, Memory and
  Cedar policy. No schema reset, permission change or deployment was performed.
- Fictional travel catalog and Alex's demo profile. No supplier was contacted
  and no payment was taken.
- Presentation scope: in-app Solution briefing, demo script, presenter guide and
  code walkthrough. No separate slide deck was supplied for this pass.

## Story evidence

| Beat | Observed result |
| --- | --- |
| Preflight | Runtime and Gateway READY, Memory ACTIVE, policy ACTIVE/ENFORCE, observability READY; all four tools present, including `confirm_booking`. |
| Concierge opening | Live catalog and Alex's profile loaded: JFK, two travelers, $3,200 per traveler and $6,400 party budget. |
| SQL | City trips under $2,000 returned five rows. Trace and walkthrough now identify the actual direct route in `backend/routers/chat.py`. |
| MCP | Comparison and euro conversion used `meridian-concierge` tool calls. The observed server is not a generic PostgreSQL MCP server; FX is indicative. |
| Retrieval | Semantic and lexical candidates plus Cohere reranking returned Tuscany first for the wine/villa request. After the grounding repair, the reply explicitly called villa privacy unverified. |
| Context off | Production refused the memory-dependent request before Runtime execution. The trace no longer treats checkpoints or disabled memory as successful recall. |
| Context on | Authorized recall returned JFK, dietary note, boutique preference, Tokyo plan, two travelers and the $6,400 party ceiling. |
| Authorization/RLS | Alex allowed; the unbound Jordan control denied. Scoped counts hid five preference rows and one interaction row in this run. Counts change with demo usage. |
| Typed hold | Managed Runtime attempted the hold through Gateway; policy refused it without explicit application confirmation. No booking receipt was returned. |
| Disruption handoff | The fixed **Run this in Workflow** action selected Phase 5 and submitted the original canceled-flight request. |
| Checkpoint pause | Five alternatives saved; next step `availability`; no hold yet. The six-step path includes `prepare_hold` and `hold`. |
| Backend replacement | Backend process replaced, browser reloaded, saved shortlist restored, and resume continued on a different worker. |
| Positive governed write | Gateway/Cedar allowed one 15-minute hold: Tokyo Executive Stopover, two nights, two travelers, $3,898. |
| System evidence | Aurora readback showed original and replacement workers, committed checkpoint, successful resume and exactly one hold record. |
| Confirmation | Two-step traveler confirmation called `confirm_booking`; the same booking became confirmed. The confirmed receipt survived page reload. |
| Product controls | Detail drawer, save/unsave, comparison add/remove and recovery navigation worked. |
| Presentation UI | Briefing and recovery checked at 1366×768 and 390×844; no document-level horizontal overflow. Fullscreen hid presenter controls. This is browser evidence, not a back-row projector test. |

The browser recovery used thread
`phase5-992b5336-cde8-4692-96d1-c8f571323d3e`, journey `jrn_9e2217d51617`,
and booking `hold_4a3a03668c74`. Its isolated database records were removed after
receipt capture; these IDs document the rehearsal and are not a retained demo.

## Independent failure rehearsals

- **Hard process kill:** `kill_and_resume_demo.py`, with a 45-second lease,
  killed the first worker with SIGKILL after the hold checkpoint. Early takeover
  was refused; the replacement resumed after the lease/transaction cleared.
  Exactly one hold remained, with the same booking ID and original expiry.
- **Lost acknowledgement:** `lost_response_demo.py` discarded the real Gateway
  response after the hold committed. A different worker resumed the prepared
  intent and recovered the same booking and expiry. Unconfirmed and over-budget
  negative controls were both denied by Cedar. This was an injected response
  loss, not a claim that the network failed spontaneously.
- Both scripts removed their isolated rehearsal records. The lost-response
  scenario passed again after the workflow telemetry change.

These observations establish the tested failure windows. They do not imply
universal exactly-once execution or atomicity between every checkpoint and
external side effect.

## Repairs made during the pass

1. Sent the actual party size on Production requests; the prior default of one
   traveler incorrectly halved the managed party budget.
2. Blocked submission while traveler authorization/context was still connecting.
3. Fixed the Production-to-Workflow follow-up so the button does not send its
   label back to managed chat.
4. Added the missing hold-intent and governed-hold nodes to workflow evidence,
   including explicit classification and intent-preparation telemetry.
5. Removed inferred lounge, dietary-fit, preferred-stay and memory-match claims
   from result badges. Search copy distinguishes wishes from catalog facts.
6. Preserved actionable error text and made dismissal independent of chat replay.
7. Corrected trace source references, separated Runtime/RLS proof from a Cedar
   decision, required all four Gateway tools in preflight, and aligned the code
   walkthrough and presenter guide with the executed paths.

Regression checks cover the party/context race, handoff, error presentation,
unsupported badges, failed-span handling, and full recovery path.

## Automated checks

- Backend: **460 passed, 6 skipped, 110 database tests deselected**; Ruff passed.
- Frontend: **229 passed across 36 files** on Node 22 with jsdom storage;
  ESLint, TypeScript and production build passed.
- Live checks above were run separately. The full database test suite was not
  pointed at the shared demo database because it requires a disposable fixture.
- One test-fixture TypeScript error found during validation was corrected before
  the final production build.

Raw runtime responses and logs are retained locally under
`meridian/.local/story-arc-2026-09-17/` and excluded from Git. They can include
deployment/account identifiers; this report carries the shareable findings.

## Forty-minute content budget

Keep [DEMO_SCRIPT.md](../DEMO_SCRIPT.md)'s 4 + 8 + 8 + 12 + 5 + 3 minute split,
leaving 20 minutes for discussion and operational flex in the 60-minute slot.
Use [CODE_WALKTHROUGH.md](CODE_WALKTHROUGH.md) for source stops by symbol.

Observed server times were approximately 0.1 seconds for SQL, 2.6 for MCP,
20–21 for retrieval, 19 for managed recall and 28 for the typed-hold refusal.
The browser recovery reached its pause in about 16 seconds; its replacement
execution took about 27 seconds. These are individual observations on this
network, not latency guarantees or a timed presenter rehearsal.

Use one recall, one refusal and the recovery hold as the core governed-action
story. Keep confirmation, the separate 12-hour direct hold, deeper retrieval
code, and a second live fault as optional material. If time slips, cut those
before the checkpoint/transaction explanation. At minute 20 start recovery;
at 32 open evidence; at 37 close.

## Remaining presentation gates

1. **Hosted release parity:** the CloudFront S3 origin still served the September
   13 frontend (`index-DGyQg8oy.js`, `MeridianDeviceShowcase-GamPwJEQ.js`), not this
   build. Anonymous CloudFront `/` returned 401, while direct App Runner
   `/api/health` returned 200 without the current security headers. Deploy and
   verify the frontend and backend together before using the hosted URL.
2. **Final materials:** validate any separate slide deck against these source and
   evidence boundaries once supplied.
3. **Room rehearsal:** run the 40-minute talk with the presenter, code font size,
   actual projector/network and fallback evidence. The code/UI run is complete;
   delivery timing and physical readability remain separate checks.
