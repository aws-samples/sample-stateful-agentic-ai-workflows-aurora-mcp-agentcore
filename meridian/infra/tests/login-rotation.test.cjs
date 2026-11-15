const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { App } = require('aws-cdk-lib');
const { Match, Template } = require('aws-cdk-lib/assertions');
const {
  ROTATABLE_LOGINS, parseRotateLogins, parseRotationDays, resolveLoginRotationConfig,
} = require('../dist/lib/login-rotation-config');
const { MeridianLoginRotationStack } = require('../dist/lib/meridian-login-rotation-stack');
const { MeridianAuditExportStack } = require('../dist/lib/meridian-audit-export-stack');
const { MeridianWebRolesStack } = require('../dist/lib/meridian-web-roles-stack');

const ACCOUNT = '123456789012';
const SECRETS = `arn:aws:secretsmanager:us-east-1:${ACCOUNT}:secret:meridian/aurora`;
const CLUSTER = `arn:aws:rds:us-east-1:${ACCOUNT}:cluster:meridian`;
const BACKEND_SECRET = `${SECRETS}/backend-login-AbC123`;
const ROTATOR_SECRET = `${SECRETS}/rotator-login-XyZ789`;
const INFRA = path.resolve(__dirname, '..');

const props = {
  env: { account: ACCOUNT, region: 'us-east-1' },
  clusterArn: CLUSTER,
  database: 'meridian',
  rotatorSecretArn: ROTATOR_SECRET,
  logins: [{ key: 'backend', role: 'meridian_backend', envKey: 'AURORA_BACKEND_SECRET_ARN', secretArn: BACKEND_SECRET }],
};

function synth(overrides = {}) {
  return Template.fromStack(new MeridianLoginRotationStack(new App(), 'Rotation', { ...props, ...overrides }));
}

const dotenv = {
  AURORA_CLUSTER_ARN: CLUSTER,
  AURORA_ROTATOR_SECRET_ARN: ROTATOR_SECRET,
  AURORA_BACKEND_SECRET_ARN: BACKEND_SECRET,
  AURORA_GATEWAY_SECRET_ARN: `${SECRETS}/gateway-login-AbC123`,
};

// The switch: nothing exists without it.

test('without the context switch no login is chosen', () => {
  assert.deepEqual(parseRotateLogins(undefined), []);
  assert.deepEqual(parseRotateLogins(''), []);
  assert.deepEqual(parseRotateLogins('  ,, '), []);
  assert.deepEqual(parseRotateLogins(null), []);
});

test('the app synthesizes no stack and no template when the switch is absent', () => {
  const outdir = fs.mkdtempSync(path.join(os.tmpdir(), 'rotation-off-'));
  try {
    const run = spawnSync(process.execPath, ['dist/bin/meridian-login-rotation.js'], {
      cwd: INFRA,
      env: { ...process.env, CDK_OUTDIR: outdir, CDK_DEFAULT_ACCOUNT: ACCOUNT },
      encoding: 'utf8',
    });
    assert.equal(run.status, 0, run.stderr);
    assert.match(run.stderr, /rotation is off/i);
    const templates = fs.readdirSync(outdir).filter((name) => name.endsWith('.template.json'));
    assert.deepEqual(templates, []);
  } finally {
    fs.rmSync(outdir, { recursive: true, force: true });
  }
});

test('no existing stack, and no existing app entry, refers to rotation', () => {
  for (const file of fs.readdirSync(path.join(INFRA, 'bin'))) {
    if (file === 'meridian-login-rotation.ts') continue;
    assert.doesNotMatch(fs.readFileSync(path.join(INFRA, 'bin', file), 'utf8'), /rotat/i, file);
  }
  const lib = fs.readdirSync(path.join(INFRA, 'lib')).filter((f) => !/login-rotation/.test(f));
  for (const file of lib) {
    assert.doesNotMatch(fs.readFileSync(path.join(INFRA, 'lib', file), 'utf8'), /RotationSchedule|addRotationSchedule/, file);
  }
});

