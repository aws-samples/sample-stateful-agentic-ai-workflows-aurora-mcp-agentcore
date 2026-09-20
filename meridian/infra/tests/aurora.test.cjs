const assert = require('node:assert/strict');
const { test } = require('node:test');
const { App } = require('aws-cdk-lib');
const { Template, Match } = require('aws-cdk-lib/assertions');
const { MeridianAuroraStack } = require('../dist/lib/meridian-aurora-stack');

function synth(snapshotArn) {
  return Template.fromStack(new MeridianAuroraStack(new App(), 'Aurora', {
    env: { account: '123456789012', region: 'us-east-1' }, clusterIdentifier: 'isolated-rehearsal',
    vpcId: 'vpc-12345678', subnetIds: ['subnet-12345678', 'subnet-87654321'], engineVersion: '18.3', snapshotArn,
  }));
}
for (const snapshotArn of [undefined, 'arn:aws:rds:us-east-1:123456789012:cluster-snapshot:reviewed']) {
  test(`Aurora ${snapshotArn ? 'restore' : 'creation'} is encrypted, protected, bounded and private`, () => {
    const template = synth(snapshotArn);
    template.hasResourceProperties('AWS::RDS::DBCluster', {
      StorageEncrypted: true, KmsKeyId: 'alias/aws/rds', DeletionProtection: true,
      BackupRetentionPeriod: 7, EnableHttpEndpoint: true,
      ServerlessV2ScalingConfiguration: { MinCapacity: 0.5, MaxCapacity: 16 },
      MasterUserPassword: Match.absent(),
      ...(snapshotArn ? { SnapshotIdentifier: snapshotArn, ManageMasterUserPassword: Match.absent() }
        : { ManageMasterUserPassword: true }),
    });
    template.hasResourceProperties('AWS::RDS::DBInstance', { PubliclyAccessible: false });
    template.hasResourceProperties('AWS::EC2::SecurityGroup', { SecurityGroupIngress: Match.absent() });
    const cluster = Object.values(template.findResources('AWS::RDS::DBCluster'))[0];
    assert.equal(cluster.DeletionPolicy, 'Snapshot');
    assert.equal(cluster.UpdateReplacePolicy, 'Snapshot');
  });
}
