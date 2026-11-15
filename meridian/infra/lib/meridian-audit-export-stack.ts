import * as path from 'node:path';
import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  type StackProps,
  aws_cloudwatch as cloudwatch,
  aws_cloudwatch_actions as cloudwatchActions,
  aws_iam as iam,
  aws_kms as kms,
  aws_lambda as lambda,
  aws_logs as logs,
  aws_s3 as s3,
  aws_scheduler as scheduler,
  aws_sns as sns,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';

/** The managed policy scripts/provision_service_logins.py creates for the meridian_audit_export login. */
export const AUDIT_EXPORT_LOGIN_POLICY_NAME = 'MeridianAuditExportAuroraAccess';
/** The key prefix infra/functions/audit_export/audit_export.py writes under; a test keeps them equal. */
export const AUDIT_EXPORT_KEY_PREFIX = 'audit-export/v1';
export const METRIC_NAMESPACE = 'Meridian/AuditExport';
/** Export lag alarm: the oldest row left behind by a full batch is older than this. */
export const BACKLOG_ALARM_SECONDS = 1800;
/** No completed run for this many schedule periods raises the heartbeat alarm. */
export const HEARTBEAT_MISSED_PERIODS = 3;

export type RetentionMode = 'GOVERNANCE' | 'COMPLIANCE';

export interface MeridianAuditExportStackProps extends StackProps {
  clusterArn: string;
  /** The meridian_audit_export login's secret: SELECT on the audit tables and nothing else. */
  auditExportSecretArn: string;
  database: string;
  /**
   * GOVERNANCE (default) lets a principal with s3:BypassGovernanceRetention shorten retention, and
   * the bucket policy denies that permission to everyone. COMPLIANCE lets nobody, root included,
   * delete or shorten before the date, and cannot be undone: choose it deliberately.
   */
  retentionMode?: RetentionMode;
  /** Days each object stays locked. Default 30. */
  retentionDays?: number;
  /** Minutes between runs. Default 5. */
  scheduleMinutes?: number;
  /**
   * Seconds a row must age before it is exported, which must exceed the longest transaction that
   * writes audit rows. Default 180.
   */
  settleSeconds?: number;
  /**
   * Days the exporter's log is kept, a CloudWatch Logs retention value. Default 365. The log
   * carries the chain-head anchors, so the group is retained when the stack is deleted.
   */
  logRetentionDays?: number;
  /** An SNS topic that receives the alarms. Without one the alarms only change state. */
  alarmTopicArn?: string;
}

