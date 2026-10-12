import {
  CfnOutput, RemovalPolicy, Stack, type StackProps,
  aws_ec2 as ec2, aws_rds as rds,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';

export interface MeridianAuroraStackProps extends StackProps {
  clusterIdentifier: string;
  vpcId: string;
  subnetIds: string[];
  engineVersion: string;
  /** Restore creates a separate cluster; it never redirects application traffic. */
  snapshotArn?: string;
}

/** Isolated, retained Aurora resources. No plaintext credential ever enters CDK. */
export class MeridianAuroraStack extends Stack {
  constructor(scope: Construct, id: string, props: MeridianAuroraStackProps) {
    super(scope, id, { ...props, terminationProtection: true });
    if (!/^[a-z][a-z0-9-]{0,61}[a-z0-9]$/.test(props.clusterIdentifier)
        || props.clusterIdentifier.includes('--')) throw new Error('Invalid cluster identifier');
    if (new Set(props.subnetIds).size < 2) throw new Error('Two distinct subnet IDs are required');
    if (!/^18\.\d+$/.test(props.engineVersion)) throw new Error('This template requires a verified Aurora PostgreSQL 18 version');

    const subnetGroup = new rds.CfnDBSubnetGroup(this, 'Subnets', {
      dbSubnetGroupDescription: 'Meridian private Aurora instances', subnetIds: props.subnetIds,
    });
    const securityGroup = new ec2.CfnSecurityGroup(this, 'DatabaseSecurityGroup', {
      groupDescription: 'Meridian Aurora - Data API only, no inbound PostgreSQL', vpcId: props.vpcId,
    });
    const parameters = new rds.CfnDBClusterParameterGroup(this, 'Parameters', {
      description: 'Meridian PostgreSQL encrypted connections', family: 'aurora-postgresql18',
      parameters: { 'rds.force_ssl': '1' },
    });
    const cluster = new rds.CfnDBCluster(this, 'Cluster', {
      dbClusterIdentifier: props.clusterIdentifier,
      engine: 'aurora-postgresql', engineMode: 'provisioned', engineVersion: props.engineVersion,
      storageEncrypted: true, kmsKeyId: 'alias/aws/rds', deletionProtection: true,
      backupRetentionPeriod: 7, copyTagsToSnapshot: true,
      serverlessV2ScalingConfiguration: { minCapacity: 0.5, maxCapacity: 16 },
      enableHttpEndpoint: true, enableCloudwatchLogsExports: ['postgresql'],
      dbSubnetGroupName: subnetGroup.ref, vpcSecurityGroupIds: [securityGroup.attrGroupId],
      dbClusterParameterGroupName: parameters.ref,
      ...(props.snapshotArn ? { snapshotIdentifier: props.snapshotArn } : {
        databaseName: 'meridian', masterUsername: 'meridian_admin', manageMasterUserPassword: true,
      }),
    });
    cluster.applyRemovalPolicy(RemovalPolicy.SNAPSHOT);
    const writer = new rds.CfnDBInstance(this, 'Writer', {
      dbClusterIdentifier: cluster.ref, engine: 'aurora-postgresql', dbInstanceClass: 'db.serverless',
      publiclyAccessible: false, autoMinorVersionUpgrade: true,
    });
    writer.applyRemovalPolicy(RemovalPolicy.RETAIN);
    subnetGroup.applyRemovalPolicy(RemovalPolicy.RETAIN);
    securityGroup.applyRemovalPolicy(RemovalPolicy.RETAIN);
    parameters.applyRemovalPolicy(RemovalPolicy.RETAIN);
    new CfnOutput(this, 'ClusterArn', { value: cluster.attrDbClusterArn });
    new CfnOutput(this, 'ClusterIdentifier', { value: cluster.ref });
    if (!props.snapshotArn) new CfnOutput(this, 'MasterSecretArn', { value: cluster.attrMasterUserSecretSecretArn });
  }
}
