import * as cdk from 'aws-cdk-lib';
import { type AgentCoreProjectSpec } from '@aws/agentcore-cdk';
import { Template } from 'aws-cdk-lib/assertions';
import { CfnFunction } from 'aws-cdk-lib/aws-lambda';
import { bindHoldBudget } from '../lib/hold-budget';

function specification(modes: string[]): AgentCoreProjectSpec {
  return {
    name: 'meridian',
    runtimes: modes.map(mode => ({
      envVars: [
        { name: 'MERIDIAN_AGENTCORE_AUTH', value: mode },
        { name: 'MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS', value: '400000' },
      ],
    })),
  } as AgentCoreProjectSpec;
}

test.each(['iam', 'jwt'])('the synthesized holds target enforces the %s release mode', mode => {
  const stack = new cdk.Stack(new cdk.App(), 'HoldMode');
  new CfnFunction(stack, 'Holds', {
    functionName: 'meridian-MeridianHolds',
    role: 'arn:aws:iam::123456789012:role/target',
    code: { zipFile: 'def handler(event, context): return event' },
    handler: 'index.handler',
    runtime: 'python3.13',
  });
  bindHoldBudget(stack, specification([mode, mode]));
  Template.fromStack(stack).hasResourceProperties('AWS::Lambda::Function', {
    Environment: {
      Variables: {
        MERIDIAN_AGENTCORE_AUTH: mode,
        MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS: '400000',
      },
    },
  });
});

test.each([
  ['jwt', 'iam'],
  ['unknown', 'unknown'],
])('inconsistent or unknown Runtime modes refuse synthesis: %s / %s', (first, second) => {
  const stack = new cdk.Stack(new cdk.App(), 'BadHoldMode');
  expect(() => bindHoldBudget(stack, specification([first, second]))).toThrow('same MERIDIAN_AGENTCORE_AUTH');
});
