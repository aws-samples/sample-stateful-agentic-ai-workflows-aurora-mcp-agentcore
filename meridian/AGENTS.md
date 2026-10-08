# Meridian agent instructions

Read this at the start of every session, with the owner's global rules. Global rules cover
coding standards and workflow; this file covers only what is specific to this repository.
Commands run from `meridian/` unless a line says otherwise. The Git root is one level up.

## 1. What this is and where things live

Meridian is a reference application for a talk on stateful agentic AI workflows with AWS
Aurora, MCP and Amazon Bedrock AgentCore. A travel concierge climbs five phases: SQL, MCP,
retrieval, a production Concierge with memory, and a durable Workflow. Real sign-in
(Amazon Cognito) identifies the traveler at every hop. The request path and module roles are in
[STRUCTURE.md](STRUCTURE.md).

| Path | Contents |
| --- | --- |
| `backend/` | FastAPI app, routers, Strands agents (`agents/phase_01_sql` to `phase_05_workflow`), RDS Data API client in `db/`, MCP servers, AgentCore adapters |
| `frontend/` | React and Vite app; the `/showcase` surface; Vitest unit tests and Playwright in `e2e/` |
| `infra/` | CDK apps for the Aurora cluster and the hosted web app; Lambda sources in `infra/functions` |
| `meridian_agentcore/` | AgentCore CLI project: Runtimes `MeridianConcierge` and `MeridianWorkflow` in `app/`, the Gateway Lambda targets in `agentcore/gateway_targets`, the Gateway request interceptor in `agentcore/interceptors`, the CDK app in `agentcore/cdk` |
| `scripts/` | Migrations in `scripts/migrations/`, provisioning and binding tools, release tools, identity proofs, the throwaway-Gateway harness in `scripts/gateway_harness/` |
| `tests/` | Pytest suite; `database`-marked tests need a live cluster |
| `docs/` | Operations, architecture, runbooks, learnings |
| `examples/` | SQL examples, plus `examples/langgraph/`, a maintained LangGraph example the application never imports |
| `.local/`, `.superpowers/` | Scratch, receipts and specs. Git-ignored. Never commit from them |

The Python version is 3.13 (the `Dockerfile` and CI). The local environment is `venv/`. Use
`venv/bin/python` and `venv/bin/ruff`, never the system Python.

## 2. Validation

The block below is what `.github/workflows/application-ci.yml` runs. A test
(`tests/test_agents_md.py`) fails when a line here is no longer a CI command or a package script.
Prefix Python tools with `venv/bin/` when running them locally.

```validation
# in .
ruff check backend scripts tests examples meridian_agentcore/app meridian_agentcore/agentcore/gateway_targets meridian_agentcore/agentcore/interceptors infra/functions
python -m pytest -m "not database" tests examples/langgraph/tests
python -m pip_audit -r requirements.txt
python -m pip_audit -r examples/langgraph/requirements.txt
# in frontend
npm ci
npm run lint
npm run typecheck
npm run test:run
npm run build
npm audit --audit-level=high
npm run test:accessibility
# in infra
npm ci
npm test
# in meridian_agentcore/agentcore/cdk
npm ci
npm run build
npm test -- --runInBand
npm run format:check
```

- Two more CI jobs: the `Dockerfile` build with a network-less MCP server check, and
  `.github/scripts/npm-audit-high.mjs` in `infra` and the AgentCore CDK app instead of a plain
  `npm audit` (it allows one documented advisory; see `docs/DEPENDENCIES.md`).
- Dependencies: `requirements.in` is the source, `requirements.txt` the hashed lock. Update with
  `uv pip compile --generate-hashes --upgrade-package <name> --output-file requirements.txt
  requirements.in`. Run `venv/bin/pip check` after installing.
- Complexity and line length are not in CI. Check files you touch with
  `venv/bin/ruff check --select C901,E501 --config 'lint.mccabe.max-complexity = 8' <files>`.
  The repository still has older violations; do not add new ones.
- Run only the tests that cover your change, then the `-m "not database"` suite before a commit.
  `venv/bin/python -m pytest --collect-only -q` lists tests offline. Unit tests get fake AWS
  credentials and no network from `tests/conftest.py`; never turn that off.
- `database`-marked tests need the live cluster. Tests for anything touching Aurora must read what
  the database holds afterwards, not a recorded call log (fakes hid four real defects once).
