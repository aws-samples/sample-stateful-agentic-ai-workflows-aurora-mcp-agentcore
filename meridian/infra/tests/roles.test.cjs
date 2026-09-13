const assert = require('node:assert/strict');
const { test } = require('node:test');
const { App } = require('aws-cdk-lib');
const { Match, Template } = require('aws-cdk-lib/assertions');
const { MeridianWebRolesStack } = require('../dist/lib/meridian-web-roles-stack');
const { serviceEnvironment } = require('../dist/lib/meridian-web-stack');

const environment = {
  AURORA_CLUSTER_ARN: 'arn:aws:rds:us-east-1:123456789012:cluster:meridian',
  AURORA_SECRET_ARN: 'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian-abcdef',
  AURORA_DATABASE: 'meridian',
  AGENTCORE_RUNTIME_ARN: 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/meridian',
  AGENTCORE_GATEWAY_URL: 'https://meridian-test.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp',
};

function stack(endpoint = environment.AGENTCORE_GATEWAY_URL) {
  return new MeridianWebRolesStack(new App(), 'Roles', {
    env: { account: '123456789012', region: 'us-east-1' },
    environment: { ...environment, AGENTCORE_GATEWAY_URL: endpoint },
  });
}

test('the backend can invoke only the configured Gateway', () => {
  const template = Template.fromStack(stack());
  template.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([{
        Action: 'bedrock-agentcore:InvokeGateway',
        Effect: 'Allow',
        Resource: {
          'Fn::Join': ['', [
            'arn:', { Ref: 'AWS::Partition' },
            ':bedrock-agentcore:us-east-1:123456789012:gateway/meridian-test',
          ]],
        },
      }]),
    },
  });
});

for (const endpoint of [
  'http://meridian-test.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp',
  'https://*.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp',
  'https://meridian-test.example.com/mcp',
  'https://meridian-test.gateway.bedrock-agentcore.us-east-1.amazonaws.com/other',
]) {
  test(`rejects an invalid Gateway endpoint: ${endpoint}`, () => {
    assert.throws(() => stack(endpoint), /standard HTTPS AgentCore Gateway endpoint/);
  });
}

test('the hosted configuration requires a Gateway endpoint', () => {
  const { AGENTCORE_GATEWAY_URL, ...missingGateway } = environment;
  assert.throws(() => serviceEnvironment(missingGateway, 'us-east-1'), /AGENTCORE_GATEWAY_URL/);
});
