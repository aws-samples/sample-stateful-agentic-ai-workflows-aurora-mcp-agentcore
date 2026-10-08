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