function requireInteger(name: string, value: number, min: number, max: number): number {
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${name} must be a whole number from ${min} to ${max}`);
  }
  return value;
}

function scheduleExpression(minutes: number): string {
  return minutes === 1 ? 'rate(1 minute)' : `rate(${minutes} minutes)`;
}

/**
 * The tamper-evident audit export: an Object Lock bucket, the Lambda that fills it, the schedule
 * that runs the Lambda, and the alarms that say when it stops.
 *
 * The Lambda reads Aurora as the meridian_audit_export login and can write to the bucket and read
 * it back, nothing more. It cannot delete or change an object, and the bucket policy denies delete
 * to every principal, so removing evidence takes a bucket policy change, which CloudTrail records.
 */
export class MeridianAuditExportStack extends Stack {
  readonly bucket: s3.Bucket;
  readonly key: kms.Key;
  readonly exporter: lambda.Function;

  constructor(scope: Construct, id: string, props: MeridianAuditExportStackProps) {
    super(scope, id, { ...props, terminationProtection: true });
    const mode = props.retentionMode ?? 'GOVERNANCE';
    if (mode !== 'GOVERNANCE' && mode !== 'COMPLIANCE') {
      throw new Error('retentionMode must be GOVERNANCE or COMPLIANCE');
    }
    const retentionDays = requireInteger('retentionDays', props.retentionDays ?? 30, 1, 36500);
    const scheduleMinutes = requireInteger('scheduleMinutes', props.scheduleMinutes ?? 5, 1, 60);
    const settleSeconds = requireInteger('settleSeconds', props.settleSeconds ?? 180, 0, 3600);
    const logRetentionDays = props.logRetentionDays ?? 365;
    if (!(Object.values(logs.RetentionDays) as unknown[]).includes(logRetentionDays)) {
      throw new Error('logRetentionDays must be a CloudWatch Logs retention value such as 365');
    }

    this.key = new kms.Key(this, 'ExportKey', {
      description: 'Meridian audit export objects',
      enableKeyRotation: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const retention = Duration.days(retentionDays);
    this.bucket = new s3.Bucket(this, 'ExportBucket', {
      objectLockEnabled: true,
      objectLockDefaultRetention: mode === 'COMPLIANCE'
        ? s3.ObjectLockRetention.compliance(retention)
        : s3.ObjectLockRetention.governance(retention),
      versioned: true,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: this.key,
      bucketKeyEnabled: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    this.denyDeletes(mode);
    this.denyUnconditionalWrites();

    this.exporter = this.createExporter(props, settleSeconds, logRetentionDays);
    this.grantExporter();
    this.createSchedule(scheduleMinutes);
    this.createAlarms(scheduleMinutes, props.alarmTopicArn);

    new CfnOutput(this, 'BucketName', { value: this.bucket.bucketName });
    new CfnOutput(this, 'KeyArn', { value: this.key.keyArn });
    new CfnOutput(this, 'FunctionName', { value: this.exporter.functionName });
  }

  private denyDeletes(mode: RetentionMode): void {
    const actions = ['s3:DeleteObject', 's3:DeleteObjectVersion'];
    if (mode === 'GOVERNANCE') actions.push('s3:BypassGovernanceRetention');
    this.bucket.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'DenyDeleteAndBypass',
      effect: iam.Effect.DENY,
      principals: [new iam.AnyPrincipal()],
      actions,
      resources: [this.bucket.arnForObjects('*')],
    }));
  }

  /** Every object write must carry If-None-Match, so no principal can overwrite a key. */
  private denyUnconditionalWrites(): void {
    this.bucket.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'DenyWriteWithoutIfNoneMatch',
      effect: iam.Effect.DENY,
      principals: [new iam.AnyPrincipal()],
      actions: ['s3:PutObject'],
      resources: [this.bucket.arnForObjects('*')],
      conditions: { Null: { 's3:if-none-match': 'true' } },
    }));
  }

  private createExporter(
    props: MeridianAuditExportStackProps,
    settleSeconds: number,
    logRetentionDays: number,
  ): lambda.Function {
    const fn = new lambda.Function(this, 'Exporter', {
      description: 'Copies new audit rows from Aurora to the Object Lock bucket as a hash chain',
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'audit_export.lambda_handler',
      code: lambda.Code.fromAsset(
        path.resolve(__dirname, '..', '..', 'functions', 'audit_export'),
        { exclude: ['__pycache__', '*.pyc', '*.pyo', '.DS_Store'] },
      ),
      logGroup: new logs.LogGroup(this, 'ExporterLogs', {
        retention: logRetentionDays as logs.RetentionDays,
        removalPolicy: RemovalPolicy.RETAIN,
      }),
      timeout: Duration.seconds(120),
      memorySize: 256,
      environment: {
        AURORA_CLUSTER_ARN: props.clusterArn,
        AURORA_SECRET_ARN: props.auditExportSecretArn,
        AURORA_DATABASE: props.database,
        AUDIT_EXPORT_BUCKET: this.bucket.bucketName,
        AUDIT_EXPORT_SETTLE_SECONDS: String(settleSeconds),
      },
    });
    fn.role!.addManagedPolicy(
      iam.ManagedPolicy.fromManagedPolicyArn(
        this, 'AuditExportLoginAccess',
        `arn:aws:iam::${this.account}:policy/${AUDIT_EXPORT_LOGIN_POLICY_NAME}`,
      ),
    );
    return fn;
  }

  /** The Data API and the login secret come from the managed policy; this adds the bucket and key. */
  private grantExporter(): void {
    const exportObjects = this.bucket.arnForObjects(`${AUDIT_EXPORT_KEY_PREFIX}/*`);
    this.exporter.addToRolePolicy(new iam.PolicyStatement({
      sid: 'WriteAndReadExportObjects',
      actions: ['s3:PutObject', 's3:GetObject'],
      resources: [exportObjects],
    }));
    this.exporter.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ListExportObjects',
      actions: ['s3:ListBucket'],
      resources: [this.bucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': [`${AUDIT_EXPORT_KEY_PREFIX}/*`] } },
    }));
    this.exporter.addToRolePolicy(new iam.PolicyStatement({
      sid: 'UseExportKey',
      actions: ['kms:GenerateDataKey', 'kms:Decrypt'],
      resources: [this.key.keyArn],
    }));
  }

  private createSchedule(scheduleMinutes: number): void {
    const role = new iam.Role(this, 'ScheduleRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
    });
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['lambda:InvokeFunction'],
      resources: [this.exporter.functionArn],
    }));
    new scheduler.CfnSchedule(this, 'Schedule', {
      description: 'Runs the Meridian audit export',
      scheduleExpression: scheduleExpression(scheduleMinutes),
      flexibleTimeWindow: { mode: 'OFF' },
      state: 'ENABLED',
      target: {
        arn: this.exporter.functionArn,
        roleArn: role.roleArn,
        retryPolicy: { maximumRetryAttempts: 2, maximumEventAgeInSeconds: 600 },
      },
    });
  }

  private createAlarms(scheduleMinutes: number, alarmTopicArn?: string): void {
    const period = Duration.minutes(scheduleMinutes);
    const alarms = [
      new cloudwatch.Alarm(this, 'ExportErrors', {
        alarmDescription: 'The audit export Lambda failed. Rows wait in Aurora until a run succeeds.',
        metric: this.exporter.metricErrors({ period, statistic: 'Sum' }),
        threshold: 1,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }),
      new cloudwatch.Alarm(this, 'ExportHeartbeatMissing', {
        alarmDescription: 'No audit export run completed for several schedule periods: the schedule, the Lambda or Aurora access is down.',
        metric: new cloudwatch.Metric({
          namespace: METRIC_NAMESPACE, metricName: 'RunsCompleted', statistic: 'Sum', period,
        }),
        threshold: 1,
        evaluationPeriods: HEARTBEAT_MISSED_PERIODS,
        datapointsToAlarm: HEARTBEAT_MISSED_PERIODS,
        comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.BREACHING,
      }),
      new cloudwatch.Alarm(this, 'ExportLag', {
        alarmDescription: 'The export is behind: a full batch left rows older than the threshold in Aurora.',
        metric: new cloudwatch.Metric({
          namespace: METRIC_NAMESPACE, metricName: 'BacklogAgeSeconds', statistic: 'Maximum', period,
        }),
        threshold: BACKLOG_ALARM_SECONDS,
        evaluationPeriods: 2,
        datapointsToAlarm: 2,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }),
    ];
    if (alarmTopicArn) {
      const action = new cloudwatchActions.SnsAction(
        sns.Topic.fromTopicArn(this, 'AlarmTopic', alarmTopicArn),
      );
      alarms.forEach((alarm) => alarm.addAlarmAction(action));
    }
  }
}
