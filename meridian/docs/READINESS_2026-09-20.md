# Meridian source and demo readiness - 20 September 2026

**Verdict: the repaired local application passes the executed source and live-service checks. Hosted release and complete presentation readiness remain blocked.** This pass extends the [September 19 review](READINESS_2026-09-19.md); historical checks below remain dated, rather than being presented as fresh execution. See the [acceptance matrix](ACCEPTANCE_MATRIX_2026-09-20.md), [presenter runbook](PRESENTER_RUNBOOK_2026-09-20.md), and [evidence inventory](EVIDENCE_2026-09-20.json).

## Scope, identity and review coverage

Repository `/Users/shayons/Desktop/Workshops/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore`, branch `main`, initially clean at `b60405c1f1424cbb12cbc073f96b864e04d596b7`. Remote: `https://github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore.git`. Existing configured Git authentication is used without reading or changing credentials. No unrelated changes or listeners were removed. Workshop Studio is excluded.

L300 chalk talk: **40 minutes planned content plus 20 minutes discussion/flex**. The story remains Alex's canceled JFK-to-Tokyo trip, progressing through SQL, MCP, retrieval, workload authority, durable recovery, and independent business evidence. Context, permission, workflow progress and business records have separate owners. The runbook names optional cuts; automated HTTP timings are not human rehearsal timings.

The review covers first-party frontend/API clients and hooks, FastAPI routes/authentication, agents/tools, Aurora access/migrations, checkpoint saver/leases/hold intent, Runtime/Gateway code, scripts/configuration, both CDK projects, dependencies, CI and presentation assets. The unchanged implementation and prior repairs were rechecked against current source and fresh contract/live tests. This is bounded review across every subsystem, not formal verification or exhaustive input coverage.

Root and nested AgentCore instructions were read. No VOICE.md exists. Imported `~/.Codex/steering/` files were unavailable. Design-taste, Impeccable and AWS CDK guidance were applied; approved visual tokens and vocabulary were preserved. The exact required `aws-secrets-manager` skill and `asm-exec` were unavailable after discovery. No secret value was fetched. AWS MCP operation failures were retained; permitted Boto3 fallback supplied read-only deployment identity. No new infrastructure deployment, migration or teardown was performed.

## Repairs

| Severity | Finding and repair | Proof |
| --- | --- | --- |
| High | Phase 2 downloaded an incompletely constrained PostgreSQL MCP environment at request time; MCP 2.x removed its FastMCP import. The server and compatible SDK now share the hash-locked application install and both launchers use the active Python interpreter. Container startup validation fails the build if the module cannot load. | Clean locked install, real generic MCP query, 32-step live validation, container build |
| High | MCP transport contexts crossed request/task boundaries or closed through a different task, causing AnyIO cancel-scope failures and leaked subprocesses. Each request now owns its connection; all three clients unwind partial startup and close in the owning task. | Nine new startup/ownership/failure regressions plus existing MCP tests and fresh repeated live calls |
| Medium | MCP traces claimed connection/discovery before execution; a failed SQL tool could look like an empty result. Generic trace names now come from tools/list, custom discovery follows a completed call, session setup is excluded from the UI tool count, absent configuration fails clearly, and tool errors propagate. | Live named tool evidence and failure regression |
| High | The SIGKILL proof driver could block indefinitely or accept the wrong worker as replacement. Startup is bounded, the killed process is reaped, and persisted execution/worker identity is verified. | Six driver regressions and actual SIGKILL/new-worker proof |
| High | The legacy cluster helper could perform credential-handling operations outside the mandated workflow. It is now plan-only; `--apply` fails before any AWS call. New provisioning remains blocked, rather than appearing supported. | Fake-AWS no-call regression; documented boundary |
| Medium | Several frontend reads/writes bypassed the common deadline and successful HTTP 204 responses were parsed as JSON. They now use bounded requests and accept empty success responses. | Five request/client regressions, UI/live journeys |
| Medium | Closing a trip dialog after a surface change could return focus to a removed button. It now falls back to the persistent travel workspace. | Two focus regressions; actual keyboard trap/Escape/return checks |
| Medium | The local packaging environment's pip 26.1.2 had a reported advisory. It was updated; the container explicitly installs 26.2.1. | Final Python audit: no known vulnerabilities |

