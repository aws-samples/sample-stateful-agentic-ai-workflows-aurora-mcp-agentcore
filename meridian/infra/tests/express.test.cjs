const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { App, aws_s3_deployment: s3deploy } = require('aws-cdk-lib');
const { defineWebApp } = require('../dist/lib/meridian-web-app');
const { MeridianWebBackendStack } = require('../dist/lib/meridian-web-backend-stack');
const { MeridianWebRolesStack } = require('../dist/lib/meridian-web-roles-stack');
const { MeridianWebServiceStack, LEGACY_LIVE_ENVIRONMENT } = require('../dist/lib/meridian-web-service-stack');
const { MeridianWebStack, serviceEnvironment } = require('../dist/lib/meridian-web-stack');
const { rolesStackWiring } = require('../dist/lib/meridian-web-wiring');

const ACCOUNT = '123456789012';
const ENV = { account: ACCOUNT, region: 'us-east-1' };
const TOKEN_SECRET = `arn:aws:secretsmanager:us-east-1:${ACCOUNT}:secret:meridian/web/api-token-AbC123`;
const LOGIN_SECRET = `arn:aws:secretsmanager:us-east-1:${ACCOUNT}:secret:meridian/aurora/backend-login-AbC123`;
const dotenv = {
  AURORA_CLUSTER_ARN: `arn:aws:rds:us-east-1:${ACCOUNT}:cluster:meridian`,
  AURORA_SECRET_ARN: `arn:aws:secretsmanager:us-east-1:${ACCOUNT}:secret:master-AbC123`,
  AURORA_BACKEND_SECRET_ARN: LOGIN_SECRET,
  AURORA_DATABASE: 'meridian',
  AGENTCORE_RUNTIME_ARN: `arn:aws:bedrock-agentcore:us-east-1:${ACCOUNT}:runtime/a`,
  AGENTCORE_WORKFLOW_RUNTIME_ARN: `arn:aws:bedrock-agentcore:us-east-1:${ACCOUNT}:runtime/b`,
  AGENTCORE_GATEWAY_URL: 'https://g.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp',
  MERIDIAN_COGNITO_REGION: 'us-east-1',
  MERIDIAN_COGNITO_USER_POOL_ID: 'us-east-1_AbCdEfGhI',
  MERIDIAN_COGNITO_APP_CLIENT_ID: 'abcdefghij0123456789klmn',
};
const APP_RUNNER_HOST = 'x.us-east-1.awsapprunner.com';
const IMPORT_CONTEXT = {
  instanceRoleArn: `arn:aws:iam::${ACCOUNT}:role/instance`,
  accessRoleArn: `arn:aws:iam::${ACCOUNT}:role/access`,
  serviceImageUri: `${ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/assets:abc123`,
  apiTokenSecretArn: TOKEN_SECRET,
};

/** The CloudFront stack synthesizes the frontend build; stand in a one-file site. */
function stubSite(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'meridian-site-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  fs.writeFileSync(path.join(directory, 'index.html'), '<!doctype html><title>Meridian</title>');
  const sourceAsset = s3deploy.Source.asset;
  t.mock.method(s3deploy.Source, 'asset', () => sourceAsset(directory));
}

/** The templates of every stack `defineWebApp` defines, by stack name. */
function synthesize(context, { mode = 'jwt', processEnv = {} } = {}) {
  const app = new App({ context });
  defineWebApp(app, {
    dotenv: { ...dotenv, MERIDIAN_AGENTCORE_AUTH: mode },
    processEnv: { CDK_DEFAULT_ACCOUNT: ACCOUNT, ...processEnv },
  });
  return Object.fromEntries(app.synth().stacks.map((s) => [s.stackName, s.template]));
}

/**
 * The stacks as bin/meridian-web.ts defined them before the Express path existed, built directly
 * from the stack classes. `defineWebApp` without the switch must match this exactly.
 */
