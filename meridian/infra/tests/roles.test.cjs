const assert = require('node:assert/strict');
const { test } = require('node:test');
const { App } = require('aws-cdk-lib');
const { Match, Template } = require('aws-cdk-lib/assertions');
const { MeridianWebRolesStack } = require('../dist/lib/meridian-web-roles-stack');
const { identityMode, serviceEnvironment } = require('../dist/lib/meridian-web-stack');
const { rolesStackWiring } = require('../dist/lib/meridian-web-wiring');

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

const BACKEND_LOGIN =
  'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian/aurora/backend-login-AbC123';
const COGNITO = {
  MERIDIAN_COGNITO_REGION: 'us-east-1',
  MERIDIAN_COGNITO_USER_POOL_ID: 'us-east-1_AbCdEfGhI',
  MERIDIAN_COGNITO_APP_CLIENT_ID: 'exampleclientid1234567',
};
const jwtDotenv = { ...environment, AURORA_BACKEND_SECRET_ARN: BACKEND_LOGIN, ...COGNITO };

const BASE_ENV = {
  AWS_DEFAULT_REGION: 'us-east-1',
  AWS_REGION: 'us-east-1',
  ENVIRONMENT: 'production',
  LOG_LEVEL: 'INFO',
  LOG_AGENT_VERBOSE: 'false',
  AGENTCORE_SKIP_CLI_SYNC: '1',
  MCP_CONNECTION_METHOD: 'rdsapi',
  MCP_DATABASE_TYPE: 'APG',
  AURORA_CLUSTER_ARN: environment.AURORA_CLUSTER_ARN,
  AURORA_DATABASE: environment.AURORA_DATABASE,
  AGENTCORE_RUNTIME_ARN: environment.AGENTCORE_RUNTIME_ARN,
  AGENTCORE_WORKFLOW_RUNTIME_ARN: environment.AGENTCORE_WORKFLOW_RUNTIME_ARN,
  AGENTCORE_GATEWAY_URL: environment.AGENTCORE_GATEWAY_URL,
  AURORA_BACKEND_SECRET_ARN: BACKEND_LOGIN,
};

test('the mode comes from the process first, then meridian/.env, and defaults to iam', () => {
  const dotenvJwt = { MERIDIAN_AGENTCORE_AUTH: 'jwt' };
  assert.equal(identityMode({}, {}), 'iam');
  assert.equal(identityMode(dotenvJwt, {}), 'jwt');
  assert.equal(identityMode(dotenvJwt, { MERIDIAN_AGENTCORE_AUTH: 'iam' }), 'iam');
  const dotenvIam = { MERIDIAN_AGENTCORE_AUTH: 'iam' };
  assert.equal(identityMode(dotenvIam, { MERIDIAN_AGENTCORE_AUTH: ' JWT ' }), 'jwt');
  assert.equal(identityMode(dotenvJwt, { MERIDIAN_AGENTCORE_AUTH: '' }), 'iam');
});

test('any other mode is refused by name', () => {
  assert.throws(
    () => identityMode({ MERIDIAN_AGENTCORE_AUTH: 'true' }, {}),
    /MERIDIAN_AGENTCORE_AUTH.*iam.*jwt/,
  );
});

test('the iam service environment is exactly the master login and no sign-in setting', () => {
  const expected = { ...BASE_ENV, AURORA_SECRET_ARN: environment.AURORA_SECRET_ARN };
  assert.deepEqual(serviceEnvironment(jwtDotenv, 'us-east-1', 'iam'), expected);
  assert.deepEqual(serviceEnvironment(jwtDotenv, 'us-east-1'), expected);
});

test('the jwt service environment is exactly the pool, the mode and the backend login', () => {
  assert.deepEqual(serviceEnvironment(jwtDotenv, 'us-east-1', 'jwt'), {
    ...BASE_ENV,
    ...COGNITO,
    MERIDIAN_AGENTCORE_AUTH: 'jwt',
    AURORA_SECRET_ARN: BACKEND_LOGIN,
  });
});

test('jwt mode refuses an environment missing its sign-in or login', () => {
  for (const key of [...Object.keys(COGNITO), 'AURORA_BACKEND_SECRET_ARN']) {
    const rest = { ...jwtDotenv };
    delete rest[key];
    assert.throws(() => serviceEnvironment(rest, 'us-east-1', 'jwt'), new RegExp(key));
  }
  assert.throws(
    () => serviceEnvironment(
      { ...jwtDotenv, AURORA_BACKEND_SECRET_ARN: environment.AURORA_SECRET_ARN },
      'us-east-1',
      'jwt',
    ),
    /must differ from AURORA_SECRET_ARN/,
  );
});

