const assert = require('node:assert/strict');
const { test } = require('node:test');
const { App } = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const {
  LEGACY_LIVE_ENVIRONMENT,
  MeridianWebServiceStack,
} = require('../dist/lib/meridian-web-service-stack');
const { serviceEnvironment } = require('../dist/lib/meridian-web-stack');

const IMAGE = '123456789012.dkr.ecr.us-east-1.amazonaws.com/cdk-assets:abc123';
const INSTANCE_ROLE = 'arn:aws:iam::123456789012:role/Roles-BackendRole-AAA';
const ACCESS_ROLE = 'arn:aws:iam::123456789012:role/Roles-BackendAccessRole-BBB';
const TOKEN_SECRET =
  'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian/web/api-token-AbC123';
const LOGIN_SECRET =
  'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian/aurora/backend-login-AbC123';
const environment = {
  ZED: 'last',
  AURORA_CLUSTER_ARN: 'arn:aws:rds:us-east-1:123456789012:cluster:meridian',
  AURORA_SECRET_ARN: LOGIN_SECRET,
  ENVIRONMENT: 'production',
};

function template(extra = {}) {
  return Template.fromStack(new MeridianWebServiceStack(new App(), 'Service', {
    env: { account: '123456789012', region: 'us-east-1' },
    environment,
    imageUri: IMAGE,
    instanceRoleArn: INSTANCE_ROLE,
    accessRoleArn: ACCESS_ROLE,
    ...extra,
  }));
}

function service(extra) {
  const [resource] = Object.values(template(extra).findResources('AWS::AppRunner::Service'));
  return resource;
}

function variables(extra) {
  return service(extra).Properties.SourceConfiguration.ImageRepository.ImageConfiguration
    .RuntimeEnvironmentVariables;
}

test('the service is retained on delete and on replacement', () => {
  const resource = service();
  assert.equal(resource.DeletionPolicy, 'Retain');
  assert.equal(resource.UpdateReplacePolicy, 'Retain');
});

test('the template holds exactly one service and nothing else that deletes', () => {
  const resources = template().toJSON().Resources;
  assert.deepEqual(Object.values(resources).map((r) => r.Type), ['AWS::AppRunner::Service']);
});

test('the service keeps the live name, size, health check and network settings', () => {
  const { Properties: p } = service();
  assert.equal(p.ServiceName, 'meridian-web');
  assert.deepEqual(
    p.InstanceConfiguration,
    { Cpu: '1024', Memory: '2048', InstanceRoleArn: INSTANCE_ROLE },
  );
  assert.deepEqual(p.HealthCheckConfiguration, {
    Protocol: 'TCP', Interval: 5, Timeout: 2, HealthyThreshold: 1, UnhealthyThreshold: 5,
  });
  assert.deepEqual(p.NetworkConfiguration, {
    EgressConfiguration: { EgressType: 'DEFAULT' },
    IngressConfiguration: { IsPubliclyAccessible: true },
    IpAddressType: 'IPV4',
  });
  assert.equal(p.AutoScalingConfigurationArn, undefined);
  assert.equal(p.ObservabilityConfiguration, undefined);
  assert.equal(p.EncryptionConfiguration, undefined);
  assert.equal(p.Tags, undefined);
});

test('the image, access role and port come from props and auto deploy is off', () => {
  const { SourceConfiguration: source } = service().Properties;
  assert.equal(source.AutoDeploymentsEnabled, false);
  assert.deepEqual(source.AuthenticationConfiguration, { AccessRoleArn: ACCESS_ROLE });
  assert.equal(source.ImageRepository.ImageIdentifier, IMAGE);
  assert.equal(source.ImageRepository.ImageRepositoryType, 'ECR');
  assert.equal(source.ImageRepository.ImageConfiguration.Port, '8000');
});

test('the environment is the props plus the legacy live keys, sorted by name', () => {
  assert.deepEqual(variables().map((v) => v.Name), [
    'AGENTCORE_GATEWAY_SEARCH_TOOL',
    'AURORA_CLUSTER_ARN',
    'AURORA_SECRET_ARN',
    'ENVIRONMENT',
    'LANGGRAPH_CHECKPOINT_DATA_API',
    'LANGGRAPH_CHECKPOINT_REQUIRED',
    'ZED',
  ]);
  const values = Object.fromEntries(variables().map((v) => [v.Name, v.Value]));
  assert.deepEqual(values, { ...LEGACY_LIVE_ENVIRONMENT, ...environment });
});

