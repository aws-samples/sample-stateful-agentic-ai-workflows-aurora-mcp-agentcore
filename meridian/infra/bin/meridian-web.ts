#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { MeridianWebBackendStack } from '../lib/meridian-web-backend-stack';
import { MeridianWebRolesStack } from '../lib/meridian-web-roles-stack';
import { MeridianWebStack, backendHost, loadServiceEnvironment } from '../lib/meridian-web-stack';

const app = new App();

// Meridian's Aurora cluster, AgentCore resources and the api-token secret all
// live in one region. Pin the stacks to it instead of inheriting the shell's
// default profile region, which may differ.
const region = process.env.MERIDIAN_WEB_REGION ?? 'us-east-1';
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region };
const environment = loadServiceEnvironment(region);

// scripts/publish.py deploys these in order: the App Runner roles (which must
// exist, and have propagated, before the service), the backend image, then the
// App Runner service through the SDK, and finally the site behind CloudFront,
// which needs the service host.
new MeridianWebRolesStack(app, 'MeridianWebRoles', {
  env,
  environment,
  description: 'Meridian travel concierge: the App Runner roles, deployed ahead of the service',
});

new MeridianWebBackendStack(app, 'MeridianWebBackend', {
  env,
  environment,
  description: 'Meridian travel concierge: the FastAPI backend image for App Runner',
});

if (process.env.MERIDIAN_BACKEND_HOST) {
  new MeridianWebStack(app, 'MeridianWeb', {
    env,
    backendHost: backendHost(),
    description: 'Meridian travel concierge: Vite build on S3 behind CloudFront, routing the API to App Runner',
  });
}
