# AgentCore Learnings (Meridian)

Short notes from getting Phase 4 fully live with AgentCore Runtime + Gateway + Memory.

## What We Learned

- **Gateway needs real targets**: creating a Gateway alone is not enough. `tools/call` fails until at least one target is attached.
- **`localhost` is not a production target**: AgentCore Gateway runs in AWS, so target endpoints must be cloud-reachable (Lambda, API Gateway, public MCP, etc.).
- **Tool names are target-prefixed**: once a target is attached, the effective tool name becomes `<TargetName>___<toolName>`.
- **Runtime session IDs have constraints**: `runtimeSessionId` must satisfy AgentCore validation (our short conversation IDs were invalid).
- **Embedding dimensions must match DB vectors**: Cohere Embed v4 default can return 1536 unless `output_dimension=1024` is explicitly set for our pgvector schema.
- **Region consistency matters**: Runtime/Gateway/Lambda must be in the same intended region (`us-east-1` for this demo).

## What We Learned Adding the Governed Runtime (September 2026)

- **The runtime owns the tool loop**: `MCPClient(url=..., auth_provider=GatewaySigV4(...))` in Strands signs MCP requests with the runtime's execution role. The CDK wires `AGENTCORE_GATEWAY_<NAME>_URL` and `MEMORY_<NAME>_ID` into the runtime and grants `bedrock-agentcore:InvokeGateway` plus the memory permissions on its own; nothing to add by hand.
- **Cedar has no floating point type**: every amount a policy compares is an integer number of cents (`totalCents`, `budgetCeilingCents`).
- **`context.input.<arg>` needs a required argument**: the policy validator only accepts arguments the tool schema marks `required`, so every argument a policy names is required.
- **Do not deploy a `forbid` next to its `permit` in one CloudFormation update**: the policies are created in parallel, the forbid is validated before the permit exists, and the validator rejects it as overly restrictive with `FAIL_ON_ANY_FINDINGS`. One permit with all conditions in `when` deploys reliably and the default deny still covers everything else.
- **Two-pass deploy for a new target plus policies**: create the target first (policies validate against its tool schema), then the policy engine and the gateway association. The L3 constructs grant the gateway role `GetPolicyEngine`, `AuthorizeAction` and `PartiallyAuthorizeActions` in the same stack.
- **A CDK-built `lambda` target has no environment variables**: the holds Lambda reads the cluster ARN, secret ARN and database name from SSM Parameter Store (`scripts/publish_gateway_parameters.py`), and its `iamPolicy` in `agentcore.json` is scoped to those parameters, the cluster and the secret.
- **The gateway Lambda is a workload**: its execution role needs its own row in `traveler_identity_bindings` (`scripts/bind_gateway_workload.py`); the subject is the role's `RoleId`, the first part of `sts:GetCallerIdentity`'s `UserId` inside the function.
- **The model retries a refused hold**: without a guard it called the tool six times after a deny. The hook settles the hold once per turn and cancels further attempts, and the explained decision replaces the raw gateway error in the tool result so the reply names the reason.
- **Observability is two env vars away**: the CLI already wraps the entrypoint with `opentelemetry-instrument`; `AGENT_OBSERVABILITY_ENABLED=true`, `OTEL_PYTHON_DISTRO=aws_distro` and `OTEL_PYTHON_CONFIGURATOR=aws_configurator` put spans and logs, with the trace id, into the runtime's log group (`spans` and `otel-rt-logs` streams).
- **`GetGateway` returns the engine under `policyEngineConfiguration.arn`**, not `policyEngineArn`.

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
