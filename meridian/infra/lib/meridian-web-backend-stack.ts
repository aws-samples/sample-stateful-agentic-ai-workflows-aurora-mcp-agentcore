import * as path from 'node:path';
import { CfnOutput, IgnoreMode, Stack, type StackProps, aws_ecr_assets as ecrAssets } from 'aws-cdk-lib';
import { Construct } from 'constructs';

// Compiled to infra/dist/lib, so three levels up is meridian/.
const meridianDir = path.resolve(__dirname, '..', '..', '..');

export interface MeridianWebBackendStackProps extends StackProps {
  /** From serviceEnvironment(); the non-secret App Runner environment. */
  environment: Record<string, string>;
}

/**
 * The backend image asset, built from meridian/Dockerfile for linux/amd64.
 *
 * Every hosting path builds it here, with these exact properties, so the asset hash (and so the
 * image tag in the CDK assets repository) is the same image whichever service runs it.
 */
export function backendImageAsset(scope: Construct): ecrAssets.DockerImageAsset {
  return new ecrAssets.DockerImageAsset(scope, 'BackendImage', {
    directory: meridianDir,
    file: 'Dockerfile',
    platform: ecrAssets.Platform.LINUX_AMD64,
    ignoreMode: IgnoreMode.DOCKER,
  });
}

/**
 * The backend image, built from meridian/Dockerfile and pushed to the CDK
 * assets repository.
 *
 * The established App Runner service was provisioned outside CloudFormation and is brought
 * under CloudFormation by MeridianWebService (an import, see docs/OPERATIONS.md).
 * scripts/publish.py remains the path that updates the service. This stack owns the image
 * and the non-secret service configuration it outputs.
 */
export class MeridianWebBackendStack extends Stack {
  constructor(scope: Construct, id: string, props: MeridianWebBackendStackProps) {
    super(scope, id, props);

    const image = backendImageAsset(this);

    new CfnOutput(this, 'ImageUri', { value: image.imageUri });
    new CfnOutput(this, 'ServiceEnvironment', { value: JSON.stringify(props.environment) });
  }
}
