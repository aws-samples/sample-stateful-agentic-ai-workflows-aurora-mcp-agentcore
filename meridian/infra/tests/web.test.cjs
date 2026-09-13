const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { App, aws_s3_deployment: s3deploy } = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const { MeridianWebStack } = require('../dist/lib/meridian-web-stack');

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
