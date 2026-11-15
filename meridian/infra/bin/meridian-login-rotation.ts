#!/usr/bin/env node
import * as path from 'node:path';
import { App } from 'aws-cdk-lib';
import {
  parseRotateLogins,
  parseRotationDays,
  resolveLoginRotationConfig,
} from '../lib/login-rotation-config';
import { MeridianLoginRotationStack } from '../lib/meridian-login-rotation-stack';
import { readDotenv } from '../lib/meridian-web-stack';

// Password rotation is off unless `-c rotateLogins=<login>[,<login>...]` is given. Without it this
// app creates no stack, and none of the other apps in this directory refers to rotation.
const app = new App();
const logins = parseRotateLogins(app.node.tryGetContext('rotateLogins'));

if (logins.length === 0) {
  process.stderr.write(
    'Login password rotation is off. Name the logins to rotate with -c rotateLogins=backend ' +
      '(see docs/OPERATIONS.md, "Rotate workload passwords automatically"). No stack was created.\n',
  );
} else {
  // Compiled to infra/dist/bin, so three levels up is meridian/.
  const dotenv = readDotenv(path.resolve(__dirname, '..', '..', '..', '.env'));
  const config = resolveLoginRotationConfig(dotenv, process.env.CDK_DEFAULT_ACCOUNT, logins);
  new MeridianLoginRotationStack(app, 'MeridianLoginRotation', {
    env: { account: config.account, region: config.region },
    clusterArn: config.clusterArn,
    database: config.database,
    rotatorSecretArn: config.rotatorSecretArn,
    logins: config.logins,
    rotationDays: parseRotationDays(app.node.tryGetContext('rotationDays')),
    description: 'Meridian: Secrets Manager rotation (alternating users) for Aurora workload login secrets',
  });
}