test('the legacy keys are exactly the three the live service carries that no code reads', () => {
  assert.deepEqual(Object.keys(LEGACY_LIVE_ENVIRONMENT).sort(), [
    'AGENTCORE_GATEWAY_SEARCH_TOOL',
    'LANGGRAPH_CHECKPOINT_DATA_API',
    'LANGGRAPH_CHECKPOINT_REQUIRED',
  ]);
});

test('the jwt release environment is exactly the live key set, less the legacy keys', () => {
  const dotenv = {
    AURORA_CLUSTER_ARN: 'arn:aws:rds:us-east-1:123456789012:cluster:meridian',
    AURORA_SECRET_ARN: 'arn:aws:secretsmanager:us-east-1:123456789012:secret:master-AbC123',
    AURORA_BACKEND_SECRET_ARN: LOGIN_SECRET,
    AURORA_DATABASE: 'meridian',
    AURORA_CLUSTER_IDENTIFIER: 'meridian',
    AGENTCORE_RUNTIME_ARN: 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/a',
    AGENTCORE_WORKFLOW_RUNTIME_ARN: 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/b',
    AGENTCORE_GATEWAY_URL: 'https://g.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp',
    AGENTCORE_MEMORY_ID: 'memory',
    AGENTCORE_REGION: 'us-east-1',
    BEDROCK_MODEL_ID: 'global.anthropic.claude-sonnet-5-5',
    BEDROCK_REGION: 'us-east-1',
    EMBEDDING_MODEL: 'cohere.embed-v4:0',
    EMBEDDING_DIMENSION: '1024',
    RERANK_MODEL: 'cohere.rerank-v3-5:0',
    MERIDIAN_COGNITO_REGION: 'us-east-1',
    MERIDIAN_COGNITO_USER_POOL_ID: 'us-east-1_AbCdEfGhI',
    MERIDIAN_COGNITO_APP_CLIENT_ID: 'abcdefghij0123456789klmn',
  };
  assert.deepEqual(Object.keys(serviceEnvironment(dotenv, 'us-east-1', 'jwt')).sort(), [
    'AGENTCORE_GATEWAY_URL', 'AGENTCORE_MEMORY_ID', 'AGENTCORE_REGION', 'AGENTCORE_RUNTIME_ARN',
    'AGENTCORE_SKIP_CLI_SYNC', 'AGENTCORE_WORKFLOW_RUNTIME_ARN', 'AURORA_BACKEND_SECRET_ARN',
    'AURORA_CLUSTER_ARN', 'AURORA_CLUSTER_IDENTIFIER', 'AURORA_DATABASE', 'AURORA_SECRET_ARN',
    'AWS_DEFAULT_REGION', 'AWS_REGION', 'BEDROCK_MODEL_ID', 'BEDROCK_REGION',
    'EMBEDDING_DIMENSION', 'EMBEDDING_MODEL', 'ENVIRONMENT', 'LOG_AGENT_VERBOSE', 'LOG_LEVEL',
    'MCP_CONNECTION_METHOD', 'MCP_DATABASE_TYPE', 'MERIDIAN_AGENTCORE_AUTH',
    'MERIDIAN_COGNITO_APP_CLIENT_ID', 'MERIDIAN_COGNITO_REGION',
    'MERIDIAN_COGNITO_USER_POOL_ID', 'RERANK_MODEL',
  ]);
});

test('without a token secret the service has no secret references', () => {
  const config = service().Properties.SourceConfiguration.ImageRepository.ImageConfiguration;
  assert.equal(config.RuntimeEnvironmentSecrets, undefined);
});

test('the iam release references the token secret by ARN and never holds a value', () => {
  const config = service({ apiTokenSecretArn: TOKEN_SECRET }).Properties.SourceConfiguration
    .ImageRepository.ImageConfiguration;
  assert.deepEqual(
    config.RuntimeEnvironmentSecrets,
    [{ Name: 'MERIDIAN_API_TOKEN', Value: TOKEN_SECRET }],
  );
  assert.ok(!config.RuntimeEnvironmentVariables.some((v) => /TOKEN|PASSWORD/.test(v.Name)));
});

test('the image must be a literal ECR image, so a token or an empty value cannot deploy', () => {
  for (const imageUri of ['', 'latest', { Ref: 'x' }, 'docker.io/library/nginx:1']) {
    assert.throws(() => template({ imageUri }), /imageUri must be a literal ECR image/);
  }
});

test('the template carries no 12-digit account id except the one passed in', () => {
  const text = JSON.stringify(template().toJSON());
  const ids = new Set(text.match(/\b\d{12}\b/g));
  assert.deepEqual([...ids], ['123456789012']);
});
