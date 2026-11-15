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
  /**
   * The failover reader's zone. Choose one the writer is not in: read the writer's zone with
   * describe-db-instances. The writer itself takes no zone property, because adding one to an
   * existing writer would make CloudFormation replace it.
   */
  readerAvailabilityZone: string;
  /** Restore creates a separate cluster; it never redirects application traffic. */
  snapshotArn?: string;
}

/**
 * Isolated Aurora resources. No plaintext credential ever enters CDK. Deleting the stack takes a
 * final cluster snapshot and deletes the writer with the cluster, so nothing keeps billing.
 */
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
      // A minimum of 1 ACU lifts the 2,000 max_connections cap that 0 or 0.5 imposes.
      serverlessV2ScalingConfiguration: { minCapacity: 1, maxCapacity: 16 },
      enableHttpEndpoint: true, enableCloudwatchLogsExports: ['postgresql'],
      dbSubnetGroupName: subnetGroup.ref, vpcSecurityGroupIds: [securityGroup.attrGroupId],
      dbClusterParameterGroupName: parameters.ref,
      ...(props.snapshotArn ? { snapshotIdentifier: props.snapshotArn } : {
        databaseName: 'meridian', masterUsername: 'meridian_admin', manageMasterUserPassword: true,
      }),
    });
    cluster.applyRemovalPolicy(RemovalPolicy.SNAPSHOT);
    // The engine minor changes only through engineVersion: a maintenance window must not move it.
    const writer = new rds.CfnDBInstance(this, 'Writer', {
      dbClusterIdentifier: cluster.ref, engine: 'aurora-postgresql', dbInstanceClass: 'db.serverless',
      publiclyAccessible: false, autoMinorVersionUpgrade: false,
    });
    writer.applyRemovalPolicy(RemovalPolicy.DESTROY);
    // The writer's properties stay exactly as created: changing the writer resource would make
    // CloudFormation send its properties again. The reader is a failover target in another
    // zone. Tier 0 and 1 readers scale with the writer, so a failover lands on warm capacity.
    // The Data API reaches only the writer, so this reader carries no application reads.
    const reader = new rds.CfnDBInstance(this, 'Reader', {
      dbClusterIdentifier: cluster.ref, engine: 'aurora-postgresql',
      dbInstanceClass: 'db.serverless',
      publiclyAccessible: false, autoMinorVersionUpgrade: false,
      availabilityZone: props.readerAvailabilityZone, promotionTier: 1,
    });
    reader.addDependency(writer);
    reader.applyRemovalPolicy(RemovalPolicy.DESTROY);
    subnetGroup.applyRemovalPolicy(RemovalPolicy.RETAIN);
    securityGroup.applyRemovalPolicy(RemovalPolicy.RETAIN);
    parameters.applyRemovalPolicy(RemovalPolicy.RETAIN);
    new CfnOutput(this, 'ClusterArn', { value: cluster.attrDbClusterArn });
    new CfnOutput(this, 'ClusterIdentifier', { value: cluster.ref });
    if (!props.snapshotArn) new CfnOutput(this, 'MasterSecretArn', { value: cluster.attrMasterUserSecretSecretArn });
  }
}
