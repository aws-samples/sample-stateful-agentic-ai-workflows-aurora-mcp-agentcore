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

## What We Learned Publishing Behind CloudFront (September 2026)

- **Phase 5 holds go through the gateway too**: the workflow node passes its checkpointed `holdRequestId`, `bookingId` and `executionId`; the Lambda re-checks the worker lease with `SELECT ... FOR UPDATE` inside the write transaction, so Cedar sees every hold and a restarted worker replays the same booking.
- **A cold worker needs a lease longer than its first nodes**: the search and availability nodes block the event loop for several seconds, so the first heartbeat is late; `scripts/kill_and_resume_demo.py` defaults to a 20 second lease for that reason.
- **Pin the CDK region**: the shell default here is us-west-2 and the first stack landed there. `bin/meridian-web.ts` pins `us-east-1`.
- **App Runner wants the complete secret ARN** (with the six character suffix) to read a Secrets Manager value at deployment; a partial ARN fails with "unable to retrieve secret from asm".
- **App Runner needs its roles to exist before the service is deployed**: a service whose instance role was created seconds earlier fails with "Failed to deploy your application image" and no application log, and the identical definition succeeds once the role is a minute old; the ECR access role behaves the same way. Both live in their own stack (`MeridianWebRoles`); `scripts/publish.py` deploys it first and waits 90 seconds whenever it created or changed it.
- **App Runner in us-east-1 rejected every optional setting** with the same message: a custom auto scaling configuration, a health check at a 10 second interval (HTTP or TCP), an explicit default egress configuration, and an empty tag list each failed on their own while the identical service without the setting deployed at the same moment. The service definition is now the bare minimum, the L2's default `NetworkConfiguration` is deleted with a property override, and the service carries a real tag so CloudFormation does not send an empty list. The default health check is TCP; uvicorn binds the port only after the lifespan startup has initialised the checkpoint backend, so an open port means the backend is ready.
- **App Runner service creation also had a window of failing everything**, including three identical known-good services, for about 20 minutes after a burst of experiments. The service is its own stack (`MeridianWebBackend`) so `publish.py` retries it without recreating CloudFront and the bucket.
- **CloudTrail shows what CloudFormation really sends**: `lookup-events` on `CreateService` exposed the `tags: []`, `observabilityConfiguration` and `networkConfiguration` fields the resource handler and the L2 add, which no CLI experiment had sent.
- **App Runner does not run the start command through a shell**: quotes are literal and `sh -c '...'` fails. To probe a container, use a whitespace free `python -c exec(bytes.fromhex(...).decode())` command that serves the health port itself and logs what the real command does.
- **The Finch VM cannot reach ghcr.io**, so uv is installed from PyPI in the Dockerfile, and its ECR push flakes until the VM is restarted.

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