- `-W error` cannot be used with pytest here. `pytest.ini` already filters one upstream warning.
- The Playwright suite runs ungated (`--mode e2e`) on port 4174 and never reads
  `frontend/.env.development.local`. The sign-in screen is covered by the `gated` project.
- macOS: `npm` and `npx` are nvm aliases that contain a `;`. In `npm run a && npm run b` the `;`
  ends the chain, so a failed gate is ignored. Call the real binary
  (`/Users/shayons/.nvm/versions/node/v22.20.0/bin/npm`), run each gate as its own command, and
  read its exit status. Do not put the nvm bin directory first on `PATH`.
- `git apply` from a subdirectory silently skips paths outside it. From the Git root use
  `git apply --directory=meridian`, or use `patch -p1`.

## 3. Hard rules

Live tools. Anything that calls AWS needs the owner's explicit approval for that step. That covers
`scripts/release_identity.py`, `scripts/publish.py`, `scripts/prove_backend_login.py`,
`scripts/identity_proof.py`, `scripts/run_gateway_harness.py`, the provisioning, seed and bind
scripts, `scripts/sync_*` and `scripts/seed_*` tools, and `agentcore deploy`.

- Dry run first, read the plan, then ask. Each of these tools needs its own confirm flag
  (`--apply` plus `--i-understand-this-changes-aws` or `--i-understand-this-creates-aws-resources`).
  Never add a flag to skip a confirmation. Exit codes differ by tool; see `scripts/README.md`.
- Never run `scripts/apply_migrations.py` bare. Run it with `--pending` first and apply one
  migration at a time, with a rolled-back dry run before the real apply.
- Deploy AgentCore only with `/opt/homebrew/bin/agentcore`. An older `agentcore` in the nvm bin
  directory fails synth with a cloud assembly schema mismatch. Never use `npx agentcore`.
- Never run release or publish tools "to check". `check` subcommands that only read still call AWS.

Secrets and identifiers. Never print, log, paste into a doc or commit credentials, tokens, secret
values, Cognito pool or client ids, the hosted-UI host, or 12-digit AWS account ids.

- Mask an account id as `<acct>`. Fake ids in tests are 123456789012, 111122223333 and
  999999999999. Use only those.
- Tokens are minted into memory or an inherited pipe, never a file or an argument.
- Passwords live in AWS Secrets Manager and the macOS Keychain. `.env` is git-ignored.
- A receipt binds to the Git HEAD sha. A live proof needs a clean, committed tree, so commit the
  change, then run the proof (after approval).
- Do not write real receipts, screenshots with identifiers, or captured responses into tracked
  files. They belong in `.local/`.

## 4. Architecture facts not to break

- Identity path: a Cognito access token is verified at the backend, both Runtimes and the Gateway.
  The traveler comes from the verified claim, never from a request body or a tool argument. The
  browser path uses the alias `me`; `authorize_traveler` resolves it (`backend/http_auth.py`).
- RLS is pinned per transaction. `scoped_session` in `backend/db/rds_data_client.py` steps down to
  `meridian_app` with `SET LOCAL ROLE`. The master login is not subject to RLS even with FORCE, so
  any read outside a scoped session bypasses every policy. Test cleanup and seeding rely on that.
- FORCE-RLS tables need `app.current_traveler_id` set; `bookings` also needs `app.agent_type`. A
  read without the pins silently matches zero rows, so an empty result is not proof of no data.
- Least-privilege logins: `meridian_backend`, `meridian_gateway`, `meridian_identity`,
  `meridian_workflow`. None bypasses RLS. The backend reads an admin count only through the
  definer function `backend_admin_count` (`backend/db/admin_counts.py`).
- `MERIDIAN_AGENTCORE_AUTH` is `iam` (default, also when unset) or `jwt`. Only the files in
  `BRANCHING_FILES` in `tests/test_identity_switch_matrix.py` may branch on the mode. Add a row
  there with a test, or do not branch. `backend/agentcore/auth_mode.py` is the one definition.
- Phase 5 is a Strands Graph on the `MeridianWorkflow` Runtime. State is Aurora snapshots in
  `workflow_snapshots`, with a worker lease and fenced writes. A snapshot is capped at 900,000
  bytes (`backend/agents/phase_05_workflow/snapshot_storage.py`). LangGraph appears only in
  `examples/langgraph/`; `tests/test_app_never_imports_langgraph.py` guards it.
- `meridian_agentcore/app/MeridianWorkflow/backend/` is generated by
  `scripts/stage_workflow_runtime.py`. Edit the source under `backend/`, not the copy.
