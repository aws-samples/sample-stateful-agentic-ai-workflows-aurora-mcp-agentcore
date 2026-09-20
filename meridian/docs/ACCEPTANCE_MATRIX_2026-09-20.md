# Meridian acceptance matrix - 20 September 2026

PASS applies only to the stated evidence. BLOCKED means missing access, authorization, execution or a human check; it is not a pass. FAIL records an observed unmet requirement. NOT APPLICABLE records an explicit scope/architecture exclusion. See the [readiness report](READINESS_2026-09-20.md), [evidence inventory](EVIDENCE_2026-09-20.json) and [runbook](PRESENTER_RUNBOOK_2026-09-20.md).

| ID | Acceptance gate | Status | Evidence or exact limitation |
| --- | --- | --- | --- |
| S1 | Intended repository, branch, remote, baseline and dirty ownership | PASS | Initially clean main at b60405c1f1424cbb12cbc073f96b864e04d596b7; established origin; task-only edits and preserved listeners. |
| S2 | Audience, duration, takeaway and core/optional demos | PASS | L300; 40-minute planned core plus 20-minute discussion; concrete cuts and source walkthrough anchors in runbook. |
| S3 | Accessible AWS/deployment identity recorded | PASS | Fresh read-only account, region, Aurora, Runtime v14, Gateway, CloudFront and App Runner identity; local record retained. |
| C1 | First-party subsystem review | PASS | Every first-party subsystem reviewed with prior baseline and current source/test evidence; bounded review, not formal verification. |
| C2 | Backend lint, locked install, offline tests and audit | PASS | Ruff; locked install; 501 offline passes, six explicit skips; final pip audit no known vulnerabilities. |
| C3 | Frontend lint, independent type-check, tests, build and audit | PASS | Lint, independent type-check, 245 tests/37 files, production build; npm audit clean. Local tools used Node 26.5; CI targets Node 22. |
| C4 | Infrastructure schema, build, tests, synthesis and template checks | PASS | Both CDK builds/tests/audits; schema validate; four templates synthesized; zero lint errors, ten W3005 warnings retained (exit 4). |
| C5 | Caller/workload authorization, RLS and safe tool scope | PASS | Fresh live RLS/grant tests, decoy 403s and Cedar denial; shared presenter principal and privileged Gateway trust boundary documented. |
| C6 | Secret handling in new provisioning/hosted publisher | BLOCKED | Provisioning helper now fails before AWS on --apply. Exact required secret skill/asm-exec unavailable; legacy publisher needs approved workflow before execution. |
| C7 | AI grounding, read-only retrieval, tool arguments and truthful failure | PASS | Phase boundaries, actual named tools, memory on/off, rerank and pinned authorization arguments; failure propagation and transport lifecycle fixed. No statistical or exhaustive red-team claim. |
| U1 | Five required routes, laptop/projector/mobile rendering | PASS | Five surfaces inspected; laptop/projector-size and all five mobile layouts; no document overflow in measured states; solid-background contrast scan bounded. |
| U2 | Keyboard, focus, names, dialog Escape and scrolling | PASS | Real dialog focus trap, Escape and return; removed-trigger fallback repaired/tested; named controls and scrolling inspected. |
| U3 | Reduced-motion and arbitrary browser zoom/screen-reader execution | BLOCKED | Arbitrary browser zoom and reduced-motion switching unavailable in IAB; Chrome connection failed. Screen-reader execution and accessible-PDF certification require follow-up. |
| U4 | Loading, empty, success, error and recovery UI | PASS | Fresh loading/empty, SQL success, pause/reload/resume, confirmed receipt and deliberately stopped local backend with explicit offline and automatic reconnection UI. |
| U5 | Hosted expired-authentication and unavailable-model browser states | BLOCKED | Hosted authentication unavailable; no live model outage induced. Unit/HTTP denials and local outage do not prove hosted states. |
| U6 | Real controls, independently persisted effects, fixture boundaries | PASS | Actual browser actions and independent Aurora receipt readback; same-worker resume labeled accurately; fictional catalog/FX/static diagram boundaries stated. |
| N1 | Narrative, technical claims, pacing and clean optional cuts | PASS | Problem, decision, demo, persisted evidence, tradeoffs and close; measured 182.109s HTTP distinguished from 40-minute estimated delivery. |
| N2 | Latest deck, diagrams, notes and screenshots synchronized | PASS | Unchanged 25-slide deck/25-page PDF rechecked; selected diagrams rendered; notes link final runbook. September 19 screenshots retain their original date. |
| N3 | Accessible slide titles and alt text | PASS | September 19 native PowerPoint title/alt-text evidence retained for byte-identical deck; not a new native validation or PDF accessibility certification. |
| D1 | Startup, warm-up, required demo and persisted payoff | PASS | Final 32-step local HTTP demo passed against live AWS; longest 32.129s; durable Aurora saver required; new SQL MCP transport gate included. |
| D2 | Worker death, duplicate request, lost response, transaction/concurrency behavior | PASS | Fresh 47.78s actual SIGKILL/new worker and 40.56s lost-reply proofs; 106 live DB passes with four explicit skips. |
| D3 | Reset, rerun, nonempty protection and scoped cleanup | PASS | Fresh owned-row cleanup and duplicate-resume conflict; unchanged bootstrap/seed/rollback proof explicitly retained from September 19, not rerun today. |
| D4 | Presenter preflight, exact inputs/results, recovery and fallback | PASS | Exact inputs, ports, warm-up, code anchors, success criteria, reset and dated fallback in final runbook. |
| I1 | Static IAM actions, trust and resource scope | PASS | Scoped cluster/secret/runtime/gateway calls and separate task/pull trusts reviewed; necessary model/telemetry/ECR wildcards documented. |
| I2 | Live established runtime permissions | PASS | Fresh calls on established demo identities verified Runtime, model, Gateway/Cedar and persisted reads/writes; no fresh-account inference. |
| I3 | Fresh deployer PassRole, boundaries, quotas, new provisioning and teardown | BLOCKED | No authorized new infrastructure target; fresh deployer permissions, quotas, rollback and teardown not executed. |
| I4 | Existing environment encryption and retention hardening | FAIL | Existing Aurora encryption/deletion protection off and one-day retention; requires separately authorized migration/hardening. |
| A1 | Clean checkout installation and declared setup/build | BLOCKED | Final GitHub clean-checkout CI pending publication; fresh local duplicate install blocked by host disk exhaustion. |
| A2 | Relative links, assets, lockfiles and release source secret scan | BLOCKED | Final source link/asset/secret scan pending staged snapshot; earlier source scan contained only two literal PASSWORD documentation placeholders. |
| A3 | Inaccessible external presentation material | NOT APPLICABLE | User selected local DAT301-R as latest; updated copy is tracked and editable; no other required external deck or recording identified |
| P1 | Final application build versus live service proof | PASS | Final local backend live 32-step validation, production frontend build/browser checks; seven unchanged Runtime files match deployed v14. |
| P2 | Hosted web final-source/image/bundle/header parity | BLOCKED | Old hosted bundle/image and missing response-header policy; no hosted deployment authorized or performed. |
| P3 | Exact staged review, commit, push and independent remote SHA | BLOCKED | Task changes complete; final staged review and independent pushed SHA receipt pending. |
| P4 | GitHub Application CI on the published implementation | BLOCKED | Final Application CI pending push. |
| H1 | Timed human delivery, projector/back-row and room/network readiness | BLOCKED | Timed human 40-minute delivery, back-row/projector, real network/session duration and event metadata require presenter checks. |
| W1 | Workshop Studio, packages, labs, pins and publication | NOT APPLICABLE | Explicitly outside revised user scope |
| A4 | Final container build and startup | BLOCKED | Intermediate locked-server image built; final pip-updated image stopped after disk exhaustion/read-only shared Finch VM. Restore engine/headroom and rerun final image build. |
