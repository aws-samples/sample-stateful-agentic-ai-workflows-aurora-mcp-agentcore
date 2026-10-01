# Dependency maintenance

Application Python dependencies are hash-locked in `requirements.txt`, generated
from `requirements.in`. Node projects keep their own `package-lock.json`; there
is no Node package at `meridian/` itself.

## Audit status - October 1, 2026

- Frontend: clean after updating transitive `brace-expansion` to 1.1.21 and 5.0.12.
- Backend: clean after updating `urllib3` to 2.8.0 and `PyJWT` to 2.15.1. Recompiled hashes and reran backend tests.
- Web and AgentCore CDK projects: **open upstream finding**. `aws-cdk-lib` bundles `brace-expansion` 5.0.9, affected by recursion and expansion denial-of-service advisories. Even the inspected 2.272.0 release retains the affected bundle, so the established CDK version is retained. This dependency runs in infrastructure tooling, not the shipped browser or backend image. No audit suppression or hand-edited vendored patch is applied; the high-severity audit gate remains red until upstream supplies a fixed bundle.

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
