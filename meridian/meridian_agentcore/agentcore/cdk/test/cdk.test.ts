import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { AgentCoreStack } from '../lib/cdk-stack';

// Stand-in values for the placeholders scripts/render_agentcore_config.py fills.
const TEST_VALUES: Record<string, string> = {
  AWS_ACCOUNT_ID: '123456789012',
  AWS_REGION: 'us-east-1',
  AURORA_CLUSTER_ARN: 'arn:aws:rds:us-east-1:123456789012:cluster:meridian',
  AURORA_SECRET_ARN: 'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian-AbC123',
  AURORA_GATEWAY_SECRET_ARN:
    'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian/aurora/gateway-login-GwY456',
  AURORA_WORKFLOW_SECRET_ARN:
    'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian/aurora/workflow-login-XyZ789',
  GATEWAY_ID: 'meridianv2-meridian-aurora-abcde12345',
  POLICY_ENGINE_ID: 'meridianv2_MeridianGovernance-abcde12345',
  MERIDIAN_AGENTCORE_AUTH: 'iam',
};
const PROJECT_ROOT = resolve(__dirname, '../../..');
const originalInitCwd = process.env.INIT_CWD;
let testProjectRoot: string;

function renderTemplate(): Record<string, any> {
  const template = readFileSync(join(PROJECT_ROOT, 'agentcore', 'agentcore.template.json'), 'utf8');
  const rendered = template.replace(/\{\{([A-Z_]+)\}\}/g, (placeholder, name: string) => {
    const value = TEST_VALUES[name];
    if (value === undefined) throw new Error(`No test value for ${placeholder}`);
    return value;
  });
  const spec = JSON.parse(rendered);
  // The default (iam) render leaves out the Cedar rule that only applies to Cognito callers.
  for (const engine of spec.policyEngines) {
    engine.policies = engine.policies.filter((p: { name: string }) => p.name !== 'meridian_traveler_binding');
  }
  return spec;
}

// The constructs locate the project through agentcore/agentcore.json, which is rendered
// per account and gitignored. Synthesize from a temporary project that links to the real
// application and gateway code, so the test never writes into the source tree.
beforeAll(() => {
  testProjectRoot = mkdtempSync(join(tmpdir(), 'meridian-agentcore-'));
  mkdirSync(join(testProjectRoot, 'agentcore'));
  writeFileSync(join(testProjectRoot, 'agentcore', 'agentcore.json'), JSON.stringify(renderTemplate()));
  symlinkSync(join(PROJECT_ROOT, 'app'), join(testProjectRoot, 'app'));
  symlinkSync(
    join(PROJECT_ROOT, 'agentcore', 'gateway_targets'),
    join(testProjectRoot, 'agentcore', 'gateway_targets')
  );
  process.env.INIT_CWD = testProjectRoot;
});

afterAll(() => {
  if (originalInitCwd === undefined) delete process.env.INIT_CWD;
  else process.env.INIT_CWD = originalInitCwd;
  rmSync(testProjectRoot, { recursive: true, force: true });
});

test('AgentCoreStack synthesizes the Meridian specification template', () => {
  const spec = renderTemplate();
  const app = new cdk.App();
  const stack = new AgentCoreStack(app, 'TestStack', {
    spec: spec as never,
    mcpSpec: spec as never,
  });
  const template = Template.fromStack(stack);
  template.hasOutput('StackNameOutput', {
    Description: 'Name of the CloudFormation Stack',
  });
  expect(spec.runtimes.map((r: { name: string }) => r.name)).toEqual(['MeridianConcierge', 'MeridianWorkflow']);
  const workflow = spec.runtimes[1];
  expect(workflow.lifecycleConfiguration).toEqual({ idleRuntimeSessionTimeout: 900, maxLifetime: 3600 });
  expect(workflow.additionalPolicies).toHaveLength(1);
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
  const rendered = JSON.stringify(resources);
  expect(rendered).not.toContain('bedrock-agentcore:CheckAuthorizePermissions');
  for (const action of ['AuthorizeAction', 'PartiallyAuthorizeActions', 'GetPolicyEngine']) {
    expect(rendered).toContain(`bedrock-agentcore:${action}`);
  }
  const holds = spec.agentCoreGateways[0].targets.find((t: { name: string }) => t.name === 'MeridianHolds');
  const secretStatement = holds.compute.iamPolicy.Statement.find((s: { Action: string[] }) =>
    s.Action.includes('secretsmanager:GetSecretValue')
  );
  expect(secretStatement.Resource).toEqual([TEST_VALUES.AURORA_SECRET_ARN, TEST_VALUES.AURORA_GATEWAY_SECRET_ARN]);
  expect(Object.keys(resources).length).toBeGreaterThan(0);
});

type Resource = { Type: string; Properties: Record<string, any> };

test('AgentCoreStack synthesizes the Cognito JWT specification', () => {
  const spec = JSON.parse(readFileSync(join(__dirname, 'fixtures', 'jwt-spec.json'), 'utf8'));
  writeFileSync(join(testProjectRoot, 'agentcore', 'agentcore.json'), JSON.stringify(spec));
  const stack = new AgentCoreStack(new cdk.App(), 'JwtStack', { spec: spec as never, mcpSpec: spec as never });
  const resources = Object.values(Template.fromStack(stack).toJSON().Resources ?? {}) as Resource[];
  const ofType = (type: string) => resources.filter(r => r.Type === type);
  const discoveryUrl =
    'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AbCdEfGhI/.well-known/openid-configuration';

  const runtimes = ofType('AWS::BedrockAgentCore::Runtime');
  expect(runtimes).toHaveLength(2);
  for (const runtime of runtimes) {
    const authorizer = runtime.Properties.AuthorizerConfiguration.CustomJWTAuthorizer;
    expect(authorizer.DiscoveryUrl).toBe(discoveryUrl);
    expect(authorizer.AllowedClients).toEqual(['exampleclientid123']);
    expect(authorizer.AllowedAudience).toBeUndefined();
    expect(runtime.Properties.RequestHeaderConfiguration.RequestHeaderAllowlist).toEqual(['Authorization']);
    const environment = runtime.Properties.EnvironmentVariables;
    expect(environment.MERIDIAN_AGENTCORE_AUTH).toBe('jwt');
  }

  const [gateway] = ofType('AWS::BedrockAgentCore::Gateway');
  expect(gateway.Properties.AuthorizerType).toBe('CUSTOM_JWT');
  expect(gateway.Properties.AuthorizerConfiguration.CustomJWTAuthorizer.DiscoveryUrl).toBe(discoveryUrl);
  expect(gateway.Properties.AuthorizerConfiguration.CustomJWTAuthorizer.AllowedClients).toEqual(['exampleclientid123']);

  const policyNames = ofType('AWS::BedrockAgentCore::Policy').map(p => p.Properties.Name);
  expect(policyNames).toContain('meridian_traveler_binding');
  expect(policyNames).toHaveLength(4);
});
