#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { MeridianWebBackendStack } from '../lib/meridian-web-backend-stack';
import { MeridianWebRolesStack } from '../lib/meridian-web-roles-stack';
import { MeridianWebStack, loadServiceEnvironment } from '../lib/meridian-web-stack';

const app = new App();

// Meridian's Aurora cluster, AgentCore resources and the api-token secret all
// live in one region. Pin the stacks to it instead of inheriting the shell's
// default profile region, which may differ.
const region = process.env.MERIDIAN_WEB_REGION ?? 'us-east-1';
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region };
const environment = loadServiceEnvironment(region);

// Three stacks, deployed in this order by scripts/publish.py: the instance
// role (which App Runner needs to exist, and to have propagated, before the
// service), the App Runner backend (retried on its own when App Runner fails
// to deploy), and the site behind CloudFront.
const roles = new MeridianWebRolesStack(app, 'MeridianWebRoles', {
  env,
  environment,
  description: 'Meridian travel concierge: the App Runner instance role, deployed ahead of the service',
});

const backend = new MeridianWebBackendStack(app, 'MeridianWebBackend', {
  env,
  environment,
  instanceRole: roles.instanceRole,
  description: 'Meridian travel concierge: FastAPI backend on App Runner',
});
backend.addStackDependency(roles);

const web = new MeridianWebStack(app, 'MeridianWeb', {
  env,
  service: backend.service,
  description: 'Meridian travel concierge: Vite build on S3 behind CloudFront, routing the API to App Runner',
});
web.addStackDependency(backend);
