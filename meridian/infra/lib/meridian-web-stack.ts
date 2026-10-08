import * as fs from 'node:fs';
import * as path from 'node:path';
import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  type StackProps,
  aws_cloudfront as cloudfront,
  aws_cloudfront_origins as origins,
  aws_s3 as s3,
  aws_s3_deployment as s3deploy,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';

/** The existing origin token secret referenced by the established publisher. */
export const API_TOKEN_SECRET_NAME = 'meridian/web/api-token';

const COGNITO_HOSTED_UI_DOMAIN =
  /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.auth\.[a-z]{2}(?:-[a-z]+)+-\d\.amazoncognito\.com$/;

/**
 * The production bundle serves its scripts and fonts from the same origin.
 * React and Motion set inline styles; catalog photography may use HTTPS URLs.
 *
 * A signed-in build also calls the Cognito hosted UI domain from the browser (the token exchange,
 * the refresh and the revoke), so that one host joins `connect-src`. It must be a bare hosted UI
 * domain: a path, a scheme, a wildcard or anything after the host would widen the policy.
 */
export function contentSecurityPolicy(cognitoHost?: string): string {
  const connect = ["'self'"];
  if (cognitoHost) {
    if (!COGNITO_HOSTED_UI_DOMAIN.test(cognitoHost)) {
      throw new Error(
        `cognitoHost must be a bare Cognito hosted UI domain such as ` +
          `meridian-x.auth.us-east-1.amazoncognito.com, not ${JSON.stringify(cognitoHost)}`,
      );
    }
    connect.push(`https://${cognitoHost}`);
  }
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https:",
    "font-src 'self'",
    `connect-src ${connect.join(' ')}`,
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
  ].join('; ');
}

/** The hosted UI domain `bin/meridian-web.ts` forwards: trimmed, and absent when blank. */
export function cognitoHostFromEnv(value: string | undefined): string | undefined {
  return value?.trim() || undefined;
}

/** The policy a build without sign-in ships. */
export const CONTENT_SECURITY_POLICY = contentSecurityPolicy();

/** Non-secret settings copied from meridian/.env into the App Runner service. */
const ENV_PASSTHROUGH = [
  'AURORA_CLUSTER_ARN',
  'AURORA_SECRET_ARN',
  'AURORA_BACKEND_SECRET_ARN',
  'AURORA_DATABASE',
  'AURORA_CLUSTER_IDENTIFIER',
  'BEDROCK_MODEL_ID',
  'BEDROCK_REGION',
  'EMBEDDING_MODEL',
  'EMBEDDING_DIMENSION',
  'RERANK_MODEL',
  'AGENTCORE_REGION',
  'AGENTCORE_RUNTIME_ARN',
  'AGENTCORE_WORKFLOW_RUNTIME_ARN',
  'AGENTCORE_GATEWAY_URL',
  'AGENTCORE_GATEWAY_SEARCH_TOOL',
  'AGENTCORE_MEMORY_ID',
  'MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS',
  'STRANDS_ORCHESTRATION',
] as const;

const REQUIRED_ENV = ['AURORA_CLUSTER_ARN', 'AURORA_SECRET_ARN', 'AURORA_DATABASE', 'AGENTCORE_RUNTIME_ARN',
  'AGENTCORE_WORKFLOW_RUNTIME_ARN', 'AGENTCORE_GATEWAY_URL'];

