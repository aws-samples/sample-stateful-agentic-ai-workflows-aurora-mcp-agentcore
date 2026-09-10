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
 * The App Runner service itself is created by scripts/publish.py with the
 * SDK, not by CloudFormation. In us-east-1 App Runner refused to deploy any
 * service whose CreateService call carried an optional setting, and the
 * CloudFormation resource handler always sends a tag list, so every stack
 * created service failed while the same definition sent by the SDK ran. This
 * stack publishes the image and the environment the script needs.
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
