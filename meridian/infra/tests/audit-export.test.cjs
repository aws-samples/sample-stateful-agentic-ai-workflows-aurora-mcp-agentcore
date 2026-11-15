const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { App } = require('aws-cdk-lib');
const { Match, Template } = require('aws-cdk-lib/assertions');
const {
  MeridianAuditExportStack, AUDIT_EXPORT_KEY_PREFIX, BACKLOG_ALARM_SECONDS,
} = require('../dist/lib/meridian-audit-export-stack');
const { resolveAuditExportConfig } = require('../dist/lib/audit-export-config');

const props = {
  env: { account: '123456789012', region: 'us-east-1' },
  clusterArn: 'arn:aws:rds:us-east-1:123456789012:cluster:meridian',
  auditExportSecretArn: 'arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian/aurora/audit-export-login-AbC123',
  database: 'meridian',
};

function synth(overrides = {}) {
  return Template.fromStack(new MeridianAuditExportStack(new App(), 'AuditExport', { ...props, ...overrides }));
}

function statements(template, type) {
  return Object.values(template.findResources(type)).flatMap((resource) => {
    const doc = resource.Properties.PolicyDocument ?? {};
    return [].concat(doc.Statement ?? []);
  });
}

const asList = (value) => [].concat(value ?? []);

test('the bucket has Object Lock with a default retention, and is versioned', () => {
  const template = synth();
  template.hasResourceProperties('AWS::S3::Bucket', {
    ObjectLockEnabled: true,
    ObjectLockConfiguration: {
      ObjectLockEnabled: 'Enabled',
      Rule: { DefaultRetention: { Mode: 'GOVERNANCE', Days: 30 } },
    },
    VersioningConfiguration: { Status: 'Enabled' },
  });
});

test('retention mode and days come from the props', () => {
  synth({ retentionMode: 'COMPLIANCE', retentionDays: 365 }).hasResourceProperties('AWS::S3::Bucket', {
    ObjectLockConfiguration: { Rule: { DefaultRetention: { Mode: 'COMPLIANCE', Days: 365 } } },
  });
});

test('objects are encrypted with the stack key, which rotates and outlives the stack', () => {
  const template = synth();
  template.hasResourceProperties('AWS::S3::Bucket', {
    BucketEncryption: { ServerSideEncryptionConfiguration: [Match.objectLike({
      BucketKeyEnabled: true,
      ServerSideEncryptionByDefault: { SSEAlgorithm: 'aws:kms', KMSMasterKeyID: Match.anyValue() },
    })] },
  });
  template.hasResource('AWS::KMS::Key', { Properties: { EnableKeyRotation: true }, DeletionPolicy: 'Retain' });
  template.hasResource('AWS::S3::Bucket', { DeletionPolicy: 'Retain' });
});

test('public access is blocked in every way and the policy refuses plain HTTP', () => {
  const template = synth();
  template.hasResourceProperties('AWS::S3::Bucket', {
    PublicAccessBlockConfiguration: {
      BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true,
    },
  });
  const tls = statements(template, 'AWS::S3::BucketPolicy').find(
    (s) => s.Effect === 'Deny' && s.Condition?.Bool?.['aws:SecureTransport'] === 'false');
  assert.ok(tls, 'a TLS-only deny statement');
  assert.deepEqual(asList(tls.Action), ['s3:*']);
});

test('the bucket policy denies delete and governance bypass to every principal', () => {
  const deny = statements(synth(), 'AWS::S3::BucketPolicy').find((s) => s.Sid === 'DenyDeleteAndBypass');
  assert.equal(deny.Effect, 'Deny');
  assert.deepEqual(deny.Principal, { AWS: '*' });
  assert.deepEqual(asList(deny.Action).sort(),
    ['s3:BypassGovernanceRetention', 's3:DeleteObject', 's3:DeleteObjectVersion']);
});

