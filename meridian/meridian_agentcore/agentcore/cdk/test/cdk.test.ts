import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { AgentCoreStack } from '../lib/cdk-stack';

test('AgentCoreStack synthesizes the checked-in Meridian specification', () => {
  const spec = JSON.parse(readFileSync(resolve(__dirname, '../../agentcore.json'), 'utf8'));
  const app = new cdk.App();
  const stack = new AgentCoreStack(app, 'TestStack', {
    spec: spec as never,
    mcpSpec: spec as never,
  });
  const template = Template.fromStack(stack);
  template.hasOutput('StackNameOutput', {
    Description: 'Name of the CloudFormation Stack',
  });
  expect(spec.runtimes).toHaveLength(1);
  expect(spec.runtimes[0].name).toBe('MeridianConcierge');
  expect(spec.memories).toHaveLength(1);
  expect(spec.agentCoreGateways).toHaveLength(1);
  expect(Object.keys(template.toJSON().Resources ?? {}).length).toBeGreaterThan(0);
});