function legacyTemplates(context, mode, tighten) {
  const app = new App({ context });
  const environment = serviceEnvironment(dotenv, 'us-east-1', mode);
  const wiring = rolesStackWiring(
    dotenv, mode, tighten ? { MERIDIAN_TIGHTEN_ROLE: '1' } : {},
  );
  new MeridianWebRolesStack(app, 'MeridianWebRoles', {
    env: ENV, environment, ...wiring,
    description: 'Meridian travel concierge: the App Runner roles, deployed ahead of the service',
  });
  new MeridianWebBackendStack(app, 'MeridianWebBackend', {
    env: ENV, environment,
    description: 'Meridian travel concierge: the FastAPI backend image for App Runner',
  });
  if (context.instanceRoleArn) {
    new MeridianWebServiceStack(app, 'MeridianWebService', {
      env: ENV, environment,
      imageUri: context.serviceImageUri,
      instanceRoleArn: context.instanceRoleArn,
      accessRoleArn: context.accessRoleArn,
      apiTokenSecretArn: mode === 'iam' ? context.apiTokenSecretArn : undefined,
      description: 'Meridian travel concierge: the App Runner service, imported and retained',
    });
  }
  new MeridianWebStack(app, 'MeridianWeb', {
    env: ENV, backendHost: APP_RUNNER_HOST, identityMode: mode,
    description:
      'Meridian travel concierge: Vite build on S3 behind CloudFront, ' +
      'routing the API to App Runner',
  });
  return Object.fromEntries(app.synth().stacks.map((s) => [s.stackName, s.template]));
}

const absent = { MERIDIAN_BACKEND_HOST: APP_RUNNER_HOST };
const express = { backendHost: 'express' };

for (const mode of ['iam', 'jwt']) {
  for (const tighten of mode === 'jwt' ? [false, true] : [false]) {
    test(`without the switch every existing stack is unchanged (${mode}${tighten ? ', tightened' : ''})`, (t) => {
      stubSite(t);
      const processEnv = { ...absent, ...(tighten ? { MERIDIAN_TIGHTEN_ROLE: '1' } : {}) };
      const actual = synthesize(IMPORT_CONTEXT, { mode, processEnv });
      const expected = legacyTemplates(IMPORT_CONTEXT, mode, tighten);
      assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort());
      for (const name of Object.keys(expected)) {
        assert.equal(JSON.stringify(actual[name]), JSON.stringify(expected[name]), name);
      }
    });
  }
}

const GOLDEN = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'golden', 'web-stacks.json'), 'utf8'),
);
const normalize = (template) => JSON.stringify(template).replace(/[0-9a-f]{64}/g, '<sha256>');
const sha256 = (text) => crypto.createHash('sha256').update(text).digest('hex');

for (const [scenario, mode, tighten] of [
  ['iam', 'iam', false], ['jwt', 'jwt', false], ['jwt-tightened', 'jwt', true],
]) {
  test(`the existing stacks match the templates recorded at ${GOLDEN.generatedFrom} (${scenario})`, (t) => {
    stubSite(t);
    const processEnv = { ...absent, ...(tighten ? { MERIDIAN_TIGHTEN_ROLE: '1' } : {}) };
    const actual = synthesize(IMPORT_CONTEXT, { mode, processEnv });
    const recorded = GOLDEN.scenarios[scenario];
    assert.deepEqual(Object.keys(recorded).sort(), Object.keys(actual).sort());
    for (const name of Object.keys(recorded)) {
      assert.equal(sha256(normalize(actual[name])), recorded[name], `${scenario} ${name}`);
    }
  });
}

test('an empty switch value is the same as no switch', (t) => {
  stubSite(t);
  const actual = synthesize({ backendHost: '' }, { processEnv: absent });
  assert.deepEqual(Object.keys(actual).sort(), ['MeridianWeb', 'MeridianWebBackend', 'MeridianWebRoles']);
});

test('without the switch no Express stack or resource exists', (t) => {
  stubSite(t);
  const templates = synthesize(IMPORT_CONTEXT, { processEnv: absent });
  assert.ok(!Object.keys(templates).some((name) => name.includes('Express')));
  assert.ok(!JSON.stringify(templates).includes('ExpressGatewayService'));
});

