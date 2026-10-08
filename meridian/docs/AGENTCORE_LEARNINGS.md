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
- **Phase 5 holds go through the gateway too.** The workflow node passes its saved `holdRequestId`, `bookingId` and `executionId`; the Lambda re-checks the worker lease with `SELECT ... FOR UPDATE` inside the write transaction, so Cedar sees every hold and a restarted worker replays the same booking.
- **A cold worker needs a lease longer than its first nodes.** The search and availability nodes block the event loop for several seconds, so the first heartbeat is late; `scripts/kill_and_resume_proof.py` defaults to a 20-second lease; the runner itself uses 60 seconds with a 10-second heartbeat.

## Workflow Runtime

- **A Runtime session id outlives its microVM.** The backend derives the session id from the traveler and thread (`workflow_session_id`), so a stopped session that is resumed starts a new microVM on the same id. Nothing on the microVM is state; the newest row in `workflow_snapshots` is.
- **Strands saves the answer before it deactivates the interrupt.** In Strands 1.57.2, a worker killed in the node after a traveler's answer leaves a snapshot that asks for the answers again. `ResumableStorage` repairs it on read, which is why `strands-agents` is pinned to `1.57.2` and `tests/test_strands_pin.py` checks the pin.
- **A retried step can leave the Graph marked failed.** The runner reports success only when `synthesize` is among the completed nodes.
- **The workflow runs as its own login.** `meridian_workflow` is NOBYPASSRLS and owns nothing, so each snapshot statement pins the traveler in its own transaction and the RLS policies decide what it sees. Re-running `scripts/provision_workflow_login.py` rotates its password.
- **Publishing needs the Runtime READY.** `scripts/publish.py` refuses to publish unless `MeridianWorkflow` is READY.

## App Runner hosting

- **Pin the CDK Region.** `infra/bin/meridian-web.ts` uses `MERIDIAN_WEB_REGION` (default `us-east-1`) instead of the shell's default Region.
- **App Runner needs the complete secret ARN**, with its six-character suffix, to read a Secrets Manager value at deployment; a partial ARN fails with "unable to retrieve secret from asm".
- **App Runner roles must exist before the service deploys.** A service whose instance or ECR access role was created seconds earlier failed with "Failed to deploy your application image" and no application log. The roles live in their own stack (`MeridianWebRoles`), which `scripts/publish.py` deploys first, and the publisher waits another 30 seconds for IAM changes to propagate before it updates the service.
- **The port must open quickly.** On one vCPU the backend needs close to thirty seconds to import its dependencies, and App Runner failed deployments whose port opened that late. `backend/launch.py` binds port 8000 immediately and hands the socket to uvicorn with `--fd`; early connections wait in the kernel backlog until startup completes.
- **App Runner does not run the start command through a shell.** Quotes are literal and `sh -c '...'` fails.
- **CloudTrail shows what CloudFormation sends.** `lookup-events` on `CreateService` shows the fields the resource handler adds, which helps when a service created by a stack fails and the same service created by the CLI works. The App Runner service itself is managed outside CloudFormation; `scripts/publish.py` updates it through the SDK.

## Gateway authorizer: what the first window showed

- **CloudFormation cannot change an existing Gateway's authorizer type.** The first coordinated window
  failed when `agentcore deploy -y` reported "Authorizer type cannot be updated for an existing
  gateway"; the stack rolled back cleanly. The resource documentation calls the change an in-place
  update, and the handler refuses it. The `UpdateGateway` API does it, so the release moves the Gateway
  with `release_identity.py gateway` first and deploys with `release_identity.py deploy`.
- **The interceptor is outside the template.** The installed `@aws/agentcore-cdk` never sets
  `InterceptorConfigurations`, and an update that omits it detaches the interceptor (harness check C5). The
  deploy command reads the Gateway back and re-applies the update when the deploy changed it.
