# Deploy the AgentCore resources

This runbook deploys the Amazon Bedrock AgentCore resources that Meridian's
Production phase, the Concierge chat, and every hold and booking confirmation
use. Every command block starts in the repository root and leaves you there;
commands that need another directory run in a subshell, `( cd ... )`.

## What gets deployed

| Resource | Name | Purpose |
| --- | --- | --- |
| Runtime | `MeridianConcierge` | Strands agent that plans with the gateway tools, keeps its session in Memory and streams its trace to the backend |
| Memory | `meridian_session` | Runtime session store, `SEMANTIC` strategy over `/users/{actorId}/sessions/{sessionId}` |
| Gateway | `meridian-aurora` | MCP endpoint with AWS_IAM inbound auth and the Cedar policy engine attached in `ENFORCE` mode |
| Gateway target | `SemanticTripSearchLambda` | `semantic_trip_search(query, limit)` over Aurora pgvector, served by the Lambda function you create in step 2 |
| Gateway target | `MeridianHolds` | Lambda built by the CDK app: `get_package_details`, `create_courtesy_hold` and `confirm_booking` |
| Policy engine | `MeridianGovernance` | `meridian_read_tools` permits the reads; `meridian_hold_governance` permits a hold only when confirmed, at most 12 hours, at most 6 travelers and within budget; `meridian_booking_governance` permits a confirmation only when confirmed and within budget |

The resources are declared in
[`meridian_agentcore/agentcore/agentcore.template.json`](../meridian_agentcore/agentcore/agentcore.template.json).
The AgentCore CLI deploys them as one CloudFormation stack,
`AgentCore-meridianv2-default`.

## Prerequisites

