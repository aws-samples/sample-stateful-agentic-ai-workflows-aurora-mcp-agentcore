# Meridian AgentCore project

This directory is an [AgentCore CLI](https://github.com/aws/agentcore-cli) project.
It declares the Amazon Bedrock AgentCore resources Meridian's Production and
Workflow phases use, and deploys them with the CLI's CDK app. The Production
phase runs in `MeridianConcierge`. The Workflow phase runs in `MeridianWorkflow`.

| Resource | Name | Purpose |
| --- | --- | --- |
| Runtime | `MeridianConcierge` | Strands agent that plans with Gateway tools and keeps its conversation in Memory |
| Runtime | `MeridianWorkflow` | Runs the Phase 5 workflow as the `meridian_workflow` database login and resumes a stopped session from saved snapshots on a new microVM |
| Memory | `meridian_session` | Session store for the runtime (`SEMANTIC` strategy) |
| Gateway | `meridian-aurora` | MCP endpoint with AWS_IAM inbound auth and the Cedar policy engine attached |
| Gateway target | `SemanticTripSearchLambda` | Existing Lambda function `meridian-semantic-trip-search` (you create it; see the runbook) |
| Gateway target | `MeridianHolds` | Lambda built by the CDK app: `get_package_details`, `create_courtesy_hold`, `confirm_booking` |
| Policy engine | `MeridianGovernance` | Cedar policies in `ENFORCE` mode: reads are permitted; holds and confirmations only within the stated conditions |

## Layout

```text
meridian_agentcore/
├── agentcore/
│   ├── agentcore.template.json    # Project spec with placeholders (committed)
│   ├── aws-targets.template.json  # Deployment target with placeholders (committed)
│   ├── agentcore.json             # Rendered for your account (gitignored)
│   ├── aws-targets.json           # Rendered for your account (gitignored)
│   ├── .cli/deployed-state.json   # Written by `agentcore deploy` (gitignored)
│   ├── cdk/                       # CDK app the CLI synthesizes and deploys
│   └── gateway_targets/           # Lambda code and tool schemas for both gateway targets
├── app/MeridianConcierge/         # Runtime code: main.py, turn_trace.py, hold_execution.py
└── app/MeridianWorkflow/          # Runtime code: main.py; backend/ is staged on render (gitignored)
```

## Configuration templates

The AgentCore CLI and its CDK app read `agentcore/agentcore.json` and
`agentcore/aws-targets.json`. Those files name your AWS account, Region,
Aurora cluster and secret, and IDs that exist only after a deploy (the gateway
ID in the Cedar policies and the runtime's environment). The repository
commits templates with placeholders instead, and
[`scripts/render_agentcore_config.py`](../scripts/render_agentcore_config.py)
writes the real files:

| Placeholder | Filled from |
| --- | --- |
| `{{AWS_ACCOUNT_ID}}` | The account in `AURORA_CLUSTER_ARN` |
| `{{AWS_REGION}}` | `AGENTCORE_REGION`, else `AWS_DEFAULT_REGION`, else the cluster's Region |
| `{{AURORA_CLUSTER_ARN}}` | `AURORA_CLUSTER_ARN` in `meridian/.env` |
| `{{AURORA_SECRET_ARN}}` | `AURORA_SECRET_ARN` in `meridian/.env` (the full ARN, with its six-character suffix) |
| `{{AURORA_WORKFLOW_SECRET_ARN}}` | `AURORA_WORKFLOW_SECRET_ARN` in `meridian/.env`, the `meridian_workflow` login's secret |
| `{{AURORA_GATEWAY_SECRET_ARN}}` | `AURORA_GATEWAY_SECRET_ARN` in `meridian/.env`, the `meridian_gateway` login's secret |
| `{{GATEWAY_ID}}` | `agentcore/.cli/deployed-state.json`, or `--gateway-id` |
| `{{POLICY_ENGINE_ID}}` | `agentcore/.cli/deployed-state.json`, or `--policy-engine-id` |
| `{{MERIDIAN_AGENTCORE_AUTH}}` | `MERIDIAN_AGENTCORE_AUTH` in `meridian/.env`: `iam` (the default) or `jwt` |

```bash
cd meridian
python scripts/render_agentcore_config.py
```

Environment variables take precedence over `meridian/.env`. The script refuses
to write when a required value is missing or malformed, or when
`deployed-state.json` does not have the shape the CLI writes, and names the
setting or key to fix. It also refuses a policy engine ID without a gateway ID,
because the engine's policies name the gateway.

The Cedar policies name the deployed gateway, so a new account needs more than
one deploy. Before the gateway exists, the script renders the spec without the
policy engine and without the gateway ID variable. After each
`agentcore deploy`, run it again: it reads the new IDs from the deployment
state and prints `Configuration complete.` once nothing is left out. The
[deployment runbook](../docs/AGENTCORE_DEPLOY_RUNBOOK.md) lists the full
sequence.

Make configuration changes in the `*.template.json` files, then render.
`agentcore add` and `agentcore remove` edit the rendered `agentcore.json`; copy
any such change into the template, replacing account-specific values with
placeholders, or the next render overwrites it.

## Identity mode

`MERIDIAN_AGENTCORE_AUTH` is `iam` (the default) or `jwt`. In `iam` mode the backend and both
Runtimes sign every AgentCore call with AWS credentials, which is how the deployed release works
today. In `jwt` mode every hop carries the signed-in person's Cognito access token instead. The
backend posts to each Runtime's invocation URL with a bearer token, each Runtime forwards the token
to the Gateway, the Gateway's request interceptor
(`agentcore/interceptors/traveler_pin/`) pins `travelerId` to the token's `traveler_id` claim, and
Cedar denies a mismatch. The render script writes the value into both Runtimes' environment, and in
`jwt` mode it adds the `meridian_traveler_binding` policy.
The policy is a `forbid ... unless` rule whose condition is one `&&` chain that starts with the
`hasTag` and `has` guards, because the AgentCore Policy validator rejects a negated `hasTag` guard
joined with `||`. An empty `traveler_id` claim is denied.

Do not set `jwt` by itself on a deployed system. A Runtime accepts IAM or a JWT, never both, so the
Gateway authorizer, both Runtime authorizers and header allowlists, the interceptor attachment and
the backend's environment change together in one release.
CloudFormation cannot change a deployed Gateway's authorizer type, so in `jwt` mode the render names the
Gateway `meridian-aurora-jwt` with a `CUSTOM_JWT` authorizer: a new resource, built in four staged deploys
through `scripts/release_identity.py deploy`, never with a bare `agentcore deploy -y`. The stack deletes the
`iam` Gateway in the first stage. The release attaches the interceptor afterwards with
`scripts/release_identity.py gateway`.
`scripts/run_gateway_harness.py` tests the Gateway behavior first, on a separate throwaway Gateway.
The release steps and what stays unswitched until then are in
[Operations](../docs/OPERATIONS.md#switch-the-agentcore-identity-mode).

## Commands

Run these from `meridian/meridian_agentcore/` after rendering:

| Command | Purpose |
| --- | --- |
| `agentcore validate --json` | Check the rendered spec against the CLI schema |
| `agentcore deploy -y` | Synthesize and deploy the CDK stack |
| `agentcore status --json` | Show deployed resources |
| `agentcore logs --runtime MeridianConcierge --follow` | Tail runtime logs |
| `agentcore logs --runtime MeridianWorkflow --follow` | Tail the workflow runtime's logs |

The CDK unit test in `agentcore/cdk/test/` synthesizes the stack from the
template with placeholder test values, so it runs without an AWS account.

## Documentation

- [AgentCore CLI](https://github.com/aws/agentcore-cli)
- [AgentCore CDK constructs](https://github.com/aws/agentcore-l3-cdk-constructs)
- [Amazon Bedrock AgentCore Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)
