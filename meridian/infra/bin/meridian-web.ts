#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { MeridianWebBackendStack } from '../lib/meridian-web-backend-stack';
import { MeridianWebRolesStack } from '../lib/meridian-web-roles-stack';
import { MeridianWebServiceStack } from '../lib/meridian-web-service-stack';
import {
  MeridianWebStack,
  backendHost,
  cognitoHostFromEnv,
  identityMode,
  loadDotenv,
  serviceEnvironment,
} from '../lib/meridian-web-stack';
import { rolesStackWiring } from '../lib/meridian-web-wiring';

const app = new App();

function required(scope: App, key: string): string {
  const value = scope.node.tryGetContext(key);
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`Required context: -c ${key}=...`);
  }
  return value.trim();
}

// Meridian's Aurora cluster, AgentCore resources and the api-token secret all
// live in one region. Pin the stacks to it instead of inheriting the shell's
// default profile region, which may differ.
const region = process.env.MERIDIAN_WEB_REGION ?? 'us-east-1';
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region };
const dotenv = loadDotenv();
const mode = identityMode(dotenv);
const environment = serviceEnvironment(dotenv, region, mode);

// scripts/publish.py deploys these in order: the App Runner roles (which must
// exist, and have propagated, before the service), the backend image, then the
// App Runner service through the SDK, and finally the site behind CloudFront,
// which needs the service host.
new MeridianWebRolesStack(app, 'MeridianWebRoles', {
  env,
  environment,
  ...rolesStackWiring(dotenv, mode, process.env),
  description: 'Meridian travel concierge: the App Runner roles, deployed ahead of the service',
});

new MeridianWebBackendStack(app, 'MeridianWebBackend', {
  env,
  environment,
  description: 'Meridian travel concierge: the FastAPI backend image for App Runner',
});

// The App Runner service stack exists only when the role ARNs are given (-c instanceRoleArn=...
// -c accessRoleArn=...), so a publish that does not manage the service is unchanged. The image
// is always explicit (-c serviceImageUri=...): the live image for the import, never a default.
const instanceRoleArn = app.node.tryGetContext('instanceRoleArn');
if (instanceRoleArn) {
  new MeridianWebServiceStack(app, 'MeridianWebService', {
    env,
    environment,
    imageUri: required(app, 'serviceImageUri'),
    instanceRoleArn,
    accessRoleArn: required(app, 'accessRoleArn'),
    apiTokenSecretArn: mode === 'iam' ? required(app, 'apiTokenSecretArn') : undefined,
    description: 'Meridian travel concierge: the App Runner service, imported and retained',
  });
}

if (process.env.MERIDIAN_BACKEND_HOST) {
  new MeridianWebStack(app, 'MeridianWeb', {
    env,
    backendHost: backendHost(),
    cognitoHost: cognitoHostFromEnv(process.env.MERIDIAN_COGNITO_HOSTED_UI_DOMAIN),
    identityMode: mode,
    description:
      'Meridian travel concierge: Vite build on S3 behind CloudFront, ' +
      'routing the API to App Runner',
  });
}
