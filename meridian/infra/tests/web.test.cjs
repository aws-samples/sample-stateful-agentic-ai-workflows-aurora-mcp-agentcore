const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { App, aws_s3_deployment: s3deploy, aws_cloudfront: cloudfront } = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const {
  MeridianWebStack,
  contentSecurityPolicy,
  CONTENT_SECURITY_POLICY,
  cognitoHostFromEnv,
} = require('../dist/lib/meridian-web-stack');

test('every hosted route receives the browser security policy', (t) => {
  // Synthesise the real distribution without requiring a frontend build in
  // the separate infrastructure CI job.
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'meridian-site-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  fs.writeFileSync(path.join(directory, 'index.html'), '<!doctype html><title>Meridian</title>');
  const sourceAsset = s3deploy.Source.asset;
  t.mock.method(s3deploy.Source, 'asset', () => sourceAsset(directory));
  const template = Template.fromStack(new MeridianWebStack(new App(), 'Web', {
    env: { account: '123456789012', region: 'us-east-1' },
    backendHost: 'test.us-east-1.awsapprunner.com',
  }));
  const [[policyId, policy]] = Object.entries(template.findResources('AWS::CloudFront::ResponseHeadersPolicy'));
  const security = policy.Properties.ResponseHeadersPolicyConfig.SecurityHeadersConfig;
  assert.match(security.ContentSecurityPolicy.ContentSecurityPolicy, /script-src 'self'/);
  assert.match(security.ContentSecurityPolicy.ContentSecurityPolicy, /frame-ancestors 'none'/);
  assert.equal(security.ContentTypeOptions.Override, true);
  assert.equal(security.FrameOptions.FrameOption, 'DENY');
  assert.equal(security.StrictTransportSecurity.AccessControlMaxAgeSec, 31536000);
  const distribution = Object.values(template.findResources('AWS::CloudFront::Distribution'))[0];
  const config = distribution.Properties.DistributionConfig;
  for (const behavior of [config.DefaultCacheBehavior, ...config.CacheBehaviors]) {
    assert.deepEqual(behavior.ResponseHeadersPolicyId, { Ref: policyId });
  }
});

const COGNITO_HOST = 'meridian-x.auth.us-east-1.amazoncognito.com';

function connectSrc(policy) {
  return policy.split('; ').find((directive) => directive.startsWith('connect-src '));
}

const REVIEWED_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: https:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
  "form-action 'self'",
].join('; ');

test('the default policy is exactly the reviewed string', () => {
  assert.equal(CONTENT_SECURITY_POLICY, REVIEWED_POLICY);
  assert.equal(contentSecurityPolicy(), REVIEWED_POLICY);
  assert.equal(contentSecurityPolicy(''), REVIEWED_POLICY);
  assert.equal(connectSrc(CONTENT_SECURITY_POLICY), "connect-src 'self'");
});

test('bin/meridian-web.ts forwards the trimmed hosted UI domain, or nothing when blank', () => {
  assert.equal(cognitoHostFromEnv(`  ${COGNITO_HOST}\n`), COGNITO_HOST);
  assert.equal(cognitoHostFromEnv(''), undefined);
  assert.equal(cognitoHostFromEnv('   '), undefined);
  assert.equal(cognitoHostFromEnv(undefined), undefined);
  const bin = fs.readFileSync(path.join(__dirname, '..', 'bin', 'meridian-web.ts'), 'utf8');
  assert.match(bin, /cognitoHost: cognitoHostFromEnv\(process\.env\.MERIDIAN_COGNITO_HOSTED_UI_DOMAIN\)/);
});

test('the Cognito host is the only origin added to connect-src, and nothing else changes', () => {
  const policy = contentSecurityPolicy(COGNITO_HOST);
  assert.equal(connectSrc(policy), `connect-src 'self' https://${COGNITO_HOST}`);
  assert.equal(
    policy,
    REVIEWED_POLICY.replace("connect-src 'self'", `connect-src 'self' https://${COGNITO_HOST}`),
  );
  assert.ok(!policy.includes('*'));
});

for (const host of [
  'evil.example.com',
  `${COGNITO_HOST}/oauth2/token`,
  `https://${COGNITO_HOST}`,
  '*.auth.us-east-1.amazoncognito.com',
  `${COGNITO_HOST}; script-src *`,
  'x.auth.us-east-1.amazoncognito.com.evil.com',
  'x.auth.evil.amazoncognito.com',
]) {
  test(`a host that is not a bare Cognito domain is refused: ${JSON.stringify(host)}`, () => {
    assert.throws(() => contentSecurityPolicy(host), /Cognito hosted UI domain/);
  });
}

