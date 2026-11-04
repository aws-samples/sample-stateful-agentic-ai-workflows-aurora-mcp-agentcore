import { Aspects, aws_bedrockagentcore as bedrockagentcore } from 'aws-cdk-lib';
import { IConstruct } from 'constructs';

/**
 * The AgentCore Runtime platform every Runtime runs on. From 2026-11-12 a Runtime created without
 * one starts on V2, which restores each session from a snapshot taken at startup. Meridian stays on
 * V1 until V2 passes a test event. CloudFormation applies a PlatformVersion change in place.
 */
export const RUNTIME_PLATFORM_VERSION = 'V1';

export function pinRuntimePlatformVersion(scope: IConstruct): void {
  Aspects.of(scope).add({
    visit(node: IConstruct): void {
      if (node instanceof bedrockagentcore.CfnRuntime) {
        node.platformVersion = RUNTIME_PLATFORM_VERSION;
      }
    },
  });
}