test('with the switch the existing App Runner stacks are still byte-identical', (t) => {
  stubSite(t);
  const without = synthesize(IMPORT_CONTEXT, { processEnv: absent });
  const withSwitch = synthesize({ ...IMPORT_CONTEXT, ...express });
  for (const name of ['MeridianWebRoles', 'MeridianWebBackend', 'MeridianWebService']) {
    assert.equal(JSON.stringify(withSwitch[name]), JSON.stringify(without[name]), name);
  }
});

test('with the switch the site differs from the App Runner site only in the API origin', (t) => {
  stubSite(t);
  const without = synthesize({}, { processEnv: absent }).MeridianWeb;
  const withSwitch = synthesize(express).MeridianWeb;
  const strip = (template) => {
    const copy = JSON.parse(JSON.stringify(template));
    delete copy.Description;
    for (const resource of Object.values(copy.Resources)) {
      if (resource.Type !== 'AWS::CloudFront::Distribution') continue;
      for (const origin of resource.Properties.DistributionConfig.Origins) {
        if (origin.CustomOriginConfig) origin.DomainName = '<api origin>';
      }
    }
    return copy;
  };
  assert.deepEqual(strip(withSwitch), strip(without));
  const distribution = Object.values(withSwitch.Resources)
    .find((resource) => resource.Type === 'AWS::CloudFront::Distribution');
  const apiOrigins = distribution.Properties.DistributionConfig.Origins
    .filter((origin) => origin.CustomOriginConfig);
  assert.equal(apiOrigins.length, 1);
  const domain = apiOrigins[0].DomainName;
  assert.equal(domain['Fn::Select'][0], 2, 'the origin is the host part of the Endpoint URL');
  const [separator, imported] = domain['Fn::Select'][1]['Fn::Split'];
  assert.equal(separator, '/');
  assert.match(imported['Fn::ImportValue'], /^MeridianWebExpress:.*BackendServiceEndpoint/);
  assert.ok(!JSON.stringify(withSwitch).includes('awsapprunner'));
});

test('the switch accepts only express, and not alongside an App Runner host', (t) => {
  stubSite(t);
  assert.throws(() => synthesize({ backendHost: 'apprunner' }), /backendHost must be 'express'/);
  assert.throws(
    () => synthesize(express, { processEnv: absent }),
    /unset MERIDIAN_BACKEND_HOST/,
  );
});

test('the iam release needs the token secret ARN for the Express path, and jwt does not', (t) => {
  stubSite(t);
  assert.throws(() => synthesize(express, { mode: 'iam' }), /Required context: -c apiTokenSecretArn/);
  assert.ok(synthesize({ ...express, apiTokenSecretArn: TOKEN_SECRET }, { mode: 'iam' }));
  assert.ok(synthesize(express, { mode: 'jwt' }));
});

function service(templates) {
  const [resource] = Object.values(templates.MeridianWebExpress.Resources)
    .filter((r) => r.Type === 'AWS::ECS::ExpressGatewayService');
  return resource;
}

test('the Express stack holds the service and its log group, and nothing that is retained', (t) => {
  stubSite(t);
  const { Resources: resources } = synthesize(express).MeridianWebExpress;
  const types = Object.values(resources).map((r) => r.Type).sort();
  assert.deepEqual(types, ['AWS::ECS::ExpressGatewayService', 'AWS::Logs::LogGroup']);
  assert.ok(Object.values(resources).every((r) => r.DeletionPolicy !== 'Retain'));
});

test('the service pins its name, size, port, health check path and scaling', (t) => {
  stubSite(t);
  const { Properties: p } = service(synthesize(express));
  assert.equal(p.ServiceName, 'meridian-web-express');
  assert.equal(p.Cpu, '1024');
  assert.equal(p.Memory, '2048');
  assert.equal(p.HealthCheckPath, '/health');
  assert.equal(p.PrimaryContainer.ContainerPort, 8000);
  assert.deepEqual(p.ScalingTarget, {
    MinTaskCount: 1, MaxTaskCount: 4, AutoScalingMetric: 'AVERAGE_CPU', AutoScalingTargetValue: 60,
  });
});