- **CloudFormation compares the template with the deployed stack template, not with the live Gateway.**
  The second window's read-only diff planned `AWS_IAM` to `CUSTOM_JWT` against the stack template, which
  means moving the live Gateway through the API cannot make a `CUSTOM_JWT` template deployable (inferred, not
  yet confirmed by a deploy). The
  render therefore keeps the Gateway resource as the stack has it (`AWS_IAM`, no JWT block) in `jwt` mode, and
  `release_identity.py gateway` alone owns the live authorizer and interceptor. The live `CUSTOM_JWT`
  Gateway under an `AWS_IAM` template is the expected state. `deploy` refuses when its plan changes the
  Gateway authorizer.
- **The jwt deploy takes `InvokeGateway` off the Runtime roles**, and `UpdateAgentRuntime` does not put it
  back, so a rollback to IAM needs the IAM render deployed. `check --expect iam` reads both roles.
- **The holds role reads the gateway secret only after the first jwt deploy**, so the SSM parameter moves
  after it, and the parameter write refuses before.

## Gateway identity: what the harness measured

The throwaway-Gateway harness (`scripts/run_gateway_harness.py`) answered the questions the design depended on, on a
separate Gateway that it created and deleted, before anything was released. The rows are the harness's own.

| Row | Question | Measured |
| --- | --- | --- |
| Q1 | Does the interceptor run before Cedar? | Yes. Cedar saw the rewritten traveler id, the deny rule passed, and the target ran with the decoy's id. |
| Q2 | Does the Gateway re-validate rewritten arguments against the tool schema? | Not measured (UNKNOWN). Cedar denies a retyped or removed `travelerId` first, which masks the schema check. A required argument omitted by the caller is not rejected before the interceptor. Measuring it needs a Gateway whose engine holds only `permit_all`. |
| Q3 | Does any token claim reach the Lambda? | No. The target event is the argument object (`note`, `travelerId`); the client context holds only `bedrockAgentCore*` ids. No claim or token reaches the target. |
| Q4 | Does the interceptor replace a traveler the caller named? | PASS. A decoy token naming Jordan's id reached the target with the decoy's id. |
| Q5 | Does Cedar alone deny the decoy? | Yes. Cedar alone denied the decoy's call naming Jordan's id, so a Cedar-only release is available. |
| C1 | Does Jordan's own token reach the target as Jordan? | PASS |
| C2 | Does the Gateway accept the interceptor's refusal shape? | PASS. A 200 result with `isError` reaches the MCP client as a tool error whose text begins `Identity Check Failed: `. |
| C3 | Does the Policy validator accept the traveler-binding rule? | PASS. It accepts `forbid ... unless { hasTag && has travelerId && getTag != "" && getTag == input }` under `FAIL_ON_ANY_FINDINGS`. It rejected the earlier `!(hasTag) \|\| ...` form: the validator cannot carry the guard through the negated `\|\|`. |
| C4 | Do `initialize` and `tools/list` still work? | PASS. Both pass through the interceptor. |
| C5 | Does an update without the interceptor detach it? | PASS. `update_gateway` without `interceptorConfigurations` leaves no interceptor attached, so one call is the rollback. |

The design that shipped is `both`: the interceptor replaces `travelerId` with the token's traveler and Cedar denies a
mismatch, with `MERIDIAN_GATEWAY_ENFORCEMENT` unset. Cedar alone (`cedar`) is a valid fallback because of Q5. The
Cedar denial text was not stored by the run; it is known only as `Tool Execution Denied` in an `isError` result, so
the identity proof attributes a Gateway refusal by the change in `traveler_access_audit` deny rows or by a
recognised refusal text or code, and counts nothing else as a refusal. The identity proof records, in every receipt, which part of the
Gateway refused the decoy.

## Why two directories

- `meridian/` is the application: backend, frontend, tests and scripts.
- `meridian/meridian_agentcore/` is the AgentCore CLI project: the declarative
  configuration, the runtime and gateway target code, and the CDK app that
  deploys them.

The application reads deployed IDs from `.env` or from the CLI's deployment
state at run time.
