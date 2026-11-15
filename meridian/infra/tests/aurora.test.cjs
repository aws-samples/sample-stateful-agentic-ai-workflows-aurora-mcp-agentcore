const assert = require('node:assert/strict');
const { test } = require('node:test');
const { App } = require('aws-cdk-lib');
const { Template, Match } = require('aws-cdk-lib/assertions');
const { MeridianAuroraStack } = require('../dist/lib/meridian-aurora-stack');

function synth(snapshotArn) {
  return Template.fromStack(new MeridianAuroraStack(new App(), 'Aurora', {
    env: { account: '123456789012', region: 'us-east-1' }, clusterIdentifier: 'isolated-rehearsal',
    vpcId: 'vpc-12345678', subnetIds: ['subnet-12345678', 'subnet-87654321'], engineVersion: '18.6', snapshotArn,
    readerAvailabilityZone: 'us-east-1b',
  }));
}
for (const snapshotArn of [undefined, 'arn:aws:rds:us-east-1:123456789012:cluster-snapshot:reviewed']) {
  test(`Aurora ${snapshotArn ? 'restore' : 'creation'} is encrypted, protected, bounded and private`, () => {
    const template = synth(snapshotArn);
    template.hasResourceProperties('AWS::RDS::DBCluster', {
      StorageEncrypted: true, KmsKeyId: 'alias/aws/rds', DeletionProtection: true,
      BackupRetentionPeriod: 7, EnableHttpEndpoint: true,
      ServerlessV2ScalingConfiguration: { MinCapacity: 1, MaxCapacity: 16 },
      MasterUserPassword: Match.absent(),
      ...(snapshotArn ? { SnapshotIdentifier: snapshotArn, ManageMasterUserPassword: Match.absent() }
        : { ManageMasterUserPassword: true }),
    });
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      PubliclyAccessible: false, AutoMinorVersionUpgrade: false,
    });
    template.hasResourceProperties('AWS::RDS::DBCluster', { EngineVersion: '18.6' });
    template.hasResourceProperties('AWS::EC2::SecurityGroup', { SecurityGroupIngress: Match.absent() });
    const cluster = Object.values(template.findResources('AWS::RDS::DBCluster'))[0];
    assert.equal(cluster.DeletionPolicy, 'Snapshot');
    assert.equal(cluster.UpdateReplacePolicy, 'Snapshot');
    // A retained instance would make the cluster delete fail and keep billing after cdk destroy.
    const instances = template.findResources('AWS::RDS::DBInstance');
    assert.deepEqual(Object.keys(instances).sort(), ['Reader', 'Writer']);
    for (const instance of Object.values(instances)) {
      assert.equal(instance.DeletionPolicy, 'Delete');
      assert.equal(instance.UpdateReplacePolicy, 'Delete');
      assert.equal(instance.Properties.DBInstanceClass, 'db.serverless');
      assert.equal(instance.Properties.PubliclyAccessible, false);
      assert.equal(instance.Properties.AutoMinorVersionUpgrade, false);
    }
    // Tier 0 and 1 readers scale with the writer, so a failover lands on warm capacity.
    // The writer keeps exactly the properties it was created with, so an update never re-sends
    // them, and keeps the default promotion tier.
    assert.deepEqual(Object.keys(instances.Writer.Properties).sort(), [
      'AutoMinorVersionUpgrade', 'DBClusterIdentifier', 'DBInstanceClass', 'Engine',
      'PubliclyAccessible',
    ]);
    assert.equal(instances.Reader.Properties.PromotionTier, 1);
    assert.equal(instances.Reader.Properties.AvailabilityZone, 'us-east-1b');
  });
}

test('Aurora refuses an engine version without its minor', () => {
  assert.throws(() => new MeridianAuroraStack(new App(), 'Aurora', {
    env: { account: '123456789012', region: 'us-east-1' }, clusterIdentifier: 'isolated-rehearsal',
    vpcId: 'vpc-12345678', subnetIds: ['subnet-12345678', 'subnet-87654321'], engineVersion: '18',
    readerAvailabilityZone: 'us-east-1b',
  }), /verified Aurora PostgreSQL 18 version/);
});
