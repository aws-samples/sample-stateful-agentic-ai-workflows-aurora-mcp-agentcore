#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { MeridianAuroraStack } from '../lib/meridian-aurora-stack';

const app = new App();
function required(key: string): string {
  const value = app.node.tryGetContext(key);
  if (typeof value !== 'string' || !value.trim()) throw new Error(`Required context: -c ${key}=...`);
  return value;
}
const account = required('account');
if (!/^\d{12}$/.test(account)) throw new Error('Explicit 12-digit target account required');
const clusterIdentifier = required('clusterIdentifier');
new MeridianAuroraStack(app, 'MeridianAurora', {
  env: { account, region: required('region') },
  clusterIdentifier, vpcId: required('vpcId'), subnetIds: required('subnetIds').split(','),
  engineVersion: required('engineVersion'), snapshotArn: app.node.tryGetContext('snapshotArn'),
  description: 'Meridian encrypted Aurora - separate creation or restore, no automatic cutover',
});
