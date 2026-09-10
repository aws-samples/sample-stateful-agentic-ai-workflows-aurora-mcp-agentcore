import { CfnOutput, Stack, type StackProps, aws_iam as iam } from 'aws-cdk-lib';
import { Construct } from 'constructs';

export interface MeridianWebRolesStackProps extends StackProps {
  /** The App Runner environment from serviceEnvironment(); supplies the ARNs the role may touch. */
  environment: Record<string, string>;
}

/**
 * The App Runner instance role, in its own stack.
 *
 * App Runner reads the api-token secret and hands the container its
 * credentials through this role while it deploys the service. When the role is
 * created in the same CloudFormation deployment as the service, App Runner
 * fails every time with "Failed to deploy your application image" and no
 * application log, because IAM has not propagated the role yet; the same
 * service definition succeeds against a role that has existed for a minute.
 * scripts/publish.py deploys this stack first and waits after creating it.
 */
export class MeridianWebRolesStack extends Stack {
  readonly instanceRole: iam.Role;

  constructor(scope: Construct, id: string, props: MeridianWebRolesStackProps) {
    super(scope, id, props);
    const { environment } = props;

    this.instanceRole = new iam.Role(this, 'BackendRole', {
      assumedBy: new iam.ServicePrincipal('tasks.apprunner.amazonaws.com'),
      description: 'Meridian backend on App Runner: Bedrock, Aurora Data API, AgentCore Runtime',
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
        resources: [environment.AURORA_SECRET_ARN],
      }),
    );
    this.instanceRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [environment.AGENTCORE_RUNTIME_ARN, `${environment.AGENTCORE_RUNTIME_ARN}/runtime-endpoint/*`],
      }),
    );

    new CfnOutput(this, 'InstanceRoleArn', { value: this.instanceRole.roleArn });
  }
}
