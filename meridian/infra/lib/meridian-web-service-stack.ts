import { RemovalPolicy, Stack, type StackProps, aws_apprunner as apprunner } from 'aws-cdk-lib';
import { Construct } from 'constructs';

/** The established service's name; changing it replaces the service. */
export const SERVICE_NAME = 'meridian-web';

/**
 * Variables the live service carries that nothing in this repository reads: no file under
 * backend/, scripts/ or infra/ names them, and LangGraph lives only in examples/langgraph/.
 * They came from older releases through publish.py, which keeps the live variables under the new
 * ones. They stay here so the import matches the live service exactly. Removing them is a
 * separate, reviewed change: empty this object and deploy the stack after the import.
 */
export const LEGACY_LIVE_ENVIRONMENT: Record<string, string> = {
  AGENTCORE_GATEWAY_SEARCH_TOOL: 'SemanticTripSearchLambda___semantic_trip_search',
  LANGGRAPH_CHECKPOINT_DATA_API: 'true',
  LANGGRAPH_CHECKPOINT_REQUIRED: 'true',
};

const ECR_IMAGE = /^\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com\/[a-z0-9._/-]+:[A-Za-z0-9._-]+$/;

export interface MeridianWebServiceStackProps extends StackProps {
  /** From serviceEnvironment(); the plain App Runner environment. */
  environment: Record<string, string>;
  /**
   * The ECR image the service runs, always passed explicitly (`-c serviceImageUri`). For the
   * import it is the image the live service runs now, so the template matches it. A deploy with
   * another image is an UpdateService; there is no default, so no deploy can pick one by accident.
   */
  imageUri: string;
  /** From the MeridianWebRoles stack output InstanceRoleArn. */
  instanceRoleArn: string;
  /** From the MeridianWebRoles stack output AccessRoleArn. */
  accessRoleArn: string;
  /**
   * Secrets Manager ARN of the shared token, for the iam release only. Passed as a secret
   * reference for App Runner to resolve; the value never enters the template.
   */
  apiTokenSecretArn?: string;
}

/** Environment as the sorted Name/Value list App Runner stores, so drift checks compare cleanly. */
function keyValuePairs(
  values: Record<string, string>,
): apprunner.CfnService.KeyValuePairProperty[] {
  return Object.keys(values).sort().map((name) => ({ name, value: values[name] }));
}

/**
 * The hosted backend's App Runner service, imported from the one provisioned outside
 * CloudFormation (`cdk import`, see docs/OPERATIONS.md) so it can be rebuilt from code.
 *
 * Every setting mirrors the live service: 1 vCPU and 2 GB, a TCP health check, the account's
 * default auto scaling configuration, public ingress, default egress and no observability
 * configuration. Those last three are left to the App Runner defaults so the template does not
 * pin an account-specific ARN. The service is retained on delete and on replacement.
 */
export class MeridianWebServiceStack extends Stack {
  readonly service: apprunner.CfnService;

  constructor(scope: Construct, id: string, props: MeridianWebServiceStackProps) {
    super(scope, id, props);
    const { apiTokenSecretArn } = props;
    if (!ECR_IMAGE.test(props.imageUri)) {
      throw new Error(
        'imageUri must be a literal ECR image like <acct>.dkr.ecr.<region>.amazonaws.com/repo:tag',
      );
    }
    const environment = { ...LEGACY_LIVE_ENVIRONMENT, ...props.environment };

    this.service = new apprunner.CfnService(this, 'BackendService', {
      serviceName: SERVICE_NAME,
      sourceConfiguration: {
        autoDeploymentsEnabled: false,
        authenticationConfiguration: { accessRoleArn: props.accessRoleArn },
        imageRepository: {
          imageIdentifier: props.imageUri,
          imageRepositoryType: 'ECR',
          imageConfiguration: {
            port: '8000',
            runtimeEnvironmentVariables: keyValuePairs(environment),
            ...(apiTokenSecretArn
              ? {
                runtimeEnvironmentSecrets: keyValuePairs({ MERIDIAN_API_TOKEN: apiTokenSecretArn }),
              }
              : {}),
          },
        },
      },
      instanceConfiguration: {
        cpu: '1024', memory: '2048', instanceRoleArn: props.instanceRoleArn,
      },
      healthCheckConfiguration: {
        protocol: 'TCP', interval: 5, timeout: 2, healthyThreshold: 1, unhealthyThreshold: 5,
      },
      networkConfiguration: {
        egressConfiguration: { egressType: 'DEFAULT' },
        ingressConfiguration: { isPubliclyAccessible: true },
        ipAddressType: 'IPV4',
      },
    });
    this.service.applyRemovalPolicy(RemovalPolicy.RETAIN);
  }
}
