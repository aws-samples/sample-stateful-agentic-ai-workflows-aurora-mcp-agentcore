# AgentCore and hosting notes

Behavior of AgentCore, Cedar and App Runner that shaped Meridian's code and
deployment steps. Each note names the code or step it explains.

## AgentCore Gateway and Runtime

- **A gateway needs targets.** Creating a gateway is not enough: `tools/call` fails until at least one target is attached.
- **Targets must be reachable from AWS.** The gateway runs in AWS, so a `localhost` endpoint cannot be a target; use Lambda, API Gateway or a public MCP server.
- **Tool names carry the target name.** Once a target is attached, a tool is called as `<TargetName>___<toolName>`, for example `SemanticTripSearchLambda___semantic_trip_search`.
- **Runtime session IDs have a minimum length.** `runtimeSessionId` must be at least 33 characters; `_build_runtime_session_id` in `backend/agentcore/runtime.py` builds one from the conversation ID.
- **Embedding dimensions must match the database.** Cohere Embed v4 can return 1536 dimensions unless `output_dimension=1024` is set, which the pgvector columns require.
- **Keep the resources in one Region.** The runtime, gateway and Lambda targets are deployed in the Aurora cluster's Region.
- **The runtime owns the tool loop.** In Strands, `MCPClient(url=..., auth_provider=GatewaySigV4(...))` signs MCP requests with the runtime's execution role. The CDK app sets `AGENTCORE_GATEWAY_<NAME>_URL` and `MEMORY_<NAME>_ID` on the runtime and grants `bedrock-agentcore:InvokeGateway` and the memory permissions.
- **Observability needs two settings.** The CLI wraps the entrypoint with `opentelemetry-instrument`; `AGENT_OBSERVABILITY_ENABLED=true`, `OTEL_PYTHON_DISTRO=aws_distro` and `OTEL_PYTHON_CONFIGURATOR=aws_configurator` send spans and logs, with the trace ID, to the runtime's log group (`spans` and `otel-rt-logs` streams).
- **`GetGateway` returns the policy engine under `policyEngineConfiguration.arn`**, not `policyEngineArn`.
- **Run the globally installed `agentcore`, not `npx agentcore`.** `npx` can resolve an older cached CLI whose bundled CDK toolkit cannot read the cloud assembly schema the project's `aws-cdk-lib` emits.

## Cedar policies

- **Cedar has no floating-point type.** Every amount a policy compares is an integer number of cents (`totalCents`, `budgetCeilingCents`).
- **`context.input.<arg>` needs a required argument.** The policy validator accepts only arguments the tool schema marks `required`, so every argument a policy names is required.
- **Do not deploy a `forbid` next to its `permit` in one update.** CloudFormation creates policies in parallel; the forbid is validated before the permit exists and `FAIL_ON_ANY_FINDINGS` rejects it as overly restrictive. One permit with all conditions in `when` deploys reliably, and the default deny covers every other call.
- **Policies need the gateway and its tools first.** A policy names the gateway ARN and is validated against the gateway's tool schema, so a new deployment creates the gateway and targets before the policy engine. `scripts/render_agentcore_config.py` leaves the policies out until the gateway exists. The CDK constructs grant the gateway role `GetPolicyEngine`, `AuthorizeAction` and `PartiallyAuthorizeActions`.
- **The model retries a refused hold.** Without a guard, the model called the hold tool repeatedly after a deny. The runtime hook settles the hold once per turn, cancels further attempts, and replaces the raw gateway error with the explained decision so the reply names the reason.

## Gateway Lambda targets

- **A CDK-built `lambda` target has no environment variables.** The holds Lambda reads the cluster ARN, secret ARN and database name from SSM Parameter Store (`scripts/publish_gateway_parameters.py`), and its `iamPolicy` in the template is scoped to those parameters, the cluster and the secret.
- **The gateway Lambda is a workload.** Its execution role needs its own row in `traveler_identity_bindings` (`scripts/bind_gateway_workload.py`). The subject is the role's `RoleId`, the first part of `sts:GetCallerIdentity`'s `UserId` inside the function.
- **Phase 5 holds go through the gateway too.** The workflow node passes its checkpointed `holdRequestId`, `bookingId` and `executionId`; the Lambda re-checks the worker lease with `SELECT ... FOR UPDATE` inside the write transaction, so Cedar sees every hold and a restarted worker replays the same booking.
- **A cold worker needs a lease longer than its first nodes.** The search and availability nodes block the event loop for several seconds, so the first heartbeat is late; `scripts/kill_and_resume_proof.py` defaults to a 20-second lease.

## App Runner hosting

- **Pin the CDK Region.** `infra/bin/meridian-web.ts` uses `MERIDIAN_WEB_REGION` (default `us-east-1`) instead of the shell's default Region.
- **App Runner needs the complete secret ARN**, with its six-character suffix, to read a Secrets Manager value at deployment; a partial ARN fails with "unable to retrieve secret from asm".
- **App Runner roles must exist before the service deploys.** A service whose instance or ECR access role was created seconds earlier failed with "Failed to deploy your application image" and no application log. The roles live in their own stack (`MeridianWebRoles`), which `scripts/publish.py` deploys first, and the publisher waits another 30 seconds for IAM changes to propagate before it updates the service.
- **The port must open quickly.** On one vCPU the backend needs close to thirty seconds to import its dependencies and initialize the checkpoint store, and App Runner failed deployments whose port opened that late. `backend/launch.py` binds port 8000 immediately and hands the socket to uvicorn with `--fd`; early connections wait in the kernel backlog until startup completes.
- **App Runner does not run the start command through a shell.** Quotes are literal and `sh -c '...'` fails.
- **CloudTrail shows what CloudFormation sends.** `lookup-events` on `CreateService` shows the fields the resource handler adds, which helps when a service created by a stack fails and the same service created by the CLI works. The App Runner service itself is managed outside CloudFormation; `scripts/publish.py` updates it through the SDK.

## Why two directories

- `meridian/` is the application: backend, frontend, tests and scripts.
- `meridian/meridian_agentcore/` is the AgentCore CLI project: the declarative
  configuration, the runtime and gateway target code, and the CDK app that
  deploys them.

The application reads deployed IDs from `.env` or from the CLI's deployment
state at run time.
