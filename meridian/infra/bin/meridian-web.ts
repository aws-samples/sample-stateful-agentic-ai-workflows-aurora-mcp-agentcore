#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { MeridianWebStack } from '../lib/meridian-web-stack';

const app = new App();

// Meridian's Aurora cluster, AgentCore resources and the api-token secret all
// live in one region. Pin the stack to it instead of inheriting the shell's
// default profile region, which may differ.
const region = process.env.MERIDIAN_WEB_REGION ?? 'us-east-1';

new MeridianWebStack(app, 'MeridianWeb', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region,
  },
  description: 'Meridian travel concierge: Vite build on S3 behind CloudFront, FastAPI backend on App Runner',
});
