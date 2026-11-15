#!/usr/bin/env node
import * as path from 'node:path';
import { App } from 'aws-cdk-lib';
import { resolveAuditExportConfig } from '../lib/audit-export-config';
import { MeridianAuditExportStack, type RetentionMode } from '../lib/meridian-audit-export-stack';
import { readDotenv } from '../lib/meridian-web-stack';

const app = new App();

function optionalNumber(key: string): number | undefined {
  const value = app.node.tryGetContext(key);
  return value === undefined ? undefined : Number(value);
}

// Compiled to infra/dist/bin, so three levels up is meridian/.
const dotenv = readDotenv(path.resolve(__dirname, '..', '..', '..', '.env'));
const config = resolveAuditExportConfig(dotenv, process.env.CDK_DEFAULT_ACCOUNT);

new MeridianAuditExportStack(app, 'MeridianAuditExport', {
  env: { account: config.account, region: config.region },
  clusterArn: config.clusterArn,
  auditExportSecretArn: config.auditExportSecretArn,
  database: config.database,
  retentionMode: app.node.tryGetContext('retentionMode') as RetentionMode | undefined,
  retentionDays: optionalNumber('retentionDays'),
  scheduleMinutes: optionalNumber('scheduleMinutes'),
  settleSeconds: optionalNumber('settleSeconds'),
  logRetentionDays: optionalNumber('logRetentionDays'),
  alarmTopicArn: app.node.tryGetContext('alarmTopicArn') ?? config.alarmTopicArn,
  description: 'Meridian tamper-evident audit export: Object Lock bucket, exporter Lambda, schedule and alarms',
});
