#!/usr/bin/env node
import * as path from 'node:path';
import { App } from 'aws-cdk-lib';
import { resolveIdentityConfig } from '../lib/identity-config';
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
const config = resolveIdentityConfig(dotenv, process.env.CDK_DEFAULT_ACCOUNT);

const callbackUrls = list(required('callbackUrls'));
new MeridianIdentityStack(app, 'MeridianIdentity', {
  env: { account: config.account, region: config.region },
  domainPrefix: required('domainPrefix'),
  callbackUrls,
  logoutUrls: list(app.node.tryGetContext('logoutUrls') ?? callbackUrls.join(',')),
  clusterArn: config.clusterArn,
  identitySecretArn: config.identitySecretArn,
  database: config.database,
  description: 'Meridian travel concierge: Cognito user pool, web app client and the traveler claim trigger',
});