test('the size matches the App Runner service', (t) => {
  stubSite(t);
  const express_ = service(synthesize(express)).Properties;
  const appRunner = Object.values(synthesize(IMPORT_CONTEXT, { processEnv: absent })
    .MeridianWebService.Resources)[0].Properties;
  assert.equal(express_.Cpu, appRunner.InstanceConfiguration.Cpu);
  assert.equal(express_.Memory, appRunner.InstanceConfiguration.Memory);
  assert.equal(
    String(express_.PrimaryContainer.ContainerPort),
    appRunner.SourceConfiguration.ImageRepository.ImageConfiguration.Port,
  );
});

test('the service runs the same image asset as the App Runner backend stack', (t) => {
  stubSite(t);
  const templates = synthesize(express);
  const backendImage = templates.MeridianWebBackend.Outputs.ImageUri.Value;
  assert.deepEqual(service(templates).Properties.PrimaryContainer.Image, backendImage);
  assert.deepEqual(templates.MeridianWebExpress.Outputs.ImageUri.Value, backendImage);
});

test('the environment is the App Runner environment without the legacy keys', (t) => {
  stubSite(t);
  const { Properties: p } = service(synthesize(express));
  const names = p.PrimaryContainer.Environment.map((v) => v.Name);
  assert.deepEqual(names, [...names].sort());
  const expected = serviceEnvironment(dotenv, 'us-east-1', 'jwt');
  assert.deepEqual(
    Object.fromEntries(p.PrimaryContainer.Environment.map((v) => [v.Name, v.Value])),
    expected,
  );
  for (const legacy of Object.keys(LEGACY_LIVE_ENVIRONMENT)) {
    assert.ok(!names.includes(legacy), legacy);
  }
  assert.equal(expected.MERIDIAN_AGENTCORE_AUTH, 'jwt');
});

test('the jwt release references no secret, and the iam release references the token by ARN', (t) => {
  stubSite(t);
  assert.equal(service(synthesize(express)).Properties.PrimaryContainer.Secrets, undefined);
  const iam = service(synthesize({ ...express, apiTokenSecretArn: TOKEN_SECRET }, { mode: 'iam' }));
  assert.deepEqual(
    iam.Properties.PrimaryContainer.Secrets,
    [{ Name: 'MERIDIAN_API_TOKEN', ValueFrom: TOKEN_SECRET }],
  );
  const names = iam.Properties.PrimaryContainer.Environment.map((v) => v.Name);
  assert.ok(!names.some((name) => /TOKEN|PASSWORD/.test(name)));
});

test('logs go to a log group the stack creates, kept 30 days', (t) => {
  stubSite(t);
  const templates = synthesize(express);
  const [[logicalId, group]] = Object.entries(templates.MeridianWebExpress.Resources)
    .filter(([, r]) => r.Type === 'AWS::Logs::LogGroup');
  assert.equal(group.Properties.RetentionInDays, 30);
  assert.equal(group.Properties.LogGroupName, '/aws/ecs/meridian-web-express');
  assert.deepEqual(service(templates).Properties.PrimaryContainer.AwsLogsConfiguration, {
    LogGroup: { Ref: logicalId }, LogStreamPrefix: 'ecs',
  });
  assert.ok(service(templates).DependsOn.includes(logicalId));
});

test('the service uses the three roles from the roles stack and no network settings', (t) => {
  stubSite(t);
  const templates = synthesize(express);
  const { Properties: p } = service(templates);
  for (const key of ['InfrastructureRoleArn', 'ExecutionRoleArn', 'TaskRoleArn']) {
    assert.ok(p[key]['Fn::ImportValue'], key);
    assert.match(p[key]['Fn::ImportValue'], /^MeridianWebExpressRoles:/);
  }
  assert.equal(p.NetworkConfiguration, undefined);
  assert.equal(p.Cluster, undefined);
});

