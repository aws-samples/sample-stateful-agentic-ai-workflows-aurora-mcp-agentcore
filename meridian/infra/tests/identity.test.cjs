const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { App } = require('aws-cdk-lib');
const { Match, Template } = require('aws-cdk-lib/assertions');
const { MeridianIdentityStack } = require('../dist/lib/meridian-identity-stack');
const { resolveIdentityConfig } = require('../dist/lib/identity-config');

const props = {
  env: { account: '123456789012', region: 'us-east-1' },
  domainPrefix: 'meridian-travelers-test',
  callbackUrls: ['https://site.example.test/showcase', 'http://localhost:5173/showcase'],
  logoutUrls: ['https://site.example.test/showcase', 'http://localhost:5173/showcase'],
  clusterArn: 'arn:aws:rds:us-east-1:123456789012:cluster:meridian',
  identitySecretArn: 'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian/aurora/identity-login-AbC123',
  database: 'meridian',
};

function synth(overrides = {}) {
  return Template.fromStack(new MeridianIdentityStack(new App(), 'Identity', { ...props, ...overrides }));
}

test('sign-up is closed, sign-in is by email, and the pool is on a plan that can customize access tokens', () => {
  synth().hasResourceProperties('AWS::Cognito::UserPool', {
    UserPoolTier: 'ESSENTIALS',
    AdminCreateUserConfig: { AllowAdminCreateUserOnly: true },
    UsernameAttributes: ['email'],
    MfaConfiguration: 'OFF',
    Policies: { PasswordPolicy: Match.objectLike({ MinimumLength: 14, RequireSymbols: true }) },
  });
});

test('the pre-token trigger is version 2 so it can change the access token', () => {
  synth().hasResourceProperties('AWS::Cognito::UserPool', {
    LambdaConfig: {
      PreTokenGenerationConfig: { LambdaVersion: 'V2_0', LambdaArn: Match.anyValue() },
    },
  });
});

test('the trigger reads Aurora as the identity login and holds no other Aurora or secret grant', () => {
  const template = synth();
  template.hasResourceProperties('AWS::Lambda::Function', {
    Handler: 'pre_token_generation.lambda_handler',
    Runtime: 'python3.13',
    Timeout: 4,
    Environment: { Variables: {
      AURORA_CLUSTER_ARN: props.clusterArn,
      AURORA_SECRET_ARN: props.identitySecretArn,
      AURORA_DATABASE: 'meridian',
    } },
  });
  const role = Object.values(template.findResources('AWS::IAM::Role'))
    .find((r) => JSON.stringify(r.Properties.ManagedPolicyArns ?? []).includes('AWSLambdaBasicExecutionRole'));
  const arns = JSON.stringify(role.Properties.ManagedPolicyArns);
  assert.ok(arns.includes('arn:aws:iam::123456789012:policy/MeridianIdentityAuroraAccess'));
  assert.equal(Object.keys(template.findResources('AWS::IAM::Policy')).length, 0);
});

test('the web client is public, uses the authorization code flow and lasts one hour', () => {
  synth().hasResourceProperties('AWS::Cognito::UserPoolClient', {
    ClientName: 'meridian-web',
    GenerateSecret: false,
    AllowedOAuthFlows: ['code'],
    AllowedOAuthFlowsUserPoolClient: true,
    AllowedOAuthScopes: ['openid', 'email', 'profile'],
    CallbackURLs: props.callbackUrls,
    LogoutURLs: props.logoutUrls,
    SupportedIdentityProviders: ['COGNITO'],
    AccessTokenValidity: 60,
    IdTokenValidity: 60,
    TokenValidityUnits: Match.objectLike({ AccessToken: 'minutes', IdToken: 'minutes' }),
    EnableTokenRevocation: true,
    PreventUserExistenceErrors: 'ENABLED',
  });
});

test('the client offers no password flow a browser could use', () => {
  const client = Object.values(synth().findResources('AWS::Cognito::UserPoolClient'))[0];
  assert.deepEqual(
    [...client.Properties.ExplicitAuthFlows].sort(),
    ['ALLOW_ADMIN_USER_PASSWORD_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH'],
  );
  assert.ok(!client.Properties.AllowedOAuthFlows.includes('implicit'));
});

test('the client cannot write any user attribute', () => {
  const client = Object.values(synth().findResources('AWS::Cognito::UserPoolClient'))[0];
  assert.deepEqual(client.Properties.WriteAttributes, []);
});

test('the refresh token lasts one day', () => {
  const client = Object.values(synth().findResources('AWS::Cognito::UserPoolClient'))[0];
  assert.equal(client.Properties.RefreshTokenValidity, 1440);
  assert.equal(client.Properties.TokenValidityUnits.RefreshToken, 'minutes');
});

