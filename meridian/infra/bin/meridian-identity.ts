#!/usr/bin/env node
import * as path from 'node:path';
import { App } from 'aws-cdk-lib';
import { MeridianIdentityStack } from '../lib/meridian-identity-stack';
import { readDotenv } from '../lib/meridian-web-stack';

const app = new App();
function required(key: string): string {
  const value = app.node.tryGetContext(key);
  if (typeof value !== 'string' || !value.trim()) throw new Error(`Required context: -c ${key}=...`);
  return value.trim();
}
const list = (value: string): string[] => value.split(',').map((item) => item.trim()).filter(Boolean);

// Compiled to infra/dist/bin, so three levels up is meridian/.
const dotenv = readDotenv(path.resolve(__dirname, '..', '..', '..', '.env'));
const clusterArn = dotenv.AURORA_CLUSTER_ARN ?? '';
const identitySecretArn = dotenv.AURORA_IDENTITY_SECRET_ARN ?? '';
const parts = clusterArn.split(':');
if (parts.length < 7 || !/^\d{12}$/.test(parts[4])) {
  throw new Error('meridian/.env needs AURORA_CLUSTER_ARN');
}
if (!identitySecretArn) {
  throw new Error('meridian/.env needs AURORA_IDENTITY_SECRET_ARN; run scripts/provision_service_logins.py --login identity --apply --write-env');
}
const [, , , region, account] = parts;
if (process.env.CDK_DEFAULT_ACCOUNT && process.env.CDK_DEFAULT_ACCOUNT !== account) {
  throw new Error('The AWS credentials are for a different account than AURORA_CLUSTER_ARN; check AWS_PROFILE');
}

const callbackUrls = list(required('callbackUrls'));
new MeridianIdentityStack(app, 'MeridianIdentity', {
  env: { account, region },
  domainPrefix: required('domainPrefix'),
  callbackUrls,
  logoutUrls: list(app.node.tryGetContext('logoutUrls') ?? callbackUrls.join(',')),
  clusterArn,
  identitySecretArn,
  database: dotenv.AURORA_DATABASE ?? 'meridian',
  description: 'Meridian travel concierge: Cognito user pool, web app client and the traveler claim trigger',
});
