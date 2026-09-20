# Deployment, hardening and rehearsal follow-up

This runbook replaces the legacy publisher and provisioning instructions. Dated execution results are recorded in [the follow-up evidence report](FOLLOWUP_2026-09-20.md). A source push runs CI; it does not deploy the hosted application.

## Established hosted release

The publisher updates an **existing** App Runner service and three existing CDK stacks. The exact target account, region and service ARN are mandatory. It builds the frontend for same-origin requests, synthesizes and diffs the infrastructure before any deployment, grants the instance role access to the origin token secret, publishes the image, checks the App Runner operation ID and actual configuration, then deploys the site and response-header policy. It never creates/deletes App Runner services, rotates credentials, writes secret values to disk, or changes the CloudFront access store. Its non-secret receipt is `.local/hosted-release.json`; `deployed_pending_verification` is not an E2E pass.

```bash
cd meridian
python scripts/publish.py --account <account-id> --region us-east-1 --service-arn <existing-service-arn>
# Review the diff, then execute the same target:
python scripts/publish.py --account <account-id> --region us-east-1 --service-arn <existing-service-arn> --apply
```

App Runner receives `MERIDIAN_API_TOKEN` through `RuntimeEnvironmentSecrets`, referencing the existing `meridian/web/api-token` secret. The edge retains its current credential and token. An absent service, absent stack/secret, non-running service, wrong account/region or failed deployment stops the release. A rollback to RUNNING does not count as success. Review a rollback against the previous image in the receipt; the tool never deletes a failed service to retry it.

The established workload grant remains necessary: `scripts/bind_web_backend_role.py` binds the instance role to the demo traveler. Releasing an image does not create a new grant. Runtime and Gateway have their own grants.

