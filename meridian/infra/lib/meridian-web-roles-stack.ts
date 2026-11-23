import { ArnFormat, CfnOutput, Stack, type StackProps, aws_iam as iam } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { grantBackendAccess } from './meridian-web-backend-grants';
import { API_TOKEN_SECRET_NAME } from './meridian-web-stack';

export interface MeridianWebRolesStackProps extends StackProps {
  /** The App Runner environment from serviceEnvironment(); supplies the ARNs the role may touch. */
  environment: Record<string, string>;
  /**
   * The master login's secret, in the jwt release only. The service does not use it; the role
   * keeps the grant (and the shared-token secret's) until the tighten release. The iam release
   * grants only the backend login secret (its `AURORA_SECRET_ARN`) and the shared token, never
   * the master.
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
    grantBackendAccess(this.instanceRole, this, environment, secretGrants);

    this.accessRole = new iam.Role(this, 'BackendAccessRole', {
      assumedBy: new iam.ServicePrincipal('build.apprunner.amazonaws.com'),
      description: 'Meridian backend on App Runner: pull the image from the CDK assets repository',
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSAppRunnerServicePolicyForECRAccess')],
    });

    new CfnOutput(this, 'InstanceRoleArn', { value: this.instanceRole.roleArn });
    new CfnOutput(this, 'AccessRoleArn', { value: this.accessRole.roleArn });
  }
}