function roleOf(template, id) {
  return Object.entries(template.Resources)
    .find(([key, r]) => r.Type === 'AWS::IAM::Role' && key.startsWith(id))[1];
}

test('each role trusts only its Amazon ECS principal, from this account for the task roles', (t) => {
  stubSite(t);
  const roles = synthesize(express).MeridianWebExpressRoles;
  const trust = (id) => roleOf(roles, id).Properties.AssumeRolePolicyDocument.Statement;
  for (const id of ['TaskRole', 'ExecutionRole']) {
    assert.deepEqual(trust(id), [{
      Action: 'sts:AssumeRole',
      Condition: { StringEquals: { 'aws:SourceAccount': ACCOUNT } },
      Effect: 'Allow',
      Principal: { Service: 'ecs-tasks.amazonaws.com' },
    }]);
  }
  assert.deepEqual(trust('InfrastructureRole'), [{
    Action: 'sts:AssumeRole', Effect: 'Allow', Principal: { Service: 'ecs.amazonaws.com' },
  }]);
});

function policyArns(role) {
  return (role.Properties.ManagedPolicyArns ?? []).map((arn) => JSON.stringify(arn));
}

test('the execution and infrastructure roles carry the AWS managed policies Express Mode needs', (t) => {
  stubSite(t);
  const roles = synthesize(express).MeridianWebExpressRoles;
  assert.match(policyArns(roleOf(roles, 'ExecutionRole')).join(), /service-role\/AmazonECSTaskExecutionRolePolicy/);
  assert.match(
    policyArns(roleOf(roles, 'InfrastructureRole')).join(),
    /service-role\/AmazonECSInfrastructureRoleforExpressGatewayServices/,
  );
});

function statementsFor(template, roleId) {
  const [role] = Object.entries(template.Resources)
    .find(([key, r]) => r.Type === 'AWS::IAM::Role' && key.startsWith(roleId));
  return Object.values(template.Resources)
    .filter((r) => r.Type === 'AWS::IAM::Policy'
      && r.Properties.Roles.some((ref) => ref.Ref === role))
    .flatMap((r) => r.Properties.PolicyDocument.Statement);
}

test('the task role holds the same statements as the tightened App Runner instance role', (t) => {
  stubSite(t);
  const roles = synthesize(express).MeridianWebExpressRoles;
  const appRunner = synthesize(
    {}, { processEnv: { ...absent, MERIDIAN_TIGHTEN_ROLE: '1' } },
  ).MeridianWebRoles;
  const task = statementsFor(roles, 'TaskRole');
  assert.ok(task.length > 5);
  assert.deepEqual(task, statementsFor(appRunner, 'BackendRole'));
  assert.deepEqual(
    roleOf(roles, 'TaskRole').Properties.ManagedPolicyArns,
    roleOf(appRunner, 'BackendRole').Properties.ManagedPolicyArns,
  );
});

test('iam mode: the task role reads only the master secret, never the backend login or the shared token', (t) => {
  stubSite(t);
  for (const mode of ['iam', 'jwt']) {
    const roles = synthesize({ ...express, apiTokenSecretArn: TOKEN_SECRET }, { mode })
      .MeridianWebExpressRoles;
    const reads = statementsFor(roles, 'TaskRole')
      .filter((s) => s.Action === 'secretsmanager:GetSecretValue');
    assert.equal(reads.length, 1, mode);
    assert.deepEqual(reads[0].Resource, LOGIN_SECRET, mode);
    assert.ok(!JSON.stringify(roles).includes(dotenv.AURORA_SECRET_ARN), `${mode}: master secret`);
  }
});

