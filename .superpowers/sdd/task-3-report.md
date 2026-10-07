# Task 3 report

Status: DONE. Commit: see `git log -1` (subject "Count across travelers through a definer function,
not the master login"), local main only, not pushed.

Files: created `meridian/backend/db/admin_counts.py`, `meridian/tests/test_backend_admin_reads.py`;
modified `meridian/backend/routers/diagnostics.py` (all 11 brief replacements applied verbatim,
each matched exactly once).

Red first: `venv/bin/python -m pytest tests/test_backend_admin_reads.py -q -p no:cacheprovider`
-> `ModuleNotFoundError: No module named 'backend.db.admin_counts'`, `1 error in 0.08s`.

Green:
- `pytest tests/test_backend_admin_reads.py tests/test_session_receipt_scope.py
  tests/test_diagnostics_rls.py tests/test_error_exposure.py` -> `26 passed`
- `pytest -m "not database" tests` -> `1100 passed, 116 deselected`
- `ruff check` on the three files, `ruff check --select C901,E501 --config
  'lint.mccabe.max-complexity = 8'` on the same, and the CI-scope ruff path list: all `All checks passed!`
- `DEMO_TRAVELER_ID` no longer appears in diagnostics.py.

No live tests added (brief has none). No frontend touched, no AWS calls.

Notes: `authorize_traveler` does not yet map the `me` alias (a later task); a missing id already
means the principal's traveler, which is all this task needs.

## Fix round 1

Changes: `_tables_exist` returns False on any exception; `_admin_count_or_none` takes `key`, and the
workflow snapshot count uses it (None -> 200 with detail "workflow snapshots for thread X could not
be counted"); negative control returns `decision: "not_applicable"` (same shape, no audit row, no
authorization call) when the principal is the decoy, frontend `decision` type and card label
updated; `_tables_exist` and `_load_policies` both take `Sequence[str]`; new contract test reads
migration 018 and asserts every kind in `RLS_BASELINE_KINDS` and `diagnostics.py` is a function branch.

Red: `pytest tests/test_backend_admin_reads.py` -> `4 failed, 8 passed`.
Green:
- `pytest tests/test_backend_admin_reads.py tests/test_session_receipt_scope.py
  tests/test_diagnostics_rls.py tests/test_error_exposure.py` -> `29 passed`
- CI-path `ruff check` and `--select C901,E501` (max-complexity 8) -> `All checks passed!`
- `pytest -m "not database" tests` -> `17 failed, 1096 passed, 116 deselected`; all 17 are in
  `test_render_agentcore_config.py`, `test_publish.py`, `test_gateway_provisioning_scripts.py`,
  which Task 4 is editing concurrently (uncommitted); none touch diagnostics.

## Fix round 2

- diagnostics.py: both error-swallowing helpers now call `log_exception` and return `Probed(value, ref)`;
  `_tables_exist` is tri-state (True/False/None). Receipt details say "could not be counted (ref X)",
  "snapshot table could not be checked (ref X)", "audit counts unavailable (ref X)"; never exception text.
- Unknown counts are `count=None` (type `Optional[int]`), never 0. Frontend: `SessionReceiptLine.count`
  is `number | null` (no component renders receipt lines yet; client.test.ts pins null passthrough).
- Contract test parses `IF/ELSIF p_kind = / IN (...)` branches and asked kinds from the AST; set
  containment asserted. Mutating the migration branch made it fail ("kinds the function lacks").
- `fake_checks` inlined; RlsProbeCard uses `.replace(/_/g, ' ')`.

Commands and results:
- `venv/bin/pytest tests/test_backend_admin_reads.py -q` -> 13 passed
- `venv/bin/ruff check backend scripts tests examples meridian_agentcore/app meridian_agentcore/agentcore/gateway_targets` -> All checks passed!
- `ruff check --select C901,E501 (max-complexity 8, line-length 100)` on changed py -> All checks passed!
- `venv/bin/pytest tests -m "not database" -q` -> 1142 passed, 116 deselected
- `frontend/node_modules/.bin/tsc --noEmit -p tsconfig.json` -> exit 0
- `eslint` on client.ts, client.test.ts, RlsProbeCard.tsx --max-warnings 0 -> exit 0
- `node scripts/design-tokens/check.mjs` -> design tokens: 18 files pass
- `vitest --run src/api/client.test.ts RlsProbeCard.test.tsx` -> 2 files, 20 tests passed