## Current evidence

Raw evidence is ignored under `meridian/.local/release-review-2026-09-20/`; the committed inventory records hashes, timestamps, commands and exit statuses. Logs contain demo resource identifiers and are not release assets.

| Gate | Result and boundary |
| --- | --- |
| Backend | Ruff PASS; **501 offline tests passed**, six saver-contract skips, 110 database tests deselected. Hash-locked install passed. Python audit reports no known vulnerabilities after the packaging-tool repair. |
| Frontend | Lint, independent type-check, **245 tests in 37 files**, production build and dependency audit PASS. Final entry bundle: `index-DUXyXmBb.js`. |
| Live Aurora | **106 passed, four skipped**, 188.62 seconds. Covers RLS, grants, receipts, transaction rollback, idempotency, replay and concurrency. Skips are not passes. |
| Infrastructure | Web CDK seven tests; AgentCore CDK one synth test; builds, format check, schema validation, all four synthesized templates and package audits checked. cfn-lint: **zero errors, ten W3005 redundant dependency warnings, exit 4 retained**. |
| Existing AWS services | Runtime v14/Gateway READY, four tools, Memory ACTIVE, policy ACTIVE/ENFORCE and observability READY. Seven deployed Runtime Python files match local source bytes. |
| Final HTTP journey | **32 checks passed**, including the newly added real PostgreSQL MCP transport query. No request exceeded the browser's 55-second deadline. Own conversations/holds were removed. Timings are in the runbook. |
| Fault injection | Actual SIGKILL/replacement worker proof passed in 47.78 seconds. Deliberately lost committed reply proof passed in 40.56 seconds. Both retained one hold with the original expiry and cleaned their own rows. |
| Browser | Actual Concierge, ladder, Recovery, System evidence and briefing; laptop 1366×768, projector-size 1920×1080 and 390px mobile layouts. No document overflow in measured states. Loading, empty, SQL, pause/reload/resume, governed confirmation, explicit offline/automatic reconnection and keyboard dialogs checked. Screenshots and DOM records retained. |
| Presentation | Unchanged 25-slide editable deck, 25 notes parts and 25-page PDF structurally checked; selected rendered diagrams and closing slides inspected. September 19 native PowerPoint title/alt-text/reading-order results apply to these unchanged assets. Embedded captures retain their original date. |
| Clean source/package | A fresh local checkout is blocked by host disk pressure. The final GitHub CI checkout is tracked separately in the publication receipt. An intermediate locked-server linux/amd64 image built successfully; the final pip-updated image did not complete after disk exhaustion made the Finch VM filesystem read-only. This final packaging gate is BLOCKED. |

Unsuppressed upstream Pydantic `lifespan`, Node test localStorage and generated CDK warnings remain. The contrast scan checks visible text against solid backgrounds, not a complete accessibility certification. Live arbitrary browser zoom, reduced-motion preference switching and screen-reader execution were unavailable and remain BLOCKED. Browser console/network records from the intentional offline test are distinguished from successful runs.

Failed attempts remain: MCP 2.x import failure; uvx under x86 container emulation exited 139; an initial catalog probe incorrectly assumed 30 rows instead of the actual 35; local pip audit advisory; final container disk exhaustion/read-only VM; an incorrect template glob; AWS MCP operation lookup failures. The final repair uses the fully locked Python module, and current count checks compare authoritative database results rather than assuming yesterday's seed size.

## Persisted payoff and truthful boundaries

Browser journey `jrn_ae24e6cfa592`, thread `phase5-e3bea4b2-5a20-4ff7-b5b1-6f8e91ced7e0`, checkpoint `1f1b5306-5d3f-6ffc-8007-b92b440e56f2`: **same-worker** pause/resume on `worker-59e64440`. Hold `hold_6a0811367694` became a confirmed booking at `2026-09-20 20:30:43 UTC`; independent Aurora readback confirmed TKY-003, two travelers, two nights, $3,898. This did not contact a supplier or take payment.