- The Aurora database is prepared (schema, migrations and seed data) as in the
  [repository README](../../README.md#1-configure-and-prepare-the-database), and
  `meridian/.env` sets `AWS_DEFAULT_REGION`, `AURORA_CLUSTER_ARN`,
  `AURORA_SECRET_ARN` (the full ARN, with its six-character suffix) and
  `AURORA_DATABASE`.
- AWS credentials for the account that owns the cluster. Sign in with
  `aws login` or `aws sso login`, then confirm with
  `aws sts get-caller-identity`.
- The AgentCore CLI, Node.js 20 or later, and
  [uv](https://docs.astral.sh/uv/getting-started/installation/), which the CLI
  uses to package the Python runtime:

  ```bash
  npm install -g @aws/agentcore
  agentcore --version
  ```

- Deploy AgentCore in the Aurora cluster's Region. The defaults assume
  `us-east-1`: the rerank model uses the `us.` cross-Region inference profile.
  In another Region, set `BEDROCK_MODEL_ID` and `RERANK_MODEL` to models
  available there.

From the repository root, activate the backend's virtual environment and set
these shell variables for the commands below:

```bash
source meridian/venv/bin/activate
export REGION=us-east-1                                      # the cluster's Region
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export CLUSTER_ARN=<AURORA_CLUSTER_ARN from meridian/.env>
export SECRET_ARN=<AURORA_SECRET_ARN from meridian/.env>
```

## 1. Render the configuration

The AgentCore CLI reads `agentcore/agentcore.json` and
`agentcore/aws-targets.json` in `meridian/meridian_agentcore/`, which are
generated from the committed templates for your account:

```bash
python meridian/scripts/render_agentcore_config.py
```

On a new account the script reports that it left out the gateway ID variables
and the Cedar policy engine: the policies name the gateway, which does not
exist yet. The [AgentCore project README](../meridian_agentcore/README.md)
describes each placeholder.

## 2. Create the semantic search Lambda

The `SemanticTripSearchLambda` target points at an existing function named
`meridian-semantic-trip-search`. Create it once, with an execution role that
can call Bedrock, the Data API and the database secret:

```bash
TARGET=meridian/meridian_agentcore/agentcore/gateway_targets/semantic_trip_search

aws iam create-role --role-name meridian-semantic-trip-search \
  --assume-role-policy-document "file://${TARGET}/trust-policy.json"
aws iam attach-role-policy --role-name meridian-semantic-trip-search \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam put-role-policy --role-name meridian-semantic-trip-search \
  --policy-name meridian-semantic-trip-search --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {\"Effect\": \"Allow\", \"Action\": \"bedrock:InvokeModel\",
       \"Resource\": \"arn:aws:bedrock:${REGION}::foundation-model/cohere.embed-v4:0\"},
      {\"Effect\": \"Allow\", \"Action\": \"rds-data:ExecuteStatement\", \"Resource\": \"${CLUSTER_ARN}\"},
      {\"Effect\": \"Allow\", \"Action\": \"secretsmanager:GetSecretValue\", \"Resource\": \"${SECRET_ARN}\"}
    ]}"

zip -j semantic-trip-search.zip "${TARGET}/lambda_function.py"
aws lambda create-function --region "$REGION" \
  --function-name meridian-semantic-trip-search \
  --runtime python3.13 --handler lambda_function.lambda_handler \
  --role "arn:aws:iam::${ACCOUNT_ID}:role/meridian-semantic-trip-search" \
  --zip-file fileb://semantic-trip-search.zip --timeout 30 --memory-size 512 \
  --environment "{\"Variables\": {\"AURORA_CLUSTER_ARN\": \"${CLUSTER_ARN}\",
    \"AURORA_SECRET_ARN\": \"${SECRET_ARN}\", \"AURORA_DATABASE\": \"meridian\"}}"
rm semantic-trip-search.zip
```

Set `AURORA_DATABASE` to your database name if it is not `meridian`. If
`create-function` reports that the role cannot be assumed, wait a few seconds
for the new role to propagate and run it again, from the `zip` line on. To
deploy a code change later, run the `TARGET=` and `zip -j` lines again, then
`aws lambda update-function-code --region "$REGION" --function-name meridian-semantic-trip-search --zip-file fileb://semantic-trip-search.zip`.
The CDK app grants the gateway role permission to invoke the function.

## 3. Publish the holds Lambda's settings

The CDK-built `MeridianHolds` Lambda has no environment variables. It reads the
cluster ARN, secret ARN and database name from SSM Parameter Store under
`/meridian/aurora/`, and its IAM policy in the template is scoped to those
parameters, the cluster and the secret:

```bash
python meridian/scripts/publish_gateway_parameters.py
```

## 4. Bootstrap CDK

Once per account and Region:

```bash
(
  cd meridian/meridian_agentcore/agentcore/cdk
  npm ci
  npm run cdk -- bootstrap "aws://${ACCOUNT_ID}/${REGION}"
)
```

## 5. Deploy

A new account needs three passes, because each one creates an ID the next
render needs. Run each pass from the repository root:

```bash
python meridian/scripts/render_agentcore_config.py
(cd meridian/meridian_agentcore && agentcore validate --json && agentcore deploy -y)
```

1. **First pass.** Creates the runtime, memory, gateway and both targets,
   without policies. Expect about 5 to 8 minutes.
2. **Second pass.** The render now finds the gateway ID in
   `meridian/meridian_agentcore/agentcore/.cli/deployed-state.json` and adds
   the policy engine, its three Cedar policies and the gateway association.
3. **Third pass.** The render adds the policy engine ID to the runtime's
   environment and prints `Configuration complete.` The deploy updates the
   runtime so its trace names the engine.

`agentcore deploy` writes the deployed ARNs and IDs to
`meridian/meridian_agentcore/agentcore/.cli/deployed-state.json`. If that file
is missing, for example on another machine, pass the IDs to
`render_agentcore_config.py` with `--gateway-id` and `--policy-engine-id`.

## 6. Grant the holds Lambda access to the demo traveler

The `MeridianHolds` Lambda is a workload: before it sets a traveler scope it
needs its own row in `traveler_identity_bindings`.

```bash
python meridian/scripts/bind_gateway_workload.py
```

## 7. Point the backend at the deployment

```bash
python meridian/scripts/sync_agentcore_env.py --write
```

This writes `AGENTCORE_RUNTIME_ARN`, `AGENTCORE_GATEWAY_URL`,
`AGENTCORE_MEMORY_ID`, `AGENTCORE_REGION` and the related names into
`meridian/.env`. Restart the backend; the next Phase 4 turn calls the deployed
runtime. The backend also reads the deployment state directly through
[`backend/agentcore/cli_config.py`](../backend/agentcore/cli_config.py);
variables in `.env` take precedence.

## Verify

From the repository root:

```bash
python meridian/scripts/verify_agentcore.py        # runtime, gateway, memory, policy engine ACTIVE and ENFORCE, four tools
python meridian/scripts/smoke_gateway_tools.py CTY-002   # tools/list and get_package_details, signed with your credentials
python meridian/scripts/smoke_production_turn.py   # search, unconfirmed hold denied, confirmed hold placed, over-budget hold denied
```

`smoke_production_turn.py` places one real 12-hour hold on a Tokyo package for
the demo traveler and saves every event under `meridian/.local/verification/`.
Holds expire on their own.

In the showcase trace for a Production turn, expect:

- `AgentCore Identity resolved` and `Workload traveler grant allowed`
- `AgentCore Runtime · turn started`, then `AgentCore Gateway · tools/list` with four tools
- `AgentCore Memory · session restored` with the event count
- `AgentCore Gateway · tools/call → semantic_trip_search` and its result
- For a hold: `tools/call → create_courtesy_hold`, its result with the Lambda's workload subject and `traveler_grant: allow`, and the hold receipt
- For a confirmation: `tools/call → confirm_booking` under `meridian_booking_governance`, and a **Confirmed booking** receipt with the same booking ID
- For a hold the policy refuses: `Hold refused by Cedar policy · Denied by policy`
- `AgentCore Runtime · turn complete` with the trace ID and a CloudWatch link

Spans for a trace ID are in the runtime log group
`/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`, stream `spans`, and the
application logs carry the same trace ID in the `runtime-logs-*` streams.
Trace indexing can take about ten minutes to start after the first deploy.

Tail the runtime while you use the app:

```bash
(cd meridian/meridian_agentcore && agentcore logs --runtime MeridianConcierge --follow)
```

## Change the deployment

Edit `meridian/meridian_agentcore/agentcore/agentcore.template.json`, then
render, validate and deploy as in step 5. A resource's `name` becomes its
CloudFormation logical ID, so renaming a resource replaces it; renaming
`meridian_session` replaces the memory and discards its history.
Changes to other fields update resources in place.

Adding a gateway tool together with a policy that names it takes two deploys:
the tool first, then the policy, because a policy is validated against the
gateway's tool schema.

## Tear down

Resources incur charges while they exist. Delete the AgentCore stack, then the
resources you created by hand:

```bash
aws cloudformation delete-stack --region "$REGION" --stack-name AgentCore-meridianv2-default
aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name AgentCore-meridianv2-default

aws lambda delete-function --region "$REGION" --function-name meridian-semantic-trip-search
aws iam delete-role-policy --role-name meridian-semantic-trip-search --policy-name meridian-semantic-trip-search
aws iam detach-role-policy --role-name meridian-semantic-trip-search \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name meridian-semantic-trip-search
aws ssm delete-parameters --region "$REGION" \
  --names /meridian/aurora/cluster_arn /meridian/aurora/secret_arn /meridian/aurora/database
```

The stack name is recorded as `stackName` in
`meridian_agentcore/agentcore/.cli/deployed-state.json`. The runtime and Lambda
log groups may remain after deletion; delete them in CloudWatch Logs if you do
not need them. The `traveler_identity_bindings` rows for deleted roles stay in
Aurora until you remove them or delete the database.