/** Read KEY=value lines from meridian/.env without a dotenv dependency. */
export function readDotenv(file: string): Record<string, string> {
  if (!fs.existsSync(file)) return {};
  const values: Record<string, string> = {};
  for (const raw of fs.readFileSync(file, 'utf8').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

/** Which credential every AgentCore hop expects: AWS signatures (`iam`) or the Cognito access token (`jwt`). */
export type IdentityMode = 'iam' | 'jwt';

const COGNITO_ENV = [
  'MERIDIAN_COGNITO_REGION',
  'MERIDIAN_COGNITO_USER_POOL_ID',
  'MERIDIAN_COGNITO_APP_CLIENT_ID',
] as const;

/** The mode: the process environment wins over meridian/.env, as scripts/publish.py reads it. */
export function identityMode(
  dotenv: Record<string, string>,
  processEnv: Record<string, string | undefined> = process.env,
): IdentityMode {
  const raw = (processEnv.MERIDIAN_AGENTCORE_AUTH ?? dotenv.MERIDIAN_AGENTCORE_AUTH ?? '').trim().toLowerCase();
  if (raw === '') return 'iam';
  if (raw === 'iam' || raw === 'jwt') return raw;
  throw new Error(`MERIDIAN_AGENTCORE_AUTH must be 'iam' or 'jwt', not '${raw}'; unset it to keep IAM`);
}

function jwtServiceEnvironment(dotenv: Record<string, string>): Record<string, string> {
  const missing = [...COGNITO_ENV, 'AURORA_BACKEND_SECRET_ARN'].filter((key) => !dotenv[key]);
  if (missing.length) {
    throw new Error(
      `meridian/.env is missing ${missing.join(', ')}; run scripts/sync_cognito_env.py --write and ` +
        'scripts/provision_service_logins.py --login backend --apply --write-env first',
    );
  }
  if (dotenv.AURORA_BACKEND_SECRET_ARN === dotenv.AURORA_SECRET_ARN) {
    throw new Error(
      'AURORA_BACKEND_SECRET_ARN must differ from AURORA_SECRET_ARN: the hosted backend runs as the ' +
        'meridian_backend login, not the master',
    );
  }
  return {
    MERIDIAN_AGENTCORE_AUTH: 'jwt',
    MERIDIAN_COGNITO_REGION: dotenv.MERIDIAN_COGNITO_REGION,
    MERIDIAN_COGNITO_USER_POOL_ID: dotenv.MERIDIAN_COGNITO_USER_POOL_ID,
    MERIDIAN_COGNITO_APP_CLIENT_ID: dotenv.MERIDIAN_COGNITO_APP_CLIENT_ID,
    AURORA_SECRET_ARN: dotenv.AURORA_BACKEND_SECRET_ARN,
  };
}

/**
 * The App Runner environment. In `jwt` mode the service also gets the pool settings and the mode,
 * and its database login becomes the meridian_backend secret (the container's AURORA_SECRET_ARN).
 */
export function serviceEnvironment(
  dotenv: Record<string, string>,
  region: string,
  mode: IdentityMode = 'iam',
): Record<string, string> {
  const missing = REQUIRED_ENV.filter((key) => !dotenv[key]);
  if (missing.length) {
    throw new Error(`meridian/.env is missing ${missing.join(', ')}; run agentcore deploy and sync_agentcore_env.py first`);
  }
  const env: Record<string, string> = {
    AWS_DEFAULT_REGION: region,
    AWS_REGION: region,
    ENVIRONMENT: 'production',
    LOG_LEVEL: dotenv.LOG_LEVEL ?? 'INFO',
    LOG_AGENT_VERBOSE: 'false',
    AGENTCORE_SKIP_CLI_SYNC: '1',
    MCP_CONNECTION_METHOD: 'rdsapi',
    MCP_DATABASE_TYPE: 'APG',
  };
  for (const key of ENV_PASSTHROUGH) {
    if (dotenv[key]) env[key] = dotenv[key];
  }
  return mode === 'jwt' ? { ...env, ...jwtServiceEnvironment(dotenv) } : env;
}

// Compiled to infra/dist/lib, so three levels up is meridian/.
const meridianDir = path.resolve(__dirname, '..', '..', '..');

/** meridian/.env as a map. */
export function loadDotenv(): Record<string, string> {
  return readDotenv(path.join(meridianDir, '.env'));
}

/** The App Runner service host the distribution routes the API to, e.g. abc.us-east-1.awsapprunner.com. */
export function backendHost(): string {
  const host = process.env.MERIDIAN_BACKEND_HOST;
  if (!host) {
    throw new Error('MERIDIAN_BACKEND_HOST is not set; run scripts/publish.py with the existing App Runner service ARN');
  }
  return host;
}

export interface MeridianWebStackProps extends StackProps {
  /** The App Runner service host from backendHost(). */
  backendHost: string;
  /** The Cognito hosted UI domain a signed-in build calls; omit for a build without sign-in. */
  cognitoHost?: string;
  /**
   * `iam` (default) deploys the established viewer function: Basic at the edge and the shared token
   * swapped into API calls. `jwt` deploys the one that passes the browser's own token through.
   */
  identityMode?: IdentityMode;
}

/** The site: the Vite build in S3, CloudFront with the viewer function, and the KeyValueStore. */
export class MeridianWebStack extends Stack {
  constructor(scope: Construct, id: string, props: MeridianWebStackProps) {
    super(scope, id, props);
    const { backendHost: apiHost, cognitoHost, identityMode: mode = 'iam' } = props;

    const site = new s3.Bucket(this, 'Site', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const access = new cloudfront.KeyValueStore(this, 'Access', {
      keyValueStoreName: 'meridian-web-access',
      comment: 'Established presenter access and backend origin token',
    });
    const jwt = mode === 'jwt';
    const viewer = new cloudfront.Function(this, 'Viewer', {
      functionName: 'meridian-web-viewer',
      comment: jwt
        ? 'Meridian edge: the browser token reaches the API, SPA route rewrite'
        : 'Meridian edge auth, bearer injection for the API, and SPA route rewrite',
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      keyValueStore: access,
      code: cloudfront.FunctionCode.fromFile({
        filePath: path.join(
          __dirname, '..', '..', 'functions', jwt ? 'viewer-request-jwt.js' : 'viewer-request.js',
        ),
      }),
    });

    const responseHeaders = new cloudfront.ResponseHeadersPolicy(this, 'ResponseHeaders', {
      securityHeadersBehavior: {
        contentSecurityPolicy: {
          contentSecurityPolicy: contentSecurityPolicy(cognitoHost),
          override: true,
        },
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        referrerPolicy: { referrerPolicy: cloudfront.HeadersReferrerPolicy.NO_REFERRER, override: true },
        strictTransportSecurity: {
          accessControlMaxAge: Duration.days(365),
          override: true,
        },
      },
    });

    const api = new origins.HttpOrigin(apiHost, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
      readTimeout: Duration.seconds(60),
      keepaliveTimeout: Duration.seconds(60),
    });
    const apiBehavior: cloudfront.BehaviorOptions = {
      origin: api,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
      responseHeadersPolicy: responseHeaders,
      functionAssociations: [{ function: viewer, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
    };

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: 'Meridian - stateful agentic travel concierge (Aurora, MCP, AgentCore)',
      defaultRootObject: 'index.html',
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(site),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: responseHeaders,
        functionAssociations: [{ function: viewer, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
      },
      additionalBehaviors: {
        '/api/*': apiBehavior,
        '/health': apiBehavior,
      },
    });

    new s3deploy.BucketDeployment(this, 'SiteDeployment', {
      sources: [s3deploy.Source.asset(path.join(meridianDir, 'frontend', 'dist'))],
      destinationBucket: site,
      distribution,
      distributionPaths: ['/*'],
      prune: true,
      memoryLimit: 512,
    });

    new CfnOutput(this, 'SiteUrl', { value: `https://${distribution.distributionDomainName}` });
    new CfnOutput(this, 'DistributionId', { value: distribution.distributionId });
    new CfnOutput(this, 'AccessStoreArn', { value: access.keyValueStoreArn });
    new CfnOutput(this, 'ApiTokenSecretName', { value: API_TOKEN_SECRET_NAME });
  }
}
