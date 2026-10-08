import { ArnFormat, CfnOutput, Stack, type StackProps, aws_iam as iam } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { API_TOKEN_SECRET_NAME } from './meridian-web-stack';

export interface MeridianWebRolesStackProps extends StackProps {
  /** The App Runner environment from serviceEnvironment(); supplies the ARNs the role may touch. */
  environment: Record<string, string>;
  /**
   * The master login's secret, in the jwt release only. The service no longer uses it, but the role
   * keeps the grant (and the shared-token secret's) until the tighten release, so a rollback to the
   * iam release still starts.
   */
  masterSecretArn?: string;
  /** Drop the master and shared-token grants. Valid only with `masterSecretArn`. */
  tighten?: boolean;
}

/**
 * The App Runner roles, in their own stack.
 *
 * App Runner pulls the image with the access role and hands the container its
 * credentials through the instance role while it deploys the service. When either role is created in the same CloudFormation
 * deployment as the service, App Runner fails with "Failed to deploy your
 * application image" and no application log, because IAM has not propagated
 * the role yet; the same service definition succeeds against roles that have
 * existed for a minute. scripts/publish.py deploys this stack first, waits
 * whenever it created or changed it, and creates the service with these roles.
 */
export class MeridianWebRolesStack extends Stack {
  readonly instanceRole: iam.Role;
  readonly accessRole: iam.Role;

  constructor(scope: Construct, id: string, props: MeridianWebRolesStackProps) {
    super(scope, id, props);
    const { environment, masterSecretArn, tighten } = props;
    if (tighten && !masterSecretArn) {
      throw new Error('tighten applies only to the jwt release; there is no master grant to drop in iam mode');
    }
    const apiTokenSecretArn = this.formatArn({
      service: 'secretsmanager', resource: 'secret',
      resourceName: `${API_TOKEN_SECRET_NAME}-??????`, arnFormat: ArnFormat.COLON_RESOURCE_NAME,
    });
    const secretGrants = masterSecretArn
      ? (tighten
        ? [environment.AURORA_SECRET_ARN]
        : [environment.AURORA_SECRET_ARN, masterSecretArn, apiTokenSecretArn])
      : [environment.AURORA_SECRET_ARN, apiTokenSecretArn];

    this.instanceRole = new iam.Role(this, 'BackendRole', {
      assumedBy: new iam.ServicePrincipal('tasks.apprunner.amazonaws.com'),
      description: 'Meridian backend on App Runner: Bedrock, Aurora Data API, the MeridianConcierge and MeridianWorkflow AgentCore Runtimes',
    });
    this.instanceRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:CountTokens'],
        resources: [
          'arn:aws:bedrock:*::foundation-model/*',
          `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
        ],
      }),
    );
    this.instanceRole.addToPolicy(
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
    this.instanceRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['secretsmanager:GetSecretValue'],
        resources: secretGrants,
      }),
    );
    // The meridian_backend login's own secret and Data API access, created by
    // scripts/provision_service_logins.py. The key is written to meridian/.env only after that
    // policy exists. The service keeps running as the master login until the cutover release
    // points its AURORA_SECRET_ARN here and drops the master secret from the statement above.
    if (environment.AURORA_BACKEND_SECRET_ARN) {
      this.instanceRole.addManagedPolicy(
        iam.ManagedPolicy.fromManagedPolicyArn(
          this, 'BackendLoginAccess',
          `arn:aws:iam::${this.account}:policy/MeridianBackendAuroraAccess`,
        ),
      );
    }
    this.instanceRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [environment.AGENTCORE_RUNTIME_ARN, `${environment.AGENTCORE_RUNTIME_ARN}/runtime-endpoint/*`],
      }),
    );
    // Phase 5 runs on the MeridianWorkflow Runtime: the backend invokes it and stops its sessions.
    this.instanceRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime', 'bedrock-agentcore:StopRuntimeSession'],
        resources: [
          environment.AGENTCORE_WORKFLOW_RUNTIME_ARN,
          `${environment.AGENTCORE_WORKFLOW_RUNTIME_ARN}/runtime-endpoint/*`,
        ],
      }),
    );

    this.accessRole = new iam.Role(this, 'BackendAccessRole', {
      assumedBy: new iam.ServicePrincipal('build.apprunner.amazonaws.com'),
      description: 'Meridian backend on App Runner: pull the image from the CDK assets repository',
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSAppRunnerServicePolicyForECRAccess')],
    });

    new CfnOutput(this, 'InstanceRoleArn', { value: this.instanceRole.roleArn });
    new CfnOutput(this, 'AccessRoleArn', { value: this.accessRole.roleArn });
  }
}