test('the distribution sends the policy that allows the Cognito host on every route', (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'meridian-site-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  fs.writeFileSync(path.join(directory, 'index.html'), '<!doctype html><title>Meridian</title>');
  const sourceAsset = s3deploy.Source.asset;
  t.mock.method(s3deploy.Source, 'asset', () => sourceAsset(directory));
  const template = Template.fromStack(new MeridianWebStack(new App(), 'WebCognito', {
    env: { account: '123456789012', region: 'us-east-1' },
    backendHost: 'test.us-east-1.awsapprunner.com',
    cognitoHost: COGNITO_HOST,
  }));
  const [[, policy]] = Object.entries(template.findResources('AWS::CloudFront::ResponseHeadersPolicy'));
  const csp = policy.Properties.ResponseHeadersPolicyConfig.SecurityHeadersConfig.ContentSecurityPolicy;
  assert.equal(connectSrc(csp.ContentSecurityPolicy), `connect-src 'self' https://${COGNITO_HOST}`);
  assert.equal(csp.Override, true);
});

const FUNCTIONS = path.join(__dirname, '..', 'functions');
const CACHING_DISABLED = '4135ea2d-6df8-44a3-9df3-4b5a84be39ad';
const ALL_VIEWER_EXCEPT_HOST_HEADER = 'b689b0a8-53d0-40ab-baf2-68738e2966ac';

function loadHandler(file) {
  const source = fs.readFileSync(path.join(FUNCTIONS, file), 'utf8').replace(
    /^import cf from 'cloudfront';$/m,
    "const cf = { kvs: () => ({ get: async () => { throw new Error('no store'); } }) };",
  );
  return new Function(`${source}\nreturn handler;`)();
}

function request(uri, authorization) {
  const headers = authorization ? { authorization: { value: authorization } } : {};
  return { request: { uri, headers } };
}

const jwtViewer = loadHandler('viewer-request-jwt.js');

for (const uri of ['/api/chat/stream', '/api/me', '/api/health', '/health']) {
  test(`the jwt viewer function hands ${uri} to the backend with the browser's own token`, async () => {
    const result = await jwtViewer(request(uri, 'Bearer browser-token'));
    assert.equal(result.statusCode, undefined);
    assert.equal(result.headers.authorization.value, 'Bearer browser-token');
    assert.equal(result.uri, uri);
  });
}

test('the jwt viewer function never asks for a Basic credential or reads the access store', async () => {
  const result = await jwtViewer(request('/showcase'));
  assert.equal(result.statusCode, undefined);
  assert.equal(result.headers['www-authenticate'], undefined);
  const source = fs.readFileSync(path.join(FUNCTIONS, 'viewer-request-jwt.js'), 'utf8')
    .split('\n').filter((line) => !line.trimStart().startsWith('//')).join('\n');
  assert.ok(!source.includes('kvs') && !source.includes('Basic'));
});

test('the jwt viewer function keeps the token away from S3 and rewrites client routes', async () => {
  const cases = [['/', '/index.html'], ['/showcase', '/index.html'], ['/assets/app.js', '/assets/app.js']];
  for (const [uri, rewritten] of cases) {
    const result = await jwtViewer(request(uri, 'Bearer browser-token'));
    assert.equal(result.headers.authorization, undefined, uri);
    assert.equal(result.uri, rewritten);
  }
});

const TOKEN = 'Bearer browser-token';
const CLASSIFICATION = [
  ['/api/../index.html', 'api'],
  ['//api', '/index.html'],
  ['/api%2Fx', '/index.html'],
  ['/APIx', '/index.html'],
  ['/API/x', '/index.html'],
  ['/api', '/index.html'],
  ['/health', 'api'],
  ['/health/', '/index.html'],
  ['/healthz', '/index.html'],
  ['/x/../api/y', '/x/../api/y'],
  ['/api/..', 'api'],
  ['/apix', '/index.html'],
  ['/apis/x', '/index.html'],
  ['/api/x', 'api'],
];

