import { CfnOutput, Stack, type StackProps, aws_iam as iam } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { grantBackendAccess } from './meridian-web-backend-grants';

export interface MeridianWebExpressRolesStackProps extends StackProps {
  /** From serviceEnvironment(); supplies the ARNs the task role may touch. */
  environment: Record<string, string>;
  /**
   * Secrets Manager ARN of the shared token, for the iam release only. Amazon ECS reads it
   * through the execution role when it starts the task; the task role never holds it.
   */
  apiTokenSecretArn?: string;
}

/**
 * The three roles of the Amazon ECS Express Mode backend, in their own stack.
 *
 * Express Mode assumes the infrastructure role at once to build the load balancer, and the
 * execution role to pull the image. AWS documents that a role created a moment before the service
 * can fail with an assume-role error until IAM has propagated. Keeping the roles in a stack that
 * deploys, and settles, ahead of the service means a failed service create never recreates them.
 *
 * - Task role: the same grants as the App Runner instance role (see grantBackendAccess), minus
 *   the master login and shared-token secrets, which the Express path never needs. It is also
 *   what `scripts/bind_web_backend_role.py` binds in the iam release.
 * - Execution role: the AWS managed policy Express Mode requires (image pull, log writes), plus a
 *   read of the shared-token secret in the iam release.
 * - Infrastructure role: the AWS managed policy for Express services.
 */
export class MeridianWebExpressRolesStack extends Stack {
  readonly taskRole: iam.Role;
  readonly executionRole: iam.Role;
  readonly infrastructureRole: iam.Role;

  constructor(scope: Construct, id: string, props: MeridianWebExpressRolesStackProps) {
    super(scope, id, props);
    const { environment, apiTokenSecretArn } = props;
    const fromThisAccount = {
      conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
    };

    this.taskRole = new iam.Role(this, 'TaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com', fromThisAccount),
      description: 'Meridian backend on Amazon ECS Express Mode: Bedrock, Aurora Data API, the MeridianConcierge and MeridianWorkflow AgentCore Runtimes',
    });
    grantBackendAccess(this.taskRole, this, environment, [environment.AURORA_SECRET_ARN]);

    this.executionRole = new iam.Role(this, 'ExecutionRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com', fromThisAccount),
      description: 'Meridian backend on Amazon ECS Express Mode: pull the image, write logs, read the shared-token secret',
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });
    if (apiTokenSecretArn) {
      this.executionRole.addToPolicy(
        new iam.PolicyStatement({
          actions: ['secretsmanager:GetSecretValue'],
          resources: [apiTokenSecretArn],
        }),
      );
    }

    this.infrastructureRole = new iam.Role(this, 'InfrastructureRole', {
      assumedBy: new iam.ServicePrincipal('ecs.amazonaws.com'),
      description: 'Meridian backend on Amazon ECS Express Mode: lets Amazon ECS manage the load balancer, security groups and scaling',
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          'service-role/AmazonECSInfrastructureRoleforExpressGatewayServices',
        ),
      ],
    });

    new CfnOutput(this, 'InstanceRoleArn', { value: this.taskRole.roleArn });
    new CfnOutput(this, 'ExecutionRoleArn', { value: this.executionRole.roleArn });
    new CfnOutput(this, 'InfrastructureRoleArn', { value: this.infrastructureRole.roleArn });
  }
}