For authenticated verification, load the repository-required [AWS Secrets Manager skill](https://github.com/aws/agent-toolkit-for-aws/blob/main/plugins/aws-core/skills/aws-secrets-manager/SKILL.md) and use its `asm-exec` wrapper. Do not fetch values into a terminal or agent context. `MERIDIAN_HOSTED_AUTH` is a runtime reference to a JSON secret with `username` and `password`; it is never a committed credential. The established installation uses `meridian/web/presenter` (imported from the preexisting local record without changing edge access).

```bash
AWS_REGION=us-east-1 \
MERIDIAN_HOSTED_AUTH='{{resolve:secretsmanager:meridian/web/presenter:SecretString}}' \
asm-exec -- python scripts/validate_demo.py \
  --base-url https://<distribution>.cloudfront.net --allow-hosted-demo-writes \
  --output .local/hosted-validation.json
```

This command creates real **demo** bookings and removes only its owned rows. Configure local Aurora settings for the exact same database as the hosted backend before running it. Validate image identity, exact frontend asset bytes, protected `/api/health`, security headers, an unauthenticated 401 and the 32-step demo separately. Never log request authorization headers or browser storage state.

## Provisioning prerequisites and protected Aurora templates

Run the read-only preflight against the intended target:

```bash
python scripts/provision_preflight.py --account <account-id> --region us-east-1 \
  --vpc-id <vpc-id> --subnet-ids <subnet-in-az-a> <subnet-in-az-b> \
  --engine-version 18.3 --output .local/provision-preflight.json
# Before planning a migration, add --source-cluster <existing-cluster-id>.
```

It checks identity, network/AZ/IP headroom, orderable serverless engine version, RDS quotas, the RDS service-linked role, CDK bootstrap and a scoped deployer PassRole simulation. A missing or denied check fails. IAM simulation does not establish organization SCPs, session policies, execution-role permissions, or a successful fresh-account lifecycle. Confirm Data API regional/version support, then exercise bootstrap, migrations, seeding, rollback and teardown against an isolated target.

**New-account hosting caveat:** [App Runner stopped accepting new customers on March 31, 2026](https://docs.aws.amazon.com/apprunner/latest/dg/what-is-apprunner.html). This established publisher is not a fresh-account hosting solution. A new customer needs a separately reviewed hosting target, such as ECS/Fargate, before claiming the full application can be provisioned there.

`infra/bin/meridian-aurora.ts` is a separate CDK entry point. It creates an encrypted PostgreSQL 18 cluster, a private Serverless v2 writer, Data API, TLS enforcement, no inbound database port, seven-day backups, 0.5-16 ACUs, deletion/termination protection and retained resources. Fresh creation uses an RDS-managed master secret. A restore retains the snapshot's database users and requires the existing application secret reference. Neither path reads a secret into CDK or redirects the app.

```bash
cd infra
npm ci && npm run build
npx cdk synth --app 'node dist/bin/meridian-aurora.js' \
  -c account=<account-id> -c region=us-east-1 \
  -c clusterIdentifier=<new-isolated-name> -c vpcId=<vpc-id> \
  -c subnetIds=<subnet-in-az-a>,<subnet-in-az-b> -c engineVersion=18.3
```

Use `diff` with the same arguments to review creation before `deploy`. The old `create_cluster.sh --apply` intentionally remains disabled; use this reviewed IaC entry point. Do not reuse an existing cluster identifier. Teardown is deliberate: termination/deletion protection must be reviewed and disabled, retained writer/network/parameter resources must be accounted for, and final snapshots must be preserved or explicitly removed. `cdk destroy` is not a promise that all retained billable resources disappear.

## Database migration and encryption assessment

**Check `StorageEncryptionType` before proposing a migration.** `sse-rds` means encrypted with an AWS-owned key, even when the legacy `StorageEncrypted` boolean is false. `sse-kms` means encryption with an AWS-managed or customer-managed KMS key; only `none` means unencrypted. The preflight's optional source inspection reports this distinction and refuses to infer an encryption mode from the boolean alone. [RDS API definitions](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_DBCluster.html).

The established `meridian-demo` cluster and its rollback snapshot report `sse-rds`: both were already encrypted. The earlier assessment was incorrect. Its migration to `meridian-encrypted` changes the key mode to `sse-kms` with `alias/aws/rds`; it was not necessary to obtain encryption at rest. Check organization key-management requirements before choosing a different key mode. Aurora's default AWS-owned encryption is a supported option. [Aurora encryption guidance](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Overview.Encryption.html).

When a snapshot migration is actually needed and authorized, preserve all journeys, checkpoints, identity bindings and business records:

1. Schedule a write-freeze window with the operator. Inventory every writer: hosted App Runner, all local backend listeners, AgentCore Runtime and Gateway Lambdas. Drain in-flight requests and prevent new writes on every entry point. Merely stopping the browser is insufficient. This installation pauses App Runner, sets both Gateway Lambda targets to zero reserved concurrency, stops the local backends, and fences Data API access to the source for workload roles.
2. Record source cluster configuration, encryption type and non-secret application bindings. Set the database's default transactions read-only and terminate preexisting application sessions from the separate `postgres` database. Record per-table counts/content hashes and metadata hashes for columns, RLS, policies, grants, roles and extensions. Disable new connections to the source's `meridian` database and terminate its remaining sessions before the final manual cluster snapshot. Wait for `available`. Retain the source and snapshot for rollback, recording their actual encryption types and ongoing costs.
3. Synthesize the isolated Aurora stack with `clusterIdentifier=meridian-encrypted` and `snapshotArn=<final-snapshot-arn>` in the same account, VPC, subnets and region. Review creation-only changes; deploy with encryption enabled. This temporarily runs two clusters. Never replace or delete the source in this step.
4. Verify `StorageEncrypted`, private writer, Data API, TLS, database users/RLS/grants, exact catalog/booking/journey counts and checkpoint/lease contents on the replacement. A snapshot also retains database connection/read-only settings: connect to `postgres` on the replacement to allow `meridian` connections, preserving the read-only default during the comparison. Test reads with the existing application secret ARN; do not reveal or rotate its value. Run isolated transaction/rollback checks. No reseeding.
5. Update the non-secret cluster ARN in local configuration, scoped IAM resources, and `/meridian/aurora/cluster_arn` for Gateway targets. The existing Runtime uses Gateway and has no direct database binding. Update the externally provisioned semantic-search Lambda's environment and its existing role's database/secret scopes. Account for the holds Lambda's cached SSM configuration: a non-secret `MERIDIAN_CONFIGURATION_REVISION` environment update forces a new configuration revision. Wait for each Lambda update to succeed. Rebind only if a workload identity actually changed. Deploy the established web service through the publisher.
6. With writes still frozen, verify that every consumer uses the replacement and execute one scoped recovery/hold/confirmation rehearsal. Reopen writes only after verification. **Rollback before reopening** restores the original bindings. After new writes reach the replacement, a simple switch back would lose those writes; reconcile or migrate them before rollback.
7. Keep the source closed to application connections and protected for an agreed rollback period. Schedule its final snapshot/retirement separately, including retained snapshots, logs, secrets and costs. The application credential remains shared with the restored cluster: `cleanup_resources.py` deliberately retains it even after source retirement. Do not mark encryption complete while consumers still write to the source.

Deploy AgentCore from its schema with the repository's CDK CLI (`node node_modules/aws-cdk/bin/cdk` from `meridian_agentcore/agentcore/cdk`). That package also defines its own `cdk` executable; an unqualified `npx cdk` can resolve the application executable instead of the AWS CDK CLI. Check the synthesized template and actual CloudFormation operation, not only the command's exit code.

Deletion protection, backup retention and the capacity ceiling can be hardened in place without this migration. Changing the Serverless v2 maximum may leave `max_connections` pending reboot; [capacity changes apply immediately, while derived static parameters need a reboot](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2.setting-capacity.html). Do not force that reboot during presentation preflight.

## Finch recovery

Before another build: check host free space, `finch vm status`, `finch ps -a` and `finch images`. Identify shared workloads before stopping the VM. With no running workloads and sufficient host headroom, `finch vm stop` followed by `finch vm start` can recover a guest remounted read-only after host disk exhaustion. Verify a container can write `/tmp`, then build the actual backend and load its MCP module offline:

```bash
finch build --platform linux/amd64 -t meridian-recovery .
finch run --rm --network none --entrypoint python meridian-recovery \
  -m awslabs.postgres_mcp_server.server --help
```

The publisher requires at least 5 GiB free on the host before building. This is a minimum guard, not a guarantee of guest capacity. Monitor both. Do not run blanket image/volume pruning or reset/delete the shared VM. If a restart does not recover writes, preserve the VM and investigate disk/filesystem health before another build. [Finch configuration reference](https://runfinch.com/docs/configuration-reference/).

## Accessibility and timed human rehearsal

`cd frontend && npm run test:accessibility` exercises all five surfaces in light/dark themes at 1366, 640 and 320 CSS pixels, plus keyboard display settings and live reduced-motion switching. Default CI tests deliberately unavailable service states; set `MERIDIAN_A11Y_LIVE=1` for real APIs and `MERIDIAN_A11Y_URL` for a chosen deployment. Hosted credentials are resolved with `asm-exec`; authenticated runs disable JSON reporting and all runs disable traces. Axe is an automated check, not screen-reader or accessible-PDF certification. Narrow viewports establish reflow, not native browser zoom.

Use [the presenter runbook](PRESENTER_RUNBOOK_2026-09-20.md) for the exact talk inputs. Record real elapsed times in the worksheet below; do not fill it from HTTP timings. Confirm event title, session code and speakers with the presenter.

| Segment | Target | Actual start/end | Presenter observation |
| --- | --- | --- | --- |
| Traveler problem and architecture | 0:00-5:00 | Pending | Back-row legibility, terminology |
| SQL, MCP and retrieval ladder | 5:00-17:00 | Pending | Actual narration and live response waits |
| Workload identity, RLS and memory | 17:00-25:00 | Pending | Distinguish context from authority |
| Pause, recovery and persisted receipt | 25:00-35:00 | Pending | Distinguish same-worker UI resume from SIGKILL proof |
| Tradeoffs, cuts and close | 35:00-40:00 | Pending | Finish within planned core |
| Discussion/flex | 40:00-60:00 | Pending | Reserve remains available |

Before calling the room ready, a person must verify actual projector/back-row/fullscreen legibility; keyboard-only and screen-reader task completion; zoom and motion preferences on the presentation browser; venue network and access duration; accessible deck/PDF reading order and event-specific requirements. Automated Chromium zoom at 200%/400% and live reduced-motion changes are recorded in the follow-up report, but do not establish human task completion. Confirm fallback assets and a scoped reset/cleanup. Human checks remain open until the presenter records evidence.
