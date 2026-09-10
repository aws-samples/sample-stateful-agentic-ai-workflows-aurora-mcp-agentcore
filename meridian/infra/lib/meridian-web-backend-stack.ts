import * as path from 'node:path';
import * as apprunner from '@aws-cdk/aws-apprunner-alpha';
import {
  CfnOutput,
  Duration,
  IgnoreMode,
  Stack,
  type StackProps,
  aws_ecr_assets as ecrAssets,
  aws_iam as iam,
  aws_secretsmanager as secretsmanager,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { API_TOKEN_SECRET_NAME } from './meridian-web-stack';

// Compiled to infra/dist/lib, so three levels up is meridian/.
const meridianDir = path.resolve(__dirname, '..', '..', '..');

export interface MeridianWebBackendStackProps extends StackProps {
  /** From loadServiceEnvironment(); the non-secret App Runner environment. */
  environment: Record<string, string>;
  /** The App Runner instance role from MeridianWebRolesStack, deployed and propagated first. */
  instanceRole: iam.IRole;
  /** The ECR access role from MeridianWebRolesStack, likewise deployed ahead. */
  accessRole: iam.IRole;
}

/**
 * The FastAPI backend on App Runner, in its own stack.
 *
 * App Runner service creation fails intermittently in this account ("Failed
 * to deploy your application image" with no application log, for images and
 * commands that deploy fine minutes later). Keeping the service apart from
 * the CloudFront distribution and the site bucket lets scripts/publish.py
 * retry just this stack, and lets a backend change redeploy without touching
 * the distribution.
 */
export class MeridianWebBackendStack extends Stack {
  readonly service: apprunner.Service;

  constructor(scope: Construct, id: string, props: MeridianWebBackendStackProps) {
    super(scope, id, props);
    const { environment, instanceRole, accessRole } = props;

    // App Runner needs the complete secret ARN (with its suffix) to read the token at
    // deployment; scripts/publish.py creates the secret and passes the ARN through.
    const apiTokenArn = process.env.MERIDIAN_API_TOKEN_SECRET_ARN;
    if (!apiTokenArn) {
      throw new Error(`MERIDIAN_API_TOKEN_SECRET_ARN is not set; run scripts/publish.py, which creates ${API_TOKEN_SECRET_NAME}`);
    }
    const apiToken = secretsmanager.Secret.fromSecretCompleteArn(this, 'ApiToken', apiTokenArn);

    const image = new ecrAssets.DockerImageAsset(this, 'BackendImage', {
      directory: meridianDir,
      file: 'Dockerfile',
      platform: ecrAssets.Platform.LINUX_AMD64,
      ignoreMode: IgnoreMode.DOCKER,
    });

    this.service = new apprunner.Service(this, 'Backend', {
      serviceName: 'meridian-web',
      source: apprunner.Source.fromAsset({
        asset: image,
        imageConfiguration: {
          port: 8000,
          environmentVariables: environment,
          environmentSecrets: {
            MERIDIAN_API_TOKEN: apprunner.Secret.fromSecretsManager(apiToken),
          },
        },
      }),
      instanceRole,
      accessRole,
      cpu: apprunner.Cpu.ONE_VCPU,
      memory: apprunner.Memory.TWO_GB,
      autoDeploymentsEnabled: false,
      // TCP on purpose. Every deployment configured with an HTTP health check on
      // /health at a 10 second interval failed in us-east-1 before App Runner
      // provisioned an instance, while the same image and command passed a TCP
      // check. Uvicorn binds the port only after the lifespan startup finishes,
      // and that startup initialises the Aurora checkpoint backend, so an open
      // port already means the backend is ready.
      healthCheck: apprunner.HealthCheck.tcp({
        interval: Duration.seconds(10),
        timeout: Duration.seconds(5),
        healthyThreshold: 1,
        unhealthyThreshold: 5,
      }),
      autoScalingConfiguration: new apprunner.AutoScalingConfiguration(this, 'BackendScaling', {
        autoScalingConfigurationName: 'meridian-web',
        minSize: 1,
        maxSize: 2,
        maxConcurrency: 25,
      }),
    });
    apiToken.grantRead(instanceRole);

    new CfnOutput(this, 'BackendUrl', { value: `https://${this.service.serviceUrl}` });
    new CfnOutput(this, 'BackendServiceArn', { value: this.service.serviceArn });
  }
}