- Data API limits measured on this cluster: a response over 1 MB fails; the 64 KB row limit in
  some AWS posts does not apply. Size guards target the 1 MB response.
- Migrations in `scripts/migrations/` are append-only. Never edit or reorder an applied file; add
  the next number. They are applied to the live cluster separately, by the owner's decision.
- The holds Lambda and every booking go through the governed Gateway path. No catalog agent
  writes a booking directly.

## 5. Copy and style

These apply to user-facing text, docs, test names and test strings.

- No middle dots, em dashes or en dashes. Use a comma, a colon, a period or a hyphen.
- Write "AWS Aurora" for Aurora and "Amazon" for the other services (Amazon Bedrock, Amazon
  Cognito). Aurora never takes the "Amazon" prefix; the docs guard rejects it.
- Never promise or call the product a demo or "demos".
- Phase 5 wording is "snapshot" or "saved step", not "checkpoint". LangGraph is never named
  outside `examples/langgraph/`.
- Plain, professional voice. No inflated or promotional phrasing, no filler.
- Enforced by `frontend/src/showcase/lib/__tests__/copyGuard.test.ts` (frontend sources) and
  `tests/test_docs_copy.py` (tracked Markdown, including this file). Fix the text, not the guard.

## 6. Working conventions

- Tests first. Write the failing test, watch it fail for the right reason, then fix. Prove a
  safety test by breaking the guarded code (a mutation) and seeing it fail.
- Reviews are read-only. A reviewer reports; it does not edit.
- Several agents can share the working tree and the Git index. Stage by explicit path, check
  `git diff --cached --name-only` first, and never run `git add -A` or `git add .`. Never
  force-add anything under `.superpowers/`. Do not stash, reset or revert work you did not make.
- Commit trailers, when the session provides them: `Co-Authored-By: <model> <noreply@anthropic.com>`
  and `Claude-Session: <url>`, after a blank line. Subject: imperative mood, at most 72 characters.
- Owner decision: all work lands on `main` for this repository. Push only reviewed commits, as
  the owner asks. Never rewrite history or force push.
- Leave scratch clean. Use `trash`, not `rm -rf`. Stop servers you started and remove temp
  directories and worktrees you made.
- Do not widen scope. If you see adjacent breakage, report it.

## 7. Private and out-of-repo material

- `chalk_talk.md` and `STAGE_CHECKLIST.md` at the Git root are private speaker material. They
  are not published. Do not read them into other files, edit them or add them to a commit.
- The DAT307 deck, its screenshots and its renders live in WorkDocs, outside this repository.
  Never delete anything there. Before any write, check for a PowerPoint `~$` lock file next to the
  deck. Never close or quit the owner's open presentation. Work from the owner's saved file, back
  it up first, and regenerate the Windows copy once at the end.
- Do not put deck paths or WorkDocs contents in committed files beyond what is already there.
- macOS can deny reads in `~/Desktop` for a process without folder access: `ls` works but every
  read fails with a permission error. Ask the owner to restore access instead of working around it.

## 8. Which document to open

| Need | Open |
| --- | --- |
| Request path and module roles | [STRUCTURE.md](STRUCTURE.md) |
| Setup, runbooks, sign-in, identity switch, proofs, publishing, troubleshooting | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| Runtime, Gateway, Memory and Cedar deployment steps | [docs/AGENTCORE_DEPLOY_RUNBOOK.md](docs/AGENTCORE_DEPLOY_RUNBOOK.md) |
| Snapshots, leases, memory, transactions, recovery | [docs/STATEFUL_ARCHITECTURE.md](docs/STATEFUL_ARCHITECTURE.md) |
| Whether Aurora should verify a signed traveler scope | [docs/SIGNED_SCOPE_EVALUATION.md](docs/SIGNED_SCOPE_EVALUATION.md) |
| AgentCore, Cedar and Lambda behavior that shaped the code | [docs/AGENTCORE_LEARNINGS.md](docs/AGENTCORE_LEARNINGS.md) |
| What each script writes, and each tool's exit codes | [scripts/README.md](scripts/README.md) |
| Dependency audit status and safe lock updates | [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) |
| Agent modules by phase | [backend/agents/README.md](backend/agents/README.md) |
| AgentCore project layout and rendered config | [meridian_agentcore/README.md](meridian_agentcore/README.md) |
| All other docs | [docs/README.md](docs/README.md) |