test('every write must carry If-None-Match, by the documented Null condition on s3:if-none-match', () => {
  const deny = statements(synth(), 'AWS::S3::BucketPolicy').find((s) => s.Sid === 'DenyWriteWithoutIfNoneMatch');
  assert.equal(deny.Effect, 'Deny');
  assert.deepEqual(deny.Principal, { AWS: '*' });
  assert.deepEqual(asList(deny.Action), ['s3:PutObject']);
  assert.deepEqual(deny.Condition, { Null: { 's3:if-none-match': 'true' } });
});

test('the exporter log keeps its chain-head anchors for a year and outlives the stack', () => {
  const template = synth();
  template.hasResource('AWS::Logs::LogGroup', { Properties: { RetentionInDays: 365 }, DeletionPolicy: 'Retain' });
  synth({ logRetentionDays: 731 }).hasResourceProperties('AWS::Logs::LogGroup', { RetentionInDays: 731 });
  assert.throws(() => synth({ logRetentionDays: 401 }), /logRetentionDays/);
});

test('a compliance bucket needs no bypass denial because nobody can bypass it', () => {
  const deny = statements(synth({ retentionMode: 'COMPLIANCE' }), 'AWS::S3::BucketPolicy')
    .find((s) => s.Sid === 'DenyDeleteAndBypass');
  assert.deepEqual(asList(deny.Action).sort(), ['s3:DeleteObject', 's3:DeleteObjectVersion']);
});

test('no identity policy anywhere can delete, change retention or bypass it', () => {
  const template = synth();
  const allowed = [...statements(template, 'AWS::IAM::Policy'), ...statements(template, 'AWS::IAM::Role')]
    .filter((s) => s.Effect === 'Allow').flatMap((s) => asList(s.Action));
  assert.ok(allowed.length > 0);
  for (const action of allowed) {
    assert.ok(!/delete|retention|bypass|legalhold|\*/i.test(action), `${action} must not be granted`);
  }
});

test('the exporter can put and get objects under the prefix, list that prefix, and use the key', () => {
  const template = synth();
  const policy = Object.values(template.findResources('AWS::IAM::Policy'))
    .find((r) => JSON.stringify(r.Properties.PolicyDocument).includes('WriteAndReadExportObjects'));
  const byId = Object.fromEntries(policy.Properties.PolicyDocument.Statement.map((s) => [s.Sid, s]));
  assert.deepEqual(Object.keys(byId).sort(), ['ListExportObjects', 'UseExportKey', 'WriteAndReadExportObjects']);
  assert.deepEqual(asList(byId.WriteAndReadExportObjects.Action).sort(), ['s3:GetObject', 's3:PutObject']);
  assert.match(JSON.stringify(byId.WriteAndReadExportObjects.Resource), new RegExp(`${AUDIT_EXPORT_KEY_PREFIX}/\\*`));
  assert.deepEqual(asList(byId.ListExportObjects.Action), ['s3:ListBucket']);
  assert.deepEqual(byId.ListExportObjects.Condition, { StringLike: { 's3:prefix': [`${AUDIT_EXPORT_KEY_PREFIX}/*`] } });
  assert.deepEqual(asList(byId.UseExportKey.Action).sort(), ['kms:Decrypt', 'kms:GenerateDataKey']);
});