test('the existing stacks synthesize no rotation resource and no rotation Lambda', () => {
  const stacks = [
    new MeridianAuditExportStack(new App(), 'AuditExport', {
      env: { account: ACCOUNT, region: 'us-east-1' },
      clusterArn: CLUSTER,
      auditExportSecretArn: `${SECRETS}/audit-export-login-AbC123`,
      database: 'meridian',
    }),
    new MeridianWebRolesStack(new App(), 'Roles', {
      env: { account: ACCOUNT, region: 'us-east-1' },
      environment: {
        AURORA_CLUSTER_ARN: CLUSTER,
        AURORA_SECRET_ARN: `${SECRETS}/backend-login-AbC123`,
        AURORA_DATABASE: 'meridian',
        AGENTCORE_RUNTIME_ARN: `arn:aws:bedrock-agentcore:us-east-1:${ACCOUNT}:runtime/meridian`,
        AGENTCORE_WORKFLOW_RUNTIME_ARN: `arn:aws:bedrock-agentcore:us-east-1:${ACCOUNT}:runtime/meridianv2_MeridianWorkflow-x`,
        AGENTCORE_GATEWAY_URL: 'https://meridian-test.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp',
      },
    }),
  ];
  for (const stack of stacks) {
    const json = JSON.stringify(Template.fromStack(stack).toJSON());
    assert.doesNotMatch(json, /RotationSchedule|login_rotation|rds-data:ExecuteStatement.*Rotat/);
    assert.doesNotMatch(json, /AWS::SecretsManager::RotationSchedule/);
  }
});

test('the switch names known logins once each, and says what is allowed', () => {
  assert.deepEqual(parseRotateLogins('backend').map((l) => l.role), ['meridian_backend']);
  assert.deepEqual(parseRotateLogins('backend, gateway').map((l) => l.key), ['backend', 'gateway']);
  assert.throws(() => parseRotateLogins('backend,backend'), /twice/);
  assert.throws(() => parseRotateLogins('master'), /choose from backend, gateway, identity, catalog, workflow, audit/);
  assert.throws(() => parseRotateLogins(true), /comma-separated list/);
  assert.equal(ROTATABLE_LOGINS.length, 6);
});

test('rotation days are a whole number from 1 to 365', () => {
  assert.equal(parseRotationDays(undefined), 30);
  assert.equal(parseRotationDays('7'), 7);
  for (const bad of ['0', '366', '1.5', 'x']) assert.throws(() => parseRotationDays(bad), /rotationDays/);
});

// The config.

test('config needs the cluster, the rotator secret, each chosen login secret and the same account', () => {
  const logins = parseRotateLogins('backend');
  const config = resolveLoginRotationConfig(dotenv, ACCOUNT, logins);
  assert.equal(config.region, 'us-east-1');
  assert.equal(config.logins[0].secretArn, BACKEND_SECRET);
  assert.throws(() => resolveLoginRotationConfig({ ...dotenv, AURORA_ROTATOR_SECRET_ARN: '' }, ACCOUNT, logins), /AURORA_ROTATOR_SECRET_ARN/);
  assert.throws(() => resolveLoginRotationConfig(dotenv, ACCOUNT, parseRotateLogins('catalog')), /AURORA_CATALOG_SECRET_ARN/);
  assert.throws(() => resolveLoginRotationConfig(dotenv, '111122223333', logins), /different account/);
  assert.throws(() => resolveLoginRotationConfig(dotenv, undefined, logins), /CDK_DEFAULT_ACCOUNT/);
  assert.throws(() => resolveLoginRotationConfig({ ...dotenv, AURORA_BACKEND_SECRET_ARN: BACKEND_SECRET.replace('us-east-1', 'us-west-2') }, ACCOUNT, logins), /different Region/);
  assert.throws(() => resolveLoginRotationConfig(dotenv, ACCOUNT, []), /names no login/);
});

// The stack.

test('one login gets one Lambda, one schedule, and no rotation on creation', () => {
  const template = synth();
  template.resourceCountIs('AWS::Lambda::Function', 1);
  template.resourceCountIs('AWS::SecretsManager::RotationSchedule', 1);
  template.hasResourceProperties('AWS::SecretsManager::RotationSchedule', {
    SecretId: BACKEND_SECRET,
    RotationLambdaARN: Match.anyValue(),
    RotateImmediatelyOnUpdate: false,
    RotationRules: { ScheduleExpression: 'rate(30 days)' },
  });
});

test('the schedule follows rotationDays', () => {
  synth({ rotationDays: 7 }).hasResourceProperties('AWS::SecretsManager::RotationSchedule', {
    RotationRules: { ScheduleExpression: 'rate(7 days)' },
  });
  assert.throws(() => synth({ rotationDays: 0 }), /rotationDays/);
  assert.throws(() => synth({ logins: [] }), /at least one login/);
});

