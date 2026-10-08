const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { App, aws_s3_deployment: s3deploy } = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const {
  MeridianWebStack,
  contentSecurityPolicy,
  CONTENT_SECURITY_POLICY,
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

const COGNITO_HOST = 'meridian-travelers-9cb4a1.auth.us-east-1.amazoncognito.com';

function connectSrc(policy) {
  return policy.split('; ').find((directive) => directive.startsWith('connect-src '));
}

test("without a Cognito host the connect-src directive is exactly today's", () => {
  assert.equal(connectSrc(CONTENT_SECURITY_POLICY), "connect-src 'self'");
  assert.equal(contentSecurityPolicy(), CONTENT_SECURITY_POLICY);
});

test('the Cognito host is the only origin added to connect-src, and nothing else changes', () => {
  const policy = contentSecurityPolicy(COGNITO_HOST);
  assert.equal(connectSrc(policy), `connect-src 'self' https://${COGNITO_HOST}`);
  assert.equal(
    policy.replace(connectSrc(policy), connectSrc(CONTENT_SECURITY_POLICY)),
    CONTENT_SECURITY_POLICY,
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
  '',
]) {
  test(`a host that is not a bare Cognito domain is refused: ${JSON.stringify(host)}`, () => {
    if (host === '') {
      assert.equal(contentSecurityPolicy(host), CONTENT_SECURITY_POLICY);
      return;
    }
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
  const source = fs.readFileSync(path.join(FUNCTIONS, 'viewer-request-jwt.js'), 'utf8');
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

function webTemplate(mode, t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'meridian-site-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  fs.writeFileSync(path.join(directory, 'index.html'), '<!doctype html><title>Meridian</title>');
  const sourceAsset = s3deploy.Source.asset;
  t.mock.method(s3deploy.Source, 'asset', () => sourceAsset(directory));
  return Template.fromStack(new MeridianWebStack(new App(), `Web${mode}`, {
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
    }
  });
}