test('the Lambda reads Aurora as the audit export login through the managed policy alone', () => {
  const template = synth();
  template.hasResourceProperties('AWS::Lambda::Function', {
    Handler: 'audit_export.lambda_handler',
    Runtime: 'python3.13',
    Timeout: 120,
    Environment: { Variables: {
      AURORA_CLUSTER_ARN: props.clusterArn,
      AURORA_SECRET_ARN: props.auditExportSecretArn,
      AURORA_DATABASE: 'meridian',
      AUDIT_EXPORT_BUCKET: Match.anyValue(),
      AUDIT_EXPORT_SETTLE_SECONDS: '180',
    } },
  });
  const role = Object.values(template.findResources('AWS::IAM::Role'))
    .find((r) => JSON.stringify(r.Properties.ManagedPolicyArns ?? []).includes('AWSLambdaBasicExecutionRole'));
  assert.ok(JSON.stringify(role.Properties.ManagedPolicyArns)
    .includes('arn:aws:iam::123456789012:policy/MeridianAuditExportAuroraAccess'));
  const actions = statements(template, 'AWS::IAM::Policy').flatMap((s) => asList(s.Action));
  assert.ok(!actions.some((a) => /^(rds-data|secretsmanager):/.test(a)),
    'the Data API and the secret are granted only by the managed policy');
});

test('the schedule runs the Lambda every five minutes with its own invoke-only role', () => {
  const template = synth();
  template.hasResourceProperties('AWS::Scheduler::Schedule', {
    ScheduleExpression: 'rate(5 minutes)',
    FlexibleTimeWindow: { Mode: 'OFF' },
    State: 'ENABLED',
    Target: Match.objectLike({
      Arn: Match.anyValue(), RoleArn: Match.anyValue(),
      RetryPolicy: { MaximumRetryAttempts: 2, MaximumEventAgeInSeconds: 600 },
    }),
  });
  template.hasResourceProperties('AWS::IAM::Role', {
    AssumeRolePolicyDocument: { Statement: [Match.objectLike({
      Principal: { Service: 'scheduler.amazonaws.com' },
      Condition: { StringEquals: { 'aws:SourceAccount': '123456789012' } },
    })] },
  });
  const invoke = statements(template, 'AWS::IAM::Policy').filter((s) => asList(s.Action).includes('lambda:InvokeFunction'));
  assert.equal(invoke.length, 1);
  assert.deepEqual(asList(invoke[0].Action), ['lambda:InvokeFunction']);
});

test('the schedule period is a prop, and one minute is singular', () => {
  synth({ scheduleMinutes: 15 }).hasResourceProperties('AWS::Scheduler::Schedule', { ScheduleExpression: 'rate(15 minutes)' });
  synth({ scheduleMinutes: 1 }).hasResourceProperties('AWS::Scheduler::Schedule', { ScheduleExpression: 'rate(1 minute)' });
});

test('alarms cover Lambda errors, a missing heartbeat and export lag', () => {
  const template = synth();
  template.hasResourceProperties('AWS::CloudWatch::Alarm', {
    MetricName: 'Errors', Namespace: 'AWS/Lambda', Threshold: 1, EvaluationPeriods: 1,
    ComparisonOperator: 'GreaterThanOrEqualToThreshold', TreatMissingData: 'notBreaching',
  });
  template.hasResourceProperties('AWS::CloudWatch::Alarm', {
    MetricName: 'RunsCompleted', Namespace: 'Meridian/AuditExport', Statistic: 'Sum', Period: 300,
    Threshold: 1, EvaluationPeriods: 3, DatapointsToAlarm: 3,
    ComparisonOperator: 'LessThanThreshold', TreatMissingData: 'breaching',
  });
  template.hasResourceProperties('AWS::CloudWatch::Alarm', {
    MetricName: 'BacklogAgeSeconds', Namespace: 'Meridian/AuditExport', Statistic: 'Maximum',
    Threshold: BACKLOG_ALARM_SECONDS, ComparisonOperator: 'GreaterThanThreshold',
  });
  assert.equal(Object.keys(template.findResources('AWS::CloudWatch::Alarm')).length, 3);
});

test('alarms notify a topic only when one is given', () => {
  const none = synth();
  Object.values(none.findResources('AWS::CloudWatch::Alarm'))
    .forEach((alarm) => assert.equal(alarm.Properties.AlarmActions, undefined));
  const topic = 'arn:aws:sns:us-east-1:123456789012:ops';
  const some = synth({ alarmTopicArn: topic });
  Object.values(some.findResources('AWS::CloudWatch::Alarm'))
    .forEach((alarm) => assert.deepEqual(alarm.Properties.AlarmActions, [topic]));
});

