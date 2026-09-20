# Meridian acceptance matrix - 19 September 2026

PASS applies only to the stated evidence. BLOCKED means missing access, authorization, execution or a human check; it is not a pass. FAIL records an observed unmet requirement. NOT APPLICABLE records an explicit scope/architecture exclusion. See the [readiness report](READINESS_2026-09-19.md), [evidence inventory](EVIDENCE_2026-09-19.json) and [runbook](PRESENTER_RUNBOOK_2026-09-19.md).

| ID | Acceptance gate | Status | Evidence or exact limitation |
| --- | --- | --- | --- |
| S1 | Intended repository, branch, remote, baseline and dirty ownership | PASS | `origin/main`, baseline `42c1defce75289ba76ea727da72b59acf1ea33d2`; existing repairs explicitly transferred by user; unrelated listeners preserved |
| S2 | Audience, duration, takeaway and core/optional demos | PASS | L300, planned 40+20 minutes; source demo script, runbook and updated deck |
| S3 | Accessible AWS/deployment identity recorded | PASS | Account/region verified; Aurora, Runtime v14, Gateway, App Runner image and CloudFront bundle recorded read-only |
| C1 | First-party subsystem review | PASS | Frontend, APIs/auth, data/migrations, agents/tools, Runtime/Lambda, scripts/config, CDK, tests and CI covered; bounded source review, not formal verification |
| C2 | Backend lint, locked install, offline tests and audit | PASS | Ruff; hash locks; 485 tests passed; six saver-specific skips explicitly limited; audit no known vulnerabilities |
| C3 | Frontend lint, independent type-check, tests, build and audit | PASS | 237 tests, 36 files; Node 22; build and audit pass; upstream experimental warning retained |
| C4 | Infrastructure schema, build, tests, synthesis and template checks | PASS | `agentcore validate`; seven web + one AgentCore CDK tests; synth; cfn-lint has zero errors and ten nonmaterial W3005 warnings (exit 4) |
| C5 | Caller/workload authorization, RLS and safe tool scope | PASS | HTTP principal fixed traveler; loopback-only development boundary; live decoy 403, RLS and Cedar denial; trusted privileged Gateway caller limitation documented |
| C6 | Secret handling in new provisioning/hosted publisher | BLOCKED | Exact required `aws-secrets-manager` skill and `asm-exec` unavailable; inherited credential workflow not executed/replaced; needs approved capability and review before use |
| C7 | AI grounding, read-only retrieval, tool arguments and truthful failure | PASS | Actual phase boundaries, memory on/off, rerank/tool evidence, Runtime pinning and denial regressions; no statistical quality or exhaustive red-team claim |
| U1 | Five required routes, laptop/projector/mobile rendering | PASS | Actual browser inspection, 1366×768/1920×1080/390px; no horizontal document overflow; focus architecture for projector |
| U2 | Keyboard, focus, names, dialog Escape and scrolling | PASS | Named controls; dialog focus wraps inside, Escape restores launcher focus; route/receipt navigation and scroll inspected |
| U3 | Reduced-motion and arbitrary browser zoom/screen-reader execution | BLOCKED | Source/unit reduced-motion coverage available; exhaustive live preference/zoom/screen-reader execution not established; final browser control reconnection failed; complete presenter accessibility check |
| U4 | Loading, empty, success, error and recovery UI | PASS | Startup DOM, empty desk, actual saved/confirmed journey; controlled local API outage with explicit offline status and reconnect; prior global receipt regression repaired |
| U5 | Hosted expired-authentication and unavailable-model browser states | BLOCKED | Local/unit/HTTP denial evidence is bounded; hosted authentication unavailable and live model outage was not induced |
| U6 | Real controls, independently persisted effects, fixture boundaries | PASS | Browser SQL/pause/reload/resume/handoff/confirm plus Aurora/API readback; fictional catalog, configured FX rates, static briefing and replay animation identified |
| N1 | Narrative, technical claims, pacing and clean optional cuts | PASS | Problem → decision → demo → records → tradeoffs; slide notes/runbook; estimates distinguished from measured HTTP times |
| N2 | Latest deck, diagrams, notes and screenshots synchronized | PASS | User-selected DAT301-R copy updated to 25 editable slides; native rendering; current screenshot dates; no old video relabeled |
| N3 | Accessible slide titles and alt text | PASS | Native PowerPoint checks found zero missing titles/alt text after repair; opening/closing reading order reviewed; print PDF not certified accessible |
| D1 | Startup, warm-up, required demo and persisted payoff | PASS | Final 31-step local HTTP run against real AWS; every contract passed within browser deadline; 333.6 s total HTTP; longest call 48.170 s |
| D2 | Worker death, duplicate request, lost response, transaction/concurrency behavior | PASS | Fresh SIGKILL/new worker and lost-response proofs; 106 live DB tests, atomic receipt/replay/inventory assertions |
| D3 | Reset, rerun, nonempty protection and scoped cleanup | PASS | Disposable Aurora database bootstrap/rollback/retry; targeted booking cleanup; final E2E removed own records; unrelated data preserved |
| D4 | Presenter preflight, exact inputs/results, recovery and fallback | PASS | Runbook, slide notes and source references; offline captures labeled; no human-timing claim |
| I1 | Static IAM actions, trust and resource scope | PASS | Runtime/Gateway/Lambda/App Runner call paths traced; obsolete action removed; scoped Aurora/secret references; logging/model/STS/service wildcard limits documented in report |
| I2 | Live established runtime permissions | PASS | Runtime, Gateway, RLS, actual model/tool calls and persisted reads/writes passed; applies to established demo identities only |
| I3 | Fresh deployer PassRole, boundaries, quotas, new provisioning and teardown | BLOCKED | No authorized new infrastructure target; static checks and existing service success cannot prove this gate |
| I4 | Existing environment encryption and retention hardening | FAIL | Aurora storage encryption off, deletion protection off, backup retention one day; requires authorized migration/retention decision |
| A1 | Clean checkout installation and declared setup/build | PASS | Detached committed implementation checkout: 485 offline tests, fresh npm ci/typecheck/build, no copied .env; hash-locked clean venv; fresh cloud provisioning remains separate |
| A2 | Relative links, assets, lockfiles and release source secret scan | PASS | Resolved links, image loading, package validation; Gitleaks findings reviewed as two literal PASSWORD documentation placeholders |
| A3 | Inaccessible external presentation material | NOT APPLICABLE | User selected local DAT301-R as latest; updated copy is tracked and editable; no other required external deck or recording identified |
| P1 | Final application build versus live service proof | PASS | Local final backend with actual Aurora/Runtime/Gateway; frontend regression rerender after final receipt repair; runtime source-byte parity |
| P2 | Hosted web final-source/image/bundle/header parity | BLOCKED | Old CloudFront bundle and App Runner image, missing source response-headers policy; deployment and authenticated revalidation required |
| P3 | Exact staged review, commit, push and independent remote SHA | PASS | Implementation `9b4b73ea512387663611154f41a7bd4ba34070f4` pushed and independently resolved on origin/main; final documentation revision is the containing Git commit, verified in the handoff; no hosted deployment implied |
| P4 | GitHub Application CI on the published implementation | PASS | Backend, frontend, AgentCore CDK and web infrastructure jobs passed; run 35481035168 |
| H1 | Timed human delivery, projector/back-row and room/network readiness | BLOCKED | Presenter and physical environment required; runbook checklist supplied |
| W1 | Workshop Studio, packages, labs, pins and publication | NOT APPLICABLE | Explicitly outside revised user scope |