test('jwt mode refuses a pool, client or secret of the wrong shape', () => {
  const wrong = {
    MERIDIAN_COGNITO_USER_POOL_ID: ['us-east-1', 'us-east-1_', 'US-EAST-1_AbC', 'us-east-1_a b'],
    MERIDIAN_COGNITO_APP_CLIENT_ID: ['short', 'UPPERCASEUPPERCASEUPPER', 'has-dash-has-dash-has'],
    AURORA_BACKEND_SECRET_ARN: [
      'not-an-arn',
      'arn:aws:secretsmanager:eu-west-1:123456789012:secret:b-AbC123',
    ],
  };
  for (const [key, values] of Object.entries(wrong)) {
    for (const value of values) {
      assert.throws(
        () => serviceEnvironment({ ...jwtDotenv, [key]: value }, 'us-east-1', 'jwt'),
        new RegExp(key),
        `${key}=${value}`,
      );
    }
  }
});

test('jwt mode refuses a pool id from another region than MERIDIAN_COGNITO_REGION', () => {
  assert.throws(
    () => serviceEnvironment(
      { ...jwtDotenv, MERIDIAN_COGNITO_REGION: 'eu-west-1' }, 'us-east-1', 'jwt',
    ),
    /MERIDIAN_COGNITO_USER_POOL_ID.*MERIDIAN_COGNITO_REGION/,
  );
});

function rolesTemplate(extra) {
  return Template.fromStack(new MeridianWebRolesStack(new App(), 'RolesJwt', {
    env: { account: '123456789012', region: 'us-east-1' },
    ...extra,
  }));
}

function secretStatements(extra) {
  return Object.values(rolesTemplate(extra).findResources('AWS::IAM::Policy'))
    .flatMap((policy) => policy.Properties.PolicyDocument.Statement)
    .filter((s) => JSON.stringify(s.Action).includes('secretsmanager:'));
}

const API_TOKEN_ARN = {
  'Fn::Join': ['', [
    'arn:',
    { Ref: 'AWS::Partition' },
    ':secretsmanager:us-east-1:123456789012:secret:meridian/web/api-token-??????',
  ]],
};
const jwtEnvironment = serviceEnvironment(jwtDotenv, 'us-east-1', 'jwt');

test('the first jwt release grants exactly the backend login, master and shared token', () => {
  assert.deepEqual(
    secretStatements({
      environment: jwtEnvironment, masterSecretArn: environment.AURORA_SECRET_ARN,
    }),
    [{
      Action: 'secretsmanager:GetSecretValue',
      Effect: 'Allow',
      Resource: [BACKEND_LOGIN, environment.AURORA_SECRET_ARN, API_TOKEN_ARN],
    }],
  );
});

test('the tighten release grants exactly the backend login secret', () => {
  assert.deepEqual(
    secretStatements({
      environment: jwtEnvironment, masterSecretArn: environment.AURORA_SECRET_ARN, tighten: true,
    }),
    [{ Action: 'secretsmanager:GetSecretValue', Effect: 'Allow', Resource: BACKEND_LOGIN }],
  );
});

test('tightening without the jwt cutover is refused so it cannot cut the iam service off', () => {
  assert.throws(
    () => rolesTemplate({ environment, tighten: true }),
    /tighten applies only to the jwt release/,
  );
});

test('the roles stack wiring passes the master secret only in jwt mode', () => {
  assert.deepEqual(
    rolesStackWiring(jwtDotenv, 'iam', {}),
    { masterSecretArn: undefined, tighten: false },
  );
  assert.deepEqual(
    rolesStackWiring(jwtDotenv, 'jwt', {}),
    { masterSecretArn: environment.AURORA_SECRET_ARN, tighten: false },
  );
});

test('MERIDIAN_TIGHTEN_ROLE=1 tightens in jwt mode and is ignored in iam mode', () => {
  const tighten = { MERIDIAN_TIGHTEN_ROLE: '1' };
  assert.equal(rolesStackWiring(jwtDotenv, 'jwt', tighten).tighten, true);
  assert.equal(rolesStackWiring(jwtDotenv, 'iam', tighten).tighten, false);
  assert.equal(rolesStackWiring(jwtDotenv, 'jwt', { MERIDIAN_TIGHTEN_ROLE: '0' }).tighten, false);
});
