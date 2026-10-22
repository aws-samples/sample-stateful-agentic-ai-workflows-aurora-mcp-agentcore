# Dependency maintenance

Application Python dependencies are hash-locked in `requirements.txt`, generated
from `requirements.in`. Node projects keep their own `package-lock.json`; there
is no Node package at `meridian/` itself.

## Audit status - October 22, 2026

- Frontend: `npm audit --audit-level=high` reports 0 vulnerabilities after moving `vite` 7.3.6 to 7.3.7 and `vitest` 4.1.11 to 4.1.12, which moves `@vitest/mocker` and the other `@vitest/*` packages to 4.1.12. Lint, typecheck, unit tests, build and the accessibility suite pass.
- Backend: `python -m pip_audit -r requirements.txt` reports no known vulnerabilities. On October 22, 2026, before these changes, it reported 34 advisories in four packages. `certifi` moved from 2026.7.22 to 2026.10.19 and `mcp` from 1.29.0 to 1.30.0 (the 1.x line, within the `<2` pin). `strands-agents-tools` was removed from `requirements.in`: the application never imported `strands_tools`, and its `pillow<13.0.0` bound blocked the Pillow fix. `aiohttp` and the other packages only it required left the lock with it. `pillow` is now a direct requirement at 13.0.0, because `scripts/install_catalog_images.py` uses it. `examples/langgraph/requirements.txt` audits clean. Backend lint, the `-m "not database"` tests and `pip check` pass.

Both AgentCore Runtime manifests are audited separately by
`python scripts/audit_runtime_dependencies.py`, also run in Application CI.
Their earlier pins retained `mcp` 2.1.1 and `certifi` 2026.7.22 after the backend
lock was fixed. Both now use `mcp` 1.30.0 and `certifi` 2026.10.19. The pinned
Strands release requires `mcp<2.2`, so the patched 1.x release is used, matching
the backend. The unused 2.x-only `mcp-types`, `httpx2` and `httpcore2` constraints
are removed. See [CVE-2026-59951](https://github.com/advisories/GHSA-84m7-p3x7-pcfv).

Earlier status, October 1, 2026:

- Frontend: clean after updating transitive `brace-expansion` to 1.1.21 and 5.0.12.
- Backend: clean after updating `urllib3` to 2.8.0 and `PyJWT` to 2.15.1.
- Web and AgentCore CDK projects: **open upstream finding**. `aws-cdk-lib` bundles `brace-expansion` 5.0.9, affected by recursion and expansion denial-of-service advisories. Even the inspected 2.272.0 release retains the affected bundle, so the established CDK version is retained. This dependency runs in infrastructure tooling, not the shipped browser or backend image. No hand-edited vendored patch is applied. In the `agentcore-cdk` and `web-infra` CI jobs, `.github/scripts/npm-audit-high.mjs` replaces `npm audit --audit-level=high` and allows exactly one exception: these three advisories on `node_modules/aws-cdk-lib/node_modules/brace-expansion`. It fails on any other high or critical finding, and it fails when the advisories no longer appear, which is the signal to delete the exception. The frontend job keeps a plain `npm audit --audit-level=high`.

Track [GHSA-qhr7-859c-m2p7](https://github.com/advisories/GHSA-qhr7-859c-m2p7),
[GHSA-6j4f-fj2g-mc7p](https://github.com/advisories/GHSA-6j4f-fj2g-mc7p), and
[GHSA-q2hr-2g5m-vwhr](https://github.com/advisories/GHSA-q2hr-2g5m-vwhr).
Only trusted repository paths and deployment inputs should reach CDK.

## Update and verify

1. Resolve a targeted dependency update, preserving unrelated lock entries. Use `uv pip compile --generate-hashes --upgrade-package <name> --output-file requirements.txt requirements.in` for Python.
2. Install the lock and rerun the affected unit, type, lint and build checks.
3. Run `python -m pip_audit -r requirements.txt` and `npm audit --audit-level=high` in each Node project.
4. For infrastructure updates, synthesize and review the complete CDK diff before deployment. A dependency-only change can still change deployed resources.

Audit results are point-in-time evidence; run them again before the event.
