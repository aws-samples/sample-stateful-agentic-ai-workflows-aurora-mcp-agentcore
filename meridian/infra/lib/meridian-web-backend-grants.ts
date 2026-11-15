import { Stack, aws_iam as iam } from 'aws-cdk-lib';
import { backendModelStatements } from './bedrock-model-access';

/**
 * What the Meridian backend's workload role may do, whichever service runs the container:
 * Amazon Bedrock models, the Aurora Data API on the cluster, Secrets Manager reads, the
 * MeridianConcierge Runtime and the MeridianWorkflow Runtime.
 *
 * The App Runner instance role and the Amazon ECS task role both get exactly these statements,
 * in this order, so the two hosting paths cannot drift apart.
 *
 * @param role - The workload role to grant.
 * @param stack - The stack that owns the role; its Region pins the Bedrock ARNs.
 * @param environment - The service environment from serviceEnvironment().
 * @param secretGrants - The Secrets Manager secret ARNs the workload may read.
 */
export function grantBackendAccess(
  role: iam.Role,
  stack: Stack,
  environment: Record<string, string>,
  secretGrants: string[],
): void {
  for (const statement of backendModelStatements(environment, stack)) {
    role.addToPolicy(statement);
  }
  role.addToPolicy(
    new iam.PolicyStatement({
      actions: [
        'rds-data:ExecuteStatement',
        'rds-data:BatchExecuteStatement',
        'rds-data:BeginTransaction',
        'rds-data:CommitTransaction',
        'rds-data:RollbackTransaction',
      ],
      resources: [environment.AURORA_CLUSTER_ARN],
    }),
  );
  role.addToPolicy(
    new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: secretGrants,
    }),
  );
  // The meridian_backend login's own secret and Data API access, created by
  // scripts/provision_service_logins.py. The key is written to meridian/.env only after that
  // policy exists.
  if (environment.AURORA_BACKEND_SECRET_ARN) {
    role.addManagedPolicy(
      iam.ManagedPolicy.fromManagedPolicyArn(
        stack, 'BackendLoginAccess',
        `arn:aws:iam::${stack.account}:policy/MeridianBackendAuroraAccess`,
      ),
    );
  }
  role.addToPolicy(
    new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeAgentRuntime'],
      resources: [environment.AGENTCORE_RUNTIME_ARN, `${environment.AGENTCORE_RUNTIME_ARN}/runtime-endpoint/*`],
    }),
  );
  // Phase 5 runs on the MeridianWorkflow Runtime: the backend invokes it and stops its sessions.
  role.addToPolicy(
    new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeAgentRuntime', 'bedrock-agentcore:StopRuntimeSession'],
      resources: [
        environment.AGENTCORE_WORKFLOW_RUNTIME_ARN,
        `${environment.AGENTCORE_WORKFLOW_RUNTIME_ARN}/runtime-endpoint/*`,
      ],
    }),
  );
}
