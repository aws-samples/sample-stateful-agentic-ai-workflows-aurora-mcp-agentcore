import * as fs from 'node:fs';
import * as path from 'node:path';
import * as apprunner from '@aws-cdk/aws-apprunner-alpha';
import {
  CfnOutput,
  Duration,
  IgnoreMode,
  RemovalPolicy,
  Stack,
  type StackProps,
  aws_cloudfront as cloudfront,
  aws_cloudfront_origins as origins,
  aws_ecr_assets as ecrAssets,
  aws_iam as iam,
  aws_s3 as s3,
  aws_s3_deployment as s3deploy,
  aws_secretsmanager as secretsmanager,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';

/** The Secrets Manager name scripts/publish.py writes the API bearer token to. */
export const API_TOKEN_SECRET_NAME = 'meridian/web/api-token';

/** Non-secret settings copied from meridian/.env into the App Runner service. */
const ENV_PASSTHROUGH = [
  'AURORA_CLUSTER_ARN',
  'AURORA_SECRET_ARN',
  'AURORA_DATABASE',
  'AURORA_CLUSTER_IDENTIFIER',
  'BEDROCK_MODEL_ID',
  'BEDROCK_REGION',
  'EMBEDDING_MODEL',
  'EMBEDDING_DIMENSION',
  'RERANK_MODEL',
  'AGENTCORE_REGION',
  'AGENTCORE_RUNTIME_ARN',
  'AGENTCORE_GATEWAY_URL',
  'AGENTCORE_GATEWAY_SEARCH_TOOL',
  'AGENTCORE_MEMORY_ID',
  'MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS',
  'STRANDS_ORCHESTRATION',
] as const;

const REQUIRED_ENV = ['AURORA_CLUSTER_ARN', 'AURORA_SECRET_ARN', 'AURORA_DATABASE', 'AGENTCORE_RUNTIME_ARN'];

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

export function serviceEnvironment(dotenv: Record<string, string>, region: string): Record<string, string> {
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
    LANGGRAPH_CHECKPOINT_DATA_API: 'true',
    LANGGRAPH_CHECKPOINT_REQUIRED: 'true',
    MCP_CONNECTION_METHOD: 'rdsapi',
    MCP_DATABASE_TYPE: 'APG',
  };
  for (const key of ENV_PASSTHROUGH) {
    if (dotenv[key]) env[key] = dotenv[key];
  }
  return env;
}

// Compiled to infra/dist/lib, so three levels up is meridian/.
const meridianDir = path.resolve(__dirname, '..', '..', '..');

/** The App Runner environment for this region, read from meridian/.env. */
export function loadServiceEnvironment(region: string): Record<string, string> {
  return serviceEnvironment(readDotenv(path.join(meridianDir, '.env')), region);
}

export interface MeridianWebStackProps extends StackProps {
  /** From loadServiceEnvironment(); shared with the roles stack. */
  environment: Record<string, string>;
  /** The App Runner instance role from MeridianWebRolesStack, deployed and propagated first. */
  instanceRole: iam.IRole;
}

export class MeridianWebStack extends Stack {
  constructor(scope: Construct, id: string, props: MeridianWebStackProps) {
    super(scope, id, props);
    const { environment, instanceRole } = props;

    // App Runner needs the complete secret ARN (with its suffix) to read the token at
    // deployment; scripts/publish.py creates the secret and passes the ARN through.
    const apiTokenArn = process.env.MERIDIAN_API_TOKEN_SECRET_ARN;
    if (!apiTokenArn) {
      throw new Error(`MERIDIAN_API_TOKEN_SECRET_ARN is not set; run scripts/publish.py, which creates ${API_TOKEN_SECRET_NAME}`);
    }
    const apiToken = secretsmanager.Secret.fromSecretCompleteArn(this, 'ApiToken', apiTokenArn);

    const image = new ecrAssets.DockerImageAsset(this, 'BackendImage', {
      directory: meridianDir,
      file: 'Dockerfile',
      platform: ecrAssets.Platform.LINUX_AMD64,
      ignoreMode: IgnoreMode.DOCKER,
    });

    const service = new apprunner.Service(this, 'Backend', {
      serviceName: 'meridian-web',
      source: apprunner.Source.fromAsset({
        asset: image,
        imageConfiguration: {
          port: 8000,
          environmentVariables: environment,
          environmentSecrets: {
            MERIDIAN_API_TOKEN: apprunner.Secret.fromSecretsManager(apiToken),
          },
        },
      }),
      instanceRole,
      cpu: apprunner.Cpu.ONE_VCPU,
      memory: apprunner.Memory.TWO_GB,
      autoDeploymentsEnabled: false,
      // TCP on purpose. Every deployment configured with an HTTP health check on
      // /health at a 10 second interval failed in us-east-1 before App Runner
      // provisioned an instance, with no application log, while the same image
      // and command passed a TCP check. Uvicorn binds the port only after the lifespan startup finishes,
      // and that startup initialises the Aurora checkpoint backend, so an open
      // port already means the backend is ready.
      healthCheck: apprunner.HealthCheck.tcp({
        interval: Duration.seconds(10),
        timeout: Duration.seconds(5),
        healthyThreshold: 1,
        unhealthyThreshold: 5,
      }),
      autoScalingConfiguration: new apprunner.AutoScalingConfiguration(this, 'BackendScaling', {
        autoScalingConfigurationName: 'meridian-web',
        minSize: 1,
        maxSize: 2,
        maxConcurrency: 25,
      }),
    });
    apiToken.grantRead(instanceRole);

    const site = new s3.Bucket(this, 'Site', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const access = new cloudfront.KeyValueStore(this, 'Access', {
      keyValueStoreName: 'meridian-web-access',
      comment: 'Basic auth credential and the backend bearer token, written by scripts/publish.py',
    });
    const viewer = new cloudfront.Function(this, 'Viewer', {
      functionName: 'meridian-web-viewer',
      comment: 'Meridian edge auth, bearer injection for the API, and SPA route rewrite',
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      keyValueStore: access,
      code: cloudfront.FunctionCode.fromFile({ filePath: path.join(__dirname, '..', '..', 'functions', 'viewer-request.js') }),
    });

    const api = new origins.HttpOrigin(service.serviceUrl, {
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
    new CfnOutput(this, 'BackendUrl', { value: `https://${service.serviceUrl}` });
    new CfnOutput(this, 'BackendServiceArn', { value: service.serviceArn });
    new CfnOutput(this, 'AccessStoreArn', { value: access.keyValueStoreArn });
    new CfnOutput(this, 'ApiTokenSecretName', { value: API_TOKEN_SECRET_NAME });
  }
}