test('the iam release runs every hosted service as the backend login and grants no master secret', (t) => {
  stubSite(t);
  const iam = synthesize({ ...IMPORT_CONTEXT, ...express }, { mode: 'iam' });
  const text = JSON.stringify(iam);
  assert.ok(!text.includes(dotenv.AURORA_SECRET_ARN), 'the master secret appears in an iam template');
  const express_ = Object.fromEntries(
    service(iam).Properties.PrimaryContainer.Environment.map((v) => [v.Name, v.Value]),
  );
  assert.equal(express_.AURORA_SECRET_ARN, LOGIN_SECRET);
  const appRunner = Object.values(iam.MeridianWebService.Resources)[0].Properties
    .SourceConfiguration.ImageRepository.ImageConfiguration.RuntimeEnvironmentVariables;
  assert.equal(appRunner.find((v) => v.Name === 'AURORA_SECRET_ARN').Value, LOGIN_SECRET);
});

test('the iam release refuses to synthesize without the backend login secret', (t) => {
  stubSite(t);
  const { AURORA_BACKEND_SECRET_ARN: _login, ...withoutLogin } = dotenv;
  for (const context of [IMPORT_CONTEXT, { ...IMPORT_CONTEXT, ...express }]) {
    const app = new App({ context });
    assert.throws(
      () => defineWebApp(app, {
        dotenv: { ...withoutLogin, MERIDIAN_AGENTCORE_AUTH: 'iam' },
        processEnv: { CDK_DEFAULT_ACCOUNT: ACCOUNT, ...absent },
      }),
      /provision_service_logins\.py --login backend/,
    );
  }
});

test('jwt mode: the task role reads only the backend login secret, never the master or the shared token', (t) => {
  stubSite(t);
  const roles = synthesize(express, { mode: 'jwt' }).MeridianWebExpressRoles;
  const reads = statementsFor(roles, 'TaskRole')
    .filter((s) => s.Action === 'secretsmanager:GetSecretValue');
  assert.equal(reads.length, 1);
  assert.deepEqual(reads[0].Resource, LOGIN_SECRET);
  assert.ok(!JSON.stringify(roles).includes(dotenv.AURORA_SECRET_ARN));
  assert.ok(!JSON.stringify(roles).includes('api-token'));
});

test('only the execution role may read the shared token, and only in the iam release', (t) => {
  stubSite(t);
  const jwt = synthesize(express).MeridianWebExpressRoles;
  assert.deepEqual(statementsFor(jwt, 'ExecutionRole'), []);
  assert.ok(!JSON.stringify(jwt).includes('api-token'));
  const iam = synthesize({ ...express, apiTokenSecretArn: TOKEN_SECRET }, { mode: 'iam' })
    .MeridianWebExpressRoles;
  assert.deepEqual(statementsFor(iam, 'ExecutionRole'), [{
    Action: 'secretsmanager:GetSecretValue', Effect: 'Allow', Resource: TOKEN_SECRET,
  }]);
});

test('no inline statement in the Express roles uses a wildcard action', (t) => {
  stubSite(t);
  for (const [mode, context] of [['jwt', express], ['iam', { ...express, apiTokenSecretArn: TOKEN_SECRET }]]) {
    const roles = synthesize(context, { mode }).MeridianWebExpressRoles;
    const actions = Object.values(roles.Resources)
      .filter((r) => r.Type === 'AWS::IAM::Policy')
      .flatMap((r) => r.Properties.PolicyDocument.Statement)
      .flatMap((s) => [].concat(s.Action));
    assert.ok(actions.length > 0);
    for (const action of actions) assert.ok(!action.includes('*'), `${mode}: ${action}`);
  }
});

test('the Express roles stack outputs the task role as InstanceRoleArn for the binding script', (t) => {
  stubSite(t);
  const { Outputs: outputs } = synthesize(express).MeridianWebExpressRoles;
  for (const key of ['ExecutionRoleArn', 'InfrastructureRoleArn', 'InstanceRoleArn']) {
    assert.ok(outputs[key], key);
  }
  assert.match(JSON.stringify(outputs.InstanceRoleArn), /TaskRole/);
});

test('the Express templates carry no 12-digit account id except the one passed in', (t) => {
  stubSite(t);
  const templates = synthesize(express);
  const text = JSON.stringify([templates.MeridianWebExpress, templates.MeridianWebExpressRoles]);
  assert.deepEqual([...new Set(text.match(/\b\d{12}\b/g))], [ACCOUNT]);
});