test('the trigger keeps its logs for one month', () => {
  const template = synth();
  template.hasResourceProperties('AWS::Logs::LogGroup', { RetentionInDays: 30 });
  template.hasResourceProperties('AWS::Lambda::Function', {
    LoggingConfig: { LogGroup: Match.anyValue() },
  });
});

test('the trigger asset ships the handler and no bytecode or editor leftovers', () => {
  const source = path.resolve(__dirname, '..', 'functions', 'pre_token_generation');
  const junk = [path.join(source, '__pycache__'), path.join(source, '.DS_Store')];
  const outdir = fs.mkdtempSync(path.join(os.tmpdir(), 'identity-asset-'));
  try {
    fs.mkdirSync(junk[0], { recursive: true });
    fs.writeFileSync(path.join(junk[0], 'x.pyc'), 'junk');
    fs.writeFileSync(junk[1], 'junk');
    new MeridianIdentityStack(new App({ outdir }), 'Identity', props);
    const assets = fs.readdirSync(outdir).filter((name) => name.startsWith('asset.'));
    assert.equal(assets.length, 1);
    assert.deepEqual(fs.readdirSync(path.join(outdir, assets[0])), ['pre_token_generation.py']);
  } finally {
    fs.rmSync(junk[0], { recursive: true, force: true });
    fs.rmSync(junk[1], { force: true });
    fs.rmSync(outdir, { recursive: true, force: true });
  }
});

test('the hosted domain and the identifiers the app needs are published as outputs', () => {
  const outputs = synth().toJSON().Outputs;
  assert.deepEqual(Object.keys(outputs).sort(), ['AppClientId', 'HostedUiDomain', 'Issuer', 'UserPoolId']);
  assert.equal(outputs.HostedUiDomain.Value, 'meridian-travelers-test.auth.us-east-1.amazoncognito.com');
});

test('a domain prefix with a reserved word or bad characters is refused', () => {
  for (const domainPrefix of ['my-cognito-app', 'aws-meridian', 'Upper', 'under_score', '-lead']) {
    assert.throws(() => synth({ domainPrefix }), /domainPrefix/, domainPrefix);
  }
});

test('return URLs must be https, or http on localhost, and carry no fragment', () => {
  assert.throws(() => synth({ callbackUrls: ['http://site.example.test/showcase'] }), /https or http on localhost/);
  assert.throws(() => synth({ logoutUrls: ['https://site.example.test/#x'] }), /fragment/);
  assert.throws(() => synth({ callbackUrls: [] }), /callback URL/);
});

test('return URLs with a wildcard are refused', () => {
  assert.throws(() => synth({ callbackUrls: ['https://*.example.test/x'] }), /wildcard/);
  assert.throws(() => synth({ logoutUrls: ['https://a.example.test/*'] }), /wildcard/);
});

const CLUSTER = 'arn:aws:rds:us-east-1:123456789012:cluster:meridian';
const SECRET = 'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian/identity-AbC123';
const dotenv = (extra = {}) => ({
  AURORA_CLUSTER_ARN: CLUSTER, AURORA_IDENTITY_SECRET_ARN: SECRET, ...extra,
});

test('the deploy inputs resolve when the credentials match the cluster account', () => {
  assert.deepEqual(resolveIdentityConfig(dotenv(), '123456789012'), {
    account: '123456789012', region: 'us-east-1', clusterArn: CLUSTER,
    identitySecretArn: SECRET, database: 'meridian',
  });
});

test('the deploy refuses when the credentials account is unset or different', () => {
  assert.throws(() => resolveIdentityConfig(dotenv(), undefined), /CDK_DEFAULT_ACCOUNT.*cdk deploy -a/);
  assert.throws(() => resolveIdentityConfig(dotenv(), ''), /CDK_DEFAULT_ACCOUNT/);
  assert.throws(() => resolveIdentityConfig(dotenv(), '999999999999'), /different account/);
});

test('the deploy refuses a secret from another account or Region, or missing settings', () => {
  const other = (arn) => dotenv({ AURORA_IDENTITY_SECRET_ARN: arn });
  assert.throws(
    () => resolveIdentityConfig(other(SECRET.replace('123456789012', '210987654321')), '123456789012'),
    /SECRET_ARN is in a different account/,
  );
  assert.throws(
    () => resolveIdentityConfig(other(SECRET.replace('us-east-1', 'us-west-2')), '123456789012'),
    /SECRET_ARN is in a different Region/,
  );
  assert.throws(() => resolveIdentityConfig({}, '123456789012'), /AURORA_CLUSTER_ARN/);
  assert.throws(
    () => resolveIdentityConfig({ AURORA_CLUSTER_ARN: CLUSTER }, '123456789012'),
    /AURORA_IDENTITY_SECRET_ARN/,
  );
});