test('the jwt viewer function classifies every tricky uri exactly', async () => {
  for (const [uri, expected] of CLASSIFICATION) {
    const headers = { authorization: { value: TOKEN } };
    const result = await jwtViewer({ request: { uri, headers } });
    if (expected === 'api') {
      assert.equal(result.uri, uri, uri);
      assert.equal(result.headers.authorization.value, TOKEN, uri);
    } else {
      assert.equal(result.uri, expected, uri);
      assert.equal(result.headers.authorization, undefined, uri);
    }
  }
});

test('the jwt viewer function removes a multiValue Authorization header entirely', async () => {
  const headers = { authorization: { value: TOKEN, multiValue: [{ value: TOKEN }] } };
  const result = await jwtViewer({ request: { uri: '/showcase', headers } });
  assert.equal('authorization' in result.headers, false);
});

test('the jwt viewer function creates no header on an API path with empty headers', async () => {
  const result = await jwtViewer({ request: { uri: '/api/me', headers: {} } });
  assert.deepEqual(result.headers, {});
});

test('the jwt viewer function passes an API request without a headers object through', async () => {
  const api = await jwtViewer({ request: { uri: '/api/me' } });
  assert.equal(api.headers, undefined);
});

function webTemplate(mode, t, stackId = `Web${mode}`) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'meridian-site-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  fs.writeFileSync(path.join(directory, 'index.html'), '<!doctype html><title>Meridian</title>');
  const sourceAsset = s3deploy.Source.asset;
  t.mock.method(s3deploy.Source, 'asset', () => sourceAsset(directory));
  return Template.fromStack(new MeridianWebStack(new App(), stackId, {
    env: { account: '123456789012', region: 'us-east-1' },
    backendHost: 'test.us-east-1.awsapprunner.com',
    ...(mode ? { identityMode: mode } : {}),
  }));
}

function viewerCode(mode, t) {
  const [viewer] = Object.values(webTemplate(mode, t).findResources('AWS::CloudFront::Function'));
  return viewer.Properties.FunctionCode;
}

for (const mode of [undefined, 'iam']) {
  test(`the ${mode ?? 'default'} release deploys the established viewer function byte for byte`, (t) => {
    assert.equal(viewerCode(mode, t), fs.readFileSync(path.join(FUNCTIONS, 'viewer-request.js'), 'utf8'));
  });
}

test('the jwt release deploys the viewer function without Basic or the shared token', (t) => {
  const code = viewerCode('jwt', t);
  assert.equal(code, fs.readFileSync(path.join(FUNCTIONS, 'viewer-request-jwt.js'), 'utf8'));
  assert.ok(!code.includes("kvs.get('basic')") && !code.includes("kvs.get('token')"));
});

for (const mode of ['iam', 'jwt']) {
  test(`the ${mode} release forwards the viewer Authorization header to the API origin`, (t) => {
    const [distribution] = Object.values(webTemplate(mode, t).findResources('AWS::CloudFront::Distribution'));
    const { CacheBehaviors: behaviors } = distribution.Properties.DistributionConfig;
    assert.deepEqual(behaviors.map((b) => b.PathPattern).sort(), ['/api/*', '/health']);
    for (const behavior of behaviors) {
      assert.equal(behavior.OriginRequestPolicyId, ALL_VIEWER_EXCEPT_HOST_HEADER);
      assert.equal(behavior.FunctionAssociations.length, 1);
      assert.equal(behavior.CachePolicyId, CACHING_DISABLED);
    }
  });
}

test('the managed CachingDisabled id matches the one the stack uses', () => {
  assert.equal(cloudfront.CachePolicy.CACHING_DISABLED.cachePolicyId, CACHING_DISABLED);
});

test('the default and iam templates are identical, and jwt differs only in the function', (t) => {
  const plain = webTemplate(undefined, t, 'WebCompare').toJSON();
  assert.deepEqual(webTemplate('iam', t, 'WebCompare').toJSON(), plain);
  const jwt = webTemplate('jwt', t, 'WebCompare').toJSON();
  const strip = (template) => JSON.parse(JSON.stringify(template, (key, value) => (
    key === 'FunctionCode' || key === 'Comment' ? undefined : value
  )));
  const [viewerId] = Object.keys(
    Object.fromEntries(Object.entries(jwt.Resources).filter(([, r]) => r.Type === 'AWS::CloudFront::Function')),
  );
  const changed = Object.keys(jwt.Resources).filter(
    (id) => JSON.stringify(jwt.Resources[id]) !== JSON.stringify(plain.Resources[id]),
  );
  assert.deepEqual(changed.filter((id) => id !== viewerId), []);
  assert.deepEqual(strip(jwt), strip(plain));
});
