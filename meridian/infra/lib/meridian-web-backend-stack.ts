import * as path from 'node:path';
import { CfnOutput, IgnoreMode, Stack, type StackProps, aws_ecr_assets as ecrAssets } from 'aws-cdk-lib';
import { Construct } from 'constructs';

// Compiled to infra/dist/lib, so three levels up is meridian/.
const meridianDir = path.resolve(__dirname, '..', '..', '..');

export interface MeridianWebBackendStackProps extends StackProps {
  /** From loadServiceEnvironment(); the non-secret App Runner environment. */
  environment: Record<string, string>;
}

/**
 * The backend image, built from meridian/Dockerfile and pushed to the CDK
 * assets repository.
 *
 * The established App Runner service was provisioned outside CloudFormation.
 * scripts/publish.py updates that exact service without deleting/recreating it.
 * This stack owns only the image and non-secret service configuration.
 */
export class MeridianWebBackendStack extends Stack {
  constructor(scope: Construct, id: string, props: MeridianWebBackendStackProps) {
    super(scope, id, props);

    const image = new ecrAssets.DockerImageAsset(this, 'BackendImage', {
      directory: meridianDir,
      file: 'Dockerfile',
      platform: ecrAssets.Platform.LINUX_AMD64,
      ignoreMode: IgnoreMode.DOCKER,
    });

    new CfnOutput(this, 'ImageUri', { value: image.imageUri });
    new CfnOutput(this, 'ServiceEnvironment', { value: JSON.stringify(props.environment) });
  }
}
