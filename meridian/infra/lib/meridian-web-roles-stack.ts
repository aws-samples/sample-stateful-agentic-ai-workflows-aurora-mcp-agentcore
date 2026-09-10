import { CfnOutput, Stack, type StackProps, aws_iam as iam } from 'aws-cdk-lib';
import { Construct } from 'constructs';

export interface MeridianWebRolesStackProps extends StackProps {
  /** The App Runner environment from serviceEnvironment(); supplies the ARNs the role may touch. */
  environment: Record<string, string>;
}

/**
 * The App Runner roles, in their own stack.
 *
 * App Runner pulls the image with the access role and hands the container its
 * credentials and the api-token secret through the instance role while it
 * deploys the service. When either role is created in the same CloudFormation
 * deployment as the service, App Runner fails with "Failed to deploy your
 * application image" and no application log, because IAM has not propagated
 * the role yet; the same service definition succeeds against roles that have
 * existed for a minute. scripts/publish.py deploys this stack first and waits
 * whenever it created or changed it.
 */
export class MeridianWebRolesStack extends Stack {
  readonly instanceRole: iam.Role;
  readonly accessRole: iam.Role;

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

    this.accessRole = new iam.Role(this, 'BackendAccessRole', {
      assumedBy: new iam.ServicePrincipal('build.apprunner.amazonaws.com'),
      description: 'Meridian backend on App Runner: pull the image from the CDK assets repository',
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSAppRunnerServicePolicyForECRAccess')],
    });

    new CfnOutput(this, 'InstanceRoleArn', { value: this.instanceRole.roleArn });
    new CfnOutput(this, 'AccessRoleArn', { value: this.accessRole.roleArn });
  }
}
