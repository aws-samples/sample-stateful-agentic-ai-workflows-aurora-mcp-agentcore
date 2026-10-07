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
  AGENTCORE_WORKFLOW_RUNTIME_ARN: 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/meridianv2_MeridianWorkflow-x',
  AGENTCORE_GATEWAY_URL: 'https://meridian-test.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp',
};

function stack(extra = {}) {
  return new MeridianWebRolesStack(new App(), 'Roles', {
    env: { account: '123456789012', region: 'us-east-1' },
    environment: { ...environment, ...extra },
  });
}

function statements() {
  const template = Template.fromStack(stack());
  return Object.values(template.findResources('AWS::IAM::Policy'))
    .flatMap((policy) => policy.Properties.PolicyDocument.Statement);
}

test('the backend can invoke and stop only the two Runtimes it uses', () => {
  const template = Template.fromStack(stack());
  template.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([{
        Action: ['bedrock-agentcore:InvokeAgentRuntime', 'bedrock-agentcore:StopRuntimeSession'],
        Effect: 'Allow',
        Resource: [
          environment.AGENTCORE_WORKFLOW_RUNTIME_ARN,
          `${environment.AGENTCORE_WORKFLOW_RUNTIME_ARN}/runtime-endpoint/*`,
        ],
      }]),
    },
  });
  template.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([{
        Action: 'bedrock-agentcore:InvokeAgentRuntime',
        Effect: 'Allow',
        Resource: [environment.AGENTCORE_RUNTIME_ARN, `${environment.AGENTCORE_RUNTIME_ARN}/runtime-endpoint/*`],
      }]),
    },
  });
});

test('the concierge Runtime is never granted a stop', () => {
  const stops = statements().filter((s) => JSON.stringify(s.Action).includes('StopRuntimeSession'));
  assert.equal(stops.length, 1);
  assert.ok(!JSON.stringify(stops[0].Resource).includes('runtime/meridian"'));
});

test('the backend no longer holds an InvokeGateway grant', () => {
  assert.ok(!JSON.stringify(statements()).includes('InvokeGateway'));
});

test('the backend role gains the backend login policy only once the login exists', () => {
  const without = Template.fromStack(stack());
  assert.ok(!JSON.stringify(without.toJSON()).includes('MeridianBackendAuroraAccess'));

  const loginSecret = 'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian/aurora/backend-login-AbC123';
  const withLogin = Template.fromStack(stack({ AURORA_BACKEND_SECRET_ARN: loginSecret }));
  withLogin.hasResourceProperties('AWS::IAM::Role', {
    ManagedPolicyArns: [
      'arn:aws:iam::123456789012:policy/MeridianBackendAuroraAccess',
    ],
  });
});

test('the master secret grant is untouched until the cutover release', () => {
  const loginSecret = 'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian/aurora/backend-login-AbC123';
  const secrets = Object.values(Template.fromStack(stack({ AURORA_BACKEND_SECRET_ARN: loginSecret }))
    .findResources('AWS::IAM::Policy'))
    .flatMap((policy) => policy.Properties.PolicyDocument.Statement)
    .filter((s) => JSON.stringify(s.Action) === '"secretsmanager:GetSecretValue"');
  assert.equal(secrets.length, 1);
  assert.ok(JSON.stringify(secrets[0].Resource).includes(environment.AURORA_SECRET_ARN));
  assert.ok(!JSON.stringify(secrets[0].Resource).includes('backend-login'));
});

test('the backend login secret is passed to the stacks but never required', () => {
  const dotenv = { ...environment, AURORA_BACKEND_SECRET_ARN: 'arn:aws:secretsmanager:us-east-1:123456789012:secret:b-AbC123' };
  assert.equal(serviceEnvironment(dotenv, 'us-east-1').AURORA_BACKEND_SECRET_ARN, dotenv.AURORA_BACKEND_SECRET_ARN);
  assert.equal(serviceEnvironment(environment, 'us-east-1').AURORA_BACKEND_SECRET_ARN, undefined);
});

test('the hosted configuration requires the workflow Runtime ARN', () => {
  const { AGENTCORE_WORKFLOW_RUNTIME_ARN, ...missing } = environment;
  assert.throws(() => serviceEnvironment(missing, 'us-east-1'), /AGENTCORE_WORKFLOW_RUNTIME_ARN/);
});

test('the hosted configuration requires a Gateway endpoint', () => {
  const { AGENTCORE_GATEWAY_URL, ...missingGateway } = environment;
  assert.throws(() => serviceEnvironment(missingGateway, 'us-east-1'), /AGENTCORE_GATEWAY_URL/);
});
