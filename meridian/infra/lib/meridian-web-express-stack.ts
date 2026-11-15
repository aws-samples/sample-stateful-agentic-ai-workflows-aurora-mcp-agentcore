import {
  CfnOutput,
  Fn,
  RemovalPolicy,
  Stack,
  type StackProps,
  aws_ecs as ecs,
  aws_iam as iam,
  aws_logs as logs,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { backendImageAsset } from './meridian-web-backend-stack';

/** The Express service's name. It differs from App Runner's `meridian-web`, so both can run. */
export const EXPRESS_SERVICE_NAME = 'meridian-web-express';
/** The container port the backend listens on (`backend/launch.py`, Dockerfile EXPOSE). */
export const BACKEND_PORT = 8000;
/** The backend's public liveness route: it answers without a token (`backend/main.py`). */
export const HEALTH_CHECK_PATH = '/health';
/** The size App Runner runs the backend at: 1 vCPU and 2 GB. */
export const TASK_CPU = '1024';
export const TASK_MEMORY = '2048';
/** Tasks the service may run, and the CPU load that triggers a scale out. */
export const MIN_TASKS = 1;
export const MAX_TASKS = 4;
export const SCALING_METRIC = 'AVERAGE_CPU';
export const SCALING_TARGET_PERCENT = 60;

export interface MeridianWebExpressStackProps extends StackProps {
  /** From serviceEnvironment(); the plain container environment. */
  environment: Record<string, string>;
  /** The roles from MeridianWebExpressRoles. */
  taskRole: iam.IRole;
  executionRole: iam.IRole;
  infrastructureRole: iam.IRole;
  /**
   * Secrets Manager ARN of the shared token, for the iam release only. Passed as a secret
   * reference for Amazon ECS to resolve; the value never enters the template.
   */
  apiTokenSecretArn?: string;
}

/** Environment as the sorted Name/Value list Amazon ECS stores, so drift checks compare cleanly. */
function sortedVariables(
  values: Record<string, string>,
): ecs.CfnExpressGatewayService.KeyValuePairProperty[] {
  return Object.keys(values).sort().map((name) => ({ name, value: values[name] }));
}

/**
 * The hosted backend on Amazon ECS Express Mode, for accounts that cannot use App Runner.
 *
 * It runs the same image (backendImageAsset), the same environment and, in the iam release, the
 * same secret reference as the App Runner service, at the same size. Express Mode creates the
 * Application Load Balancer, its certificate, the security groups, the ECS cluster `default` and
 * the scaling policy. The service uses the default VPC's public subnets; see
 * docs/OPERATIONS.md for the prerequisite and for what Express Mode cannot set.
 *
 * Unlike the App Runner service this one is not retained: deleting the stack deletes the service.
 */
export class MeridianWebExpressStack extends Stack {
  readonly service: ecs.CfnExpressGatewayService;
  /**
   * The service's host name, for the CloudFront origin. The resource's `Endpoint` attribute is a
   * URL (`https://<name>.ecs.<region>.on.aws`, seen on the first deploy); a CloudFront origin takes
   * the host alone.
   */
  readonly endpoint: string;

  constructor(scope: Construct, id: string, props: MeridianWebExpressStackProps) {
    super(scope, id, props);
    const { apiTokenSecretArn } = props;
    const image = backendImageAsset(this);

    const logGroup = new logs.LogGroup(this, 'BackendLogs', {
      logGroupName: `/aws/ecs/${EXPRESS_SERVICE_NAME}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    this.service = new ecs.CfnExpressGatewayService(this, 'BackendService', {
      serviceName: EXPRESS_SERVICE_NAME,
      infrastructureRoleArn: props.infrastructureRole.roleArn,
      executionRoleArn: props.executionRole.roleArn,
      taskRoleArn: props.taskRole.roleArn,
      cpu: TASK_CPU,
      memory: TASK_MEMORY,
      healthCheckPath: HEALTH_CHECK_PATH,
      scalingTarget: {
        minTaskCount: MIN_TASKS,
        maxTaskCount: MAX_TASKS,
        autoScalingMetric: SCALING_METRIC,
        autoScalingTargetValue: SCALING_TARGET_PERCENT,
      },
      primaryContainer: {
        image: image.imageUri,
        containerPort: BACKEND_PORT,
        environment: sortedVariables(props.environment),
        ...(apiTokenSecretArn
          ? { secrets: [{ name: 'MERIDIAN_API_TOKEN', valueFrom: apiTokenSecretArn }] }
          : {}),
        awsLogsConfiguration: { logGroup: logGroup.logGroupName, logStreamPrefix: 'ecs' },
      },
    });
    this.service.node.addDependency(logGroup);
    this.endpoint = Fn.select(2, Fn.split('/', this.service.attrEndpoint));

    new CfnOutput(this, 'ImageUri', { value: image.imageUri });
    new CfnOutput(this, 'ServiceEndpoint', { value: this.service.attrEndpoint });
    new CfnOutput(this, 'ServiceHost', { value: this.endpoint });
    new CfnOutput(this, 'ServiceArn', { value: this.service.attrServiceArn });
  }
}
