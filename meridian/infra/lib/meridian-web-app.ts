import { type App } from 'aws-cdk-lib';
import { MeridianWebBackendStack } from './meridian-web-backend-stack';
import { MeridianWebExpressRolesStack } from './meridian-web-express-roles-stack';
import { MeridianWebExpressStack } from './meridian-web-express-stack';
import { MeridianWebRolesStack } from './meridian-web-roles-stack';
import { MeridianWebServiceStack } from './meridian-web-service-stack';
import {
  MeridianWebStack,
  cognitoHostFromEnv,
  identityMode,
  serviceEnvironment,
} from './meridian-web-stack';
import { rolesStackWiring } from './meridian-web-wiring';

/** What the app reads from outside: meridian/.env and the process environment. */
export interface WebAppInputs {
  dotenv: Record<string, string>;
  processEnv: Record<string, string | undefined>;
}

/** The context value that selects the Amazon ECS Express Mode backend. */
export const EXPRESS_HOST = 'express';

function required(app: App, key: string): string {
  const value = app.node.tryGetContext(key);
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`Required context: -c ${key}=...`);
  }
  return value.trim();
}

/**
 * Which backend host the app is built for: `undefined` is the App Runner path, `express` the
 * Amazon ECS Express Mode path. Anything else is refused so a typo cannot fall back silently.
 */
function selectedBackendHost(app: App): typeof EXPRESS_HOST | undefined {
  const value = app.node.tryGetContext('backendHost');
  if (value === undefined || value === '') return undefined;
  if (value === EXPRESS_HOST) return EXPRESS_HOST;
  throw new Error(`-c backendHost must be '${EXPRESS_HOST}' or absent, not '${String(value)}'`);
}

/**
 * The Meridian hosted web app: the stacks `bin/meridian-web.ts` synthesizes.
 *
 * Without `-c backendHost=express` it defines exactly the App Runner stacks, and their templates
 * do not depend on any Express code. With it, it adds MeridianWebExpressRoles and
 * MeridianWebExpress, and MeridianWeb routes the API to the Express service's endpoint.
 *
 * Meridian's Aurora cluster, AgentCore resources and the api-token secret all live in one Region.
 * The stacks are pinned to it instead of inheriting the shell's default profile Region.
 */
export function defineWebApp(app: App, inputs: WebAppInputs): void {
  const { dotenv, processEnv } = inputs;
  const region = processEnv.MERIDIAN_WEB_REGION ?? 'us-east-1';
  const env = { account: processEnv.CDK_DEFAULT_ACCOUNT, region };
  const mode = identityMode(dotenv, processEnv);
  const environment = serviceEnvironment(dotenv, region, mode);
  const backendHost = selectedBackendHost(app);

  // scripts/publish.py deploys these in order: the App Runner roles (which must
  // exist, and have propagated, before the service), the backend image, then the
  // App Runner service through the SDK, and finally the site behind CloudFront,
  // which needs the service host.
  new MeridianWebRolesStack(app, 'MeridianWebRoles', {
    env,
    environment,
    ...rolesStackWiring(dotenv, mode, processEnv),
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

  const origin = backendHost === EXPRESS_HOST
    ? defineExpressBackend(app, { env, environment, mode, processEnv })
    : processEnv.MERIDIAN_BACKEND_HOST;
  if (origin) {
    new MeridianWebStack(app, 'MeridianWeb', {
      env,
      backendHost: origin,
      cognitoHost: cognitoHostFromEnv(processEnv.MERIDIAN_COGNITO_HOSTED_UI_DOMAIN),
      identityMode: mode,
      description: backendHost === EXPRESS_HOST
        ? 'Meridian travel concierge: Vite build on S3 behind CloudFront, ' +
          'routing the API to Amazon ECS Express Mode'
        : 'Meridian travel concierge: Vite build on S3 behind CloudFront, ' +
          'routing the API to App Runner',
    });
  }
}

interface ExpressBackendInputs {
  env: { account: string | undefined; region: string };
  environment: Record<string, string>;
  mode: 'iam' | 'jwt';
  processEnv: Record<string, string | undefined>;
}

/**
 * The Express roles and service stacks; returns the service host the CloudFront origin uses.
 *
 * @throws Error when `MERIDIAN_BACKEND_HOST` is also set: it names an App Runner host, and two
 *   origins for one distribution would leave it unclear which one the site routes to.
 */
function defineExpressBackend(app: App, inputs: ExpressBackendInputs): string {
  const { env, environment, mode, processEnv } = inputs;
  if (processEnv.MERIDIAN_BACKEND_HOST) {
    throw new Error(
      '-c backendHost=express takes the CloudFront origin from the Express service; unset ' +
        'MERIDIAN_BACKEND_HOST, which names an App Runner host',
    );
  }
  const apiTokenSecretArn = mode === 'iam' ? required(app, 'apiTokenSecretArn') : undefined;
  const roles = new MeridianWebExpressRolesStack(app, 'MeridianWebExpressRoles', {
    env,
    environment,
    apiTokenSecretArn,
    description: 'Meridian travel concierge: the Amazon ECS Express Mode roles, deployed ahead of the service',
  });
  const express = new MeridianWebExpressStack(app, 'MeridianWebExpress', {
    env,
    environment,
    taskRole: roles.taskRole,
    executionRole: roles.executionRole,
    infrastructureRole: roles.infrastructureRole,
    apiTokenSecretArn,
    description: 'Meridian travel concierge: the FastAPI backend on Amazon ECS Express Mode',
  });
  return express.endpoint;
}