test('each chosen login gets its own Lambda with its own role in the environment', () => {
  const gateway = { key: 'gateway', role: 'meridian_gateway', envKey: 'X', secretArn: `${SECRETS}/gateway-login-AbC123` };
  const template = synth({ logins: [...props.logins, gateway] });
  template.resourceCountIs('AWS::Lambda::Function', 2);
  template.resourceCountIs('AWS::SecretsManager::RotationSchedule', 2);
  for (const role of ['meridian_backend', 'meridian_gateway']) {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Environment: { Variables: Match.objectLike({ ROTATION_LOGIN_ROLE: role }) },
    });
  }
});

test('the Lambda is configured with the cluster, the database and the rotator secret', () => {
  synth().hasResourceProperties('AWS::Lambda::Function', {
    Runtime: 'python3.13',
    Handler: 'login_rotation.lambda_handler',
    Timeout: 60,
    Environment: {
      Variables: {
        ROTATION_CLUSTER_ARN: CLUSTER,
        ROTATION_DATABASE: 'meridian',
        ROTATION_ROTATOR_SECRET_ARN: ROTATOR_SECRET,
        ROTATION_LOGIN_ROLE: 'meridian_backend',
      },
    },
  });
});

test('the Lambda runs outside a VPC, since the Data API needs none', () => {
  const functions = Object.values(synth().findResources('AWS::Lambda::Function'));
  for (const fn of functions) assert.equal(fn.Properties.VpcConfig, undefined);
});

test('the role can rotate its own secret, read the rotator secret, and run statements, nothing more', () => {
  const template = synth();
  const statements = Object.values(template.findResources('AWS::IAM::Policy'))
    .flatMap((resource) => [].concat(resource.Properties.PolicyDocument.Statement));
  const byAction = (action) => statements.filter((s) => [].concat(s.Action).includes(action));
  const own = byAction('secretsmanager:PutSecretValue');
  assert.equal(own.length, 1);
  assert.deepEqual([].concat(own[0].Resource), [BACKEND_SECRET]);
  assert.deepEqual([].concat(own[0].Action).sort(), [
    'secretsmanager:DescribeSecret', 'secretsmanager:GetSecretValue',
    'secretsmanager:PutSecretValue', 'secretsmanager:UpdateSecretVersionStage',
  ]);
  const rotatorReads = statements.filter((s) => [].concat(s.Resource).includes(ROTATOR_SECRET));
  assert.ok(rotatorReads.length > 0);
  for (const s of rotatorReads) {
    for (const action of [].concat(s.Action)) assert.match(action, /^secretsmanager:(GetSecretValue|DescribeSecret)$/);
  }
  const data = byAction('rds-data:ExecuteStatement');
  assert.equal(data.length, 1);
  assert.deepEqual([].concat(data[0].Resource), [CLUSTER]);
  for (const s of statements) {
    for (const action of [].concat(s.Action)) {
      assert.doesNotMatch(action, /secretsmanager:(CreateSecret|DeleteSecret|PutResourcePolicy|RotateSecret)|iam:|rds:/);
    }
    if (s.Resource === '*') assert.equal(s.Action, 'secretsmanager:GetRandomPassword');
  }
});

test('Secrets Manager, and only Secrets Manager, may invoke each Lambda', () => {
  const permissions = Object.values(synth().findResources('AWS::Lambda::Permission'));
  assert.ok(permissions.length >= 1);
  for (const permission of permissions) {
    assert.equal(permission.Properties.Principal, 'secretsmanager.amazonaws.com');
    assert.equal(permission.Properties.Action, 'lambda:InvokeFunction');
  }
});

test('the stack is protected from deletion and logs expire', () => {
  const stack = new MeridianLoginRotationStack(new App(), 'Rotation', props);
  assert.equal(stack.terminationProtection, true);
  Template.fromStack(stack).hasResourceProperties('AWS::Logs::LogGroup', { RetentionInDays: 90 });
});

test('the template holds no account id outside the fixed test account and no secret value', () => {
  const json = JSON.stringify(synth().toJSON());
  assert.doesNotMatch(json, /"password"|SecretString/);
  for (const id of json.match(/\d{12}/g) ?? []) assert.equal(id, ACCOUNT);
});

test('the Lambda source directory holds only the rotation module', () => {
  const files = fs.readdirSync(path.join(INFRA, 'functions', 'login_rotation')).filter((f) => !f.startsWith('__pycache__'));
  assert.deepEqual(files, ['login_rotation.py']);
});
