#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { MeridianWebRolesStack } from '../lib/meridian-web-roles-stack';
import { MeridianWebStack, loadServiceEnvironment } from '../lib/meridian-web-stack';

const app = new App();

// Meridian's Aurora cluster, AgentCore resources and the api-token secret all
// live in one region. Pin the stacks to it instead of inheriting the shell's
// default profile region, which may differ.
const region = process.env.MERIDIAN_WEB_REGION ?? 'us-east-1';
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region };
const environment = loadServiceEnvironment(region);

// The instance role lives in its own stack so it exists, and has propagated,
// before App Runner deploys the service that assumes it. See the roles stack.
const roles = new MeridianWebRolesStack(app, 'MeridianWebRoles', {
  env,
  environment,
  description: 'Meridian travel concierge: the App Runner instance role, deployed ahead of the service',
});

const web = new MeridianWebStack(app, 'MeridianWeb', {
  env,
  environment,
  instanceRole: roles.instanceRole,
  description: 'Meridian travel concierge: Vite build on S3 behind CloudFront, FastAPI backend on App Runner',
});
web.addStackDependency(roles);