Separate SIGKILL journey `jrn_0e035218fca7`, thread `demo-85505aa284`: worker `worker-46ed96b7` exited -9; a different worker completed the saved execution. Hold `hold_ae3d2517335f` remained unique. Separate lost-reply journey `jrn_e989aff10486` retained hold `hold_a06e4403edec`. Fault logs remain dated evidence after scoped cleanup.

The browser uses one shared demo principal bound to Alex. Workload grants and RLS constrain traveler access; model-produced arguments do not grant authority. Cedar and Lambda enforce write constraints, with privileged Gateway IAM callers an explicit trusted boundary. Fictional persona/catalog, illustrative FX, inspiration images and static architecture diagrams are not live external services. Rerank animation replays returned ranks, not elapsed model execution. No statistical quality benchmark, exhaustive prompt-injection red team or online model-judge deployment is claimed.

## Remaining blockers and exact next actions

1. **Hosted parity - BLOCKED.** Established account `619763002613`, region `us-east-1`; Runtime `meridianv2_MeridianConcierge-LpDBbFBjsc` v14 matches its unchanged source. CloudFront `E2Z7IZTFX7YYZJ` still serves `index-DGyQg8oy.js`; App Runner still uses image `2c5495b068259535d8ea8a4b0ad2794355dee68f4788f2c216a6ccaa0334187e`. The live distribution lacks the source response-headers policy. Obtain target-specific deployment authorization and the approved secret workflow, repair/review the inherited publisher, deploy, then verify authenticated image/bundle/header parity and rerun the live journeys. Do not use the old hosted site as evidence for this source.
2. **Fresh infrastructure lifecycle - BLOCKED.** New cluster creation, deployer PassRole, boundaries, service-linked roles, quotas, bootstrap in a fresh account, rollback and teardown were not executed. The new plan-only provisioning helper makes this explicit. The September 19 disposable-Aurora bootstrap/seed/rollback proof remains valid historical evidence for unchanged database scripts; it was not rerun today. Use an authorized isolated target for a new rehearsal.
3. **Existing Aurora hardening - FAIL.** Read-only inspection still reports encryption off, deletion protection off, one-day retention, and 0.5–64 ACUs. A source fix cannot retrofit storage encryption. Authorize a snapshot/copy/restore migration and retention/cost plan separately. Do not migrate or reset the shared demo database during preflight.
4. **Final container and local clean checkout - BLOCKED.** The intermediate locked-server image built (`b1bbb806dd43350584701272a17d54c52669a1cba1da63f5e91a82571bc135d2`), but the subsequent final pip-updated build encountered host disk exhaustion and a read-only Finch VM, then was stopped with exit 130. Only this review's temporary source scan and generated staging assets were removed; unrelated images and the shared VM were preserved. Restore disk headroom and the shared container engine, then rebuild the final Dockerfile and verify startup. A new GitHub CI job builds the final production image and runs its MCP module as the non-root image user with networking disabled. Its final result is recorded separately; local engine recovery remains outside this source change.
5. **Accessibility and human delivery - BLOCKED.** Complete the runbook's timed 40-minute rehearsal, 20-minute discussion reserve, actual projector/back-row/fullscreen checks, browser zoom, reduced motion, screen-reader and event-specific accessible-PDF checks. Confirm event title, session code and speakers. Automated timings and renders do not establish these.

Static IAM review retains scoped cluster/secret/runtime/gateway resources and separate App Runner task/pull trusts. Configurable model/inference-profile resources and telemetry/ECR actions retain justified service-level wildcards. No new grant, boundary or IAM simulation was treated as proof of fresh deployment permissions. Existing billable Aurora, snapshots, logs, Runtime/Memory, App Runner and CloudFront resources remain.

## Source publication

The containing Git revision identifies this complete source release. The final handoff and ignored `release-receipt.json` record the full implementation/final documentation SHAs, independent remote lookup, clean checkout, container identity and GitHub CI. This avoids embedding a commit hash inside the file that determines that hash. No force push, hosted deployment or Workshop Studio operation is part of this source publication.
