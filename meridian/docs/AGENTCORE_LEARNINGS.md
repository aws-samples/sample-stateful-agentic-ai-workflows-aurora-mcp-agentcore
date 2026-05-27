# AgentCore Learnings (Meridian)

Short notes from getting Phase 4 fully live with AgentCore Runtime + Gateway + Memory.

## What We Learned

- **Gateway needs real targets**: creating a Gateway alone is not enough. `tools/call` fails until at least one target is attached.
- **`localhost` is not a production target**: AgentCore Gateway runs in AWS, so target endpoints must be cloud-reachable (Lambda, API Gateway, public MCP, etc.).
- **Tool names are target-prefixed**: once a target is attached, the effective tool name becomes `<TargetName>___<toolName>`.
- **Runtime session IDs have constraints**: `runtimeSessionId` must satisfy AgentCore validation (our short conversation IDs were invalid).
- **Embedding dimensions must match DB vectors**: Cohere Embed v4 default can return 1536 unless `output_dimension=1024` is explicitly set for our pgvector schema.
- **Region consistency matters**: Runtime/Gateway/Lambda must be in the same intended region (`us-east-1` for this demo).

## Do We Need Both `meridian` and `meridian_agentcore`?

Yes, for now:

- **`meridian/`** = product app (FastAPI + frontend + tests + demo code).
- **`meridian/meridian_agentcore/`** = AgentCore CLI project (infrastructure definition and deployment state).

Think of `meridian_agentcore` as infra-as-code for AgentCore resources. The app reads deployed values from env/CLI state and uses them at runtime.

If we later want to simplify naming, we can migrate infra config into a single stable folder name, but functionally this split is fine.

## Kiosk + Walkthrough Strategy

Recommended approach: **reuse the same deployed stack**, do not live-redeploy during the talk unless deployment itself is part of the lesson.

### Before kiosk starts

1. Verify backend + frontend are up.
2. Verify AgentCore resources are healthy (`runtime`, `gateway`, `memory`).
3. Verify Gateway tool call works once (`semantic_trip_search`).
4. Keep this deployment running through kiosk and walkthrough.

### During code walkthrough (2 hours later)

- Use the same codebase and same deployed resources.
- Walk through code path phase-by-phase.
- Optionally show the exact deploy commands as a "recorded/proven" step, but avoid re-running full deploy live unless you have buffer time.

### If you want to demo deployment live

Use a **controlled segment**:

- Pre-validate AWS auth + target config.
- Run only the minimal commands.
- Keep a fallback branch/environment with already-working resources in case deploy timing or policy issues occur.

## Practical Rule of Thumb

- **Demo reliability first** (kiosk): pre-deploy, pre-validate, no surprises.
- **Teaching clarity second** (walkthrough): explain architecture and commands, but reuse known-good deployed resources unless live deploy is explicitly the session goal.
