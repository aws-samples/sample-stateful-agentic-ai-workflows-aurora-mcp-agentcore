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
  expect(spec.memories[0].name).toBe('meridian_session');
  expect(spec.agentCoreGateways).toHaveLength(1);
  expect(spec.agentCoreGateways[0].targets.map((t: { name: string }) => t.name)).toEqual([
    'SemanticTripSearchLambda',
    'MeridianHolds',
  ]);
  expect(spec.agentCoreGateways[0].policyEngineConfiguration).toEqual({
    policyEngineName: 'MeridianGovernance',
    mode: 'ENFORCE',
  });
  expect(spec.policyEngines).toHaveLength(1);
  expect(spec.policyEngines[0].policies.map((p: { name: string }) => p.name)).toEqual([
    'meridian_read_tools',
    'meridian_hold_governance',
    'meridian_booking_governance',
  ]);
  const resources = template.toJSON().Resources ?? {};
  const types = Object.values(resources).map(r => (r as { Type: string }).Type);
  expect(types).toContain('AWS::BedrockAgentCore::PolicyEngine');
  expect(types.filter(t => t === 'AWS::BedrockAgentCore::GatewayTarget')).toHaveLength(2);
  expect(types).toContain('AWS::Lambda::Function');
  expect(Object.keys(resources).length).toBeGreaterThan(0);
});