test('the stack is termination protected', () => {
  const stack = new MeridianAuditExportStack(new App(), 'AuditExport', props);
  assert.equal(stack.terminationProtection, true);
});

test('the key prefix is the one the Lambda writes under', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'functions', 'audit_export', 'audit_export.py'), 'utf8');
  assert.equal(source.match(/^KEY_PREFIX = "([^"]+)"/m)[1], AUDIT_EXPORT_KEY_PREFIX);
});

test('invalid settings are refused', () => {
  assert.throws(() => synth({ retentionMode: 'FOREVER' }), /GOVERNANCE or COMPLIANCE/);
  assert.throws(() => synth({ retentionDays: 0 }), /retentionDays/);
  assert.throws(() => synth({ retentionDays: 1.5 }), /retentionDays/);
  assert.throws(() => synth({ scheduleMinutes: 0 }), /scheduleMinutes/);
  assert.throws(() => synth({ settleSeconds: -1 }), /settleSeconds/);
});

test('the deploy config needs the cluster, the login secret and credentials for the same account', () => {
  const dotenv = { AURORA_CLUSTER_ARN: props.clusterArn, AURORA_AUDIT_EXPORT_SECRET_ARN: props.auditExportSecretArn };
  assert.deepEqual(resolveAuditExportConfig(dotenv, '123456789012'), {
    account: '123456789012', region: 'us-east-1', clusterArn: props.clusterArn,
    auditExportSecretArn: props.auditExportSecretArn, database: 'meridian', alarmTopicArn: undefined,
  });
  assert.throws(() => resolveAuditExportConfig({}, '123456789012'), /AURORA_CLUSTER_ARN/);
  assert.throws(() => resolveAuditExportConfig({ AURORA_CLUSTER_ARN: props.clusterArn }, '123456789012'),
    /AURORA_AUDIT_EXPORT_SECRET_ARN; run scripts\/provision_service_logins.py --login audit/);
  assert.throws(() => resolveAuditExportConfig(
    { ...dotenv, AURORA_AUDIT_EXPORT_SECRET_ARN: props.auditExportSecretArn.replace('123456789012', '111122223333') },
    '123456789012'), /different account/);
  assert.throws(() => resolveAuditExportConfig(
    { ...dotenv, AURORA_AUDIT_EXPORT_SECRET_ARN: props.auditExportSecretArn.replace('us-east-1', 'us-west-2') },
    '123456789012'), /different Region/);
  assert.throws(() => resolveAuditExportConfig(dotenv, undefined), /CDK_DEFAULT_ACCOUNT/);
  assert.throws(() => resolveAuditExportConfig(dotenv, '111122223333'), /different account/);
});

test('the alarm topic is read from the deploy config and must match the cluster', () => {
  const dotenv = { AURORA_CLUSTER_ARN: props.clusterArn, AURORA_AUDIT_EXPORT_SECRET_ARN: props.auditExportSecretArn };
  const topic = 'arn:aws:sns:us-east-1:123456789012:meridian-audit-export-alarms';
  const withTopic = (value) => resolveAuditExportConfig({ ...dotenv, AUDIT_EXPORT_ALARM_TOPIC_ARN: value }, '123456789012');
  assert.equal(withTopic(topic).alarmTopicArn, topic);
  assert.equal(withTopic('').alarmTopicArn, undefined);
  assert.throws(() => withTopic(topic.replace('123456789012', '111122223333')), /account and Region/);
  assert.throws(() => withTopic(topic.replace('us-east-1', 'us-west-2')), /account and Region/);
  assert.throws(() => withTopic('arn:aws:sqs:us-east-1:123456789012:queue'), /SNS topic ARN/);
});
