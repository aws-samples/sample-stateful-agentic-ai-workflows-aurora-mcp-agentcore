import * as path from 'node:path';
import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  type StackProps,
  aws_cognito as cognito,
  aws_iam as iam,
  aws_lambda as lambda,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';

/** Cognito rejects a domain prefix that contains any of these words. */
const RESERVED_DOMAIN_WORDS = ['aws', 'amazon', 'cognito'];
const DOMAIN_PREFIX = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const LOCAL_HOSTS = ['localhost', '127.0.0.1'];

/** The managed policy scripts/provision_service_logins.py creates for the meridian_identity login. */
export const IDENTITY_LOGIN_POLICY_NAME = 'MeridianIdentityAuroraAccess';

export interface MeridianIdentityStackProps extends StackProps {
  /** Hosted UI domain prefix, unique in the Region: https://<prefix>.auth.<region>.amazoncognito.com. */
  domainPrefix: string;
  /** Where the browser may be sent back after sign-in. HTTPS, or http on localhost. */
  callbackUrls: string[];
  /** Where the browser may be sent back after sign-out. */
  logoutUrls: string[];
  clusterArn: string;
  /** The meridian_identity login's secret: it can read traveler_identity_bindings and nothing else. */
  identitySecretArn: string;
  database: string;
}

function assertReturnUrl(url: string): void {
  const parsed = new URL(url);
  const local = parsed.protocol === 'http:' && LOCAL_HOSTS.includes(parsed.hostname);
  if (parsed.protocol !== 'https:' && !local) {
    throw new Error(`Return URL must be https or http on localhost: ${url}`);
  }
  if (parsed.hash) throw new Error(`Return URL must not contain a fragment: ${url}`);
}

/**
 * One user pool, one single-page-app client, and the trigger that puts the traveler in the token.
 *
 * Sign-up is closed: people are created by scripts/seed_cognito_users.py. The access token lasts
 * one hour and carries `traveler_id`, copied from traveler_identity_bindings by the trigger.
 */
export class MeridianIdentityStack extends Stack {
  readonly userPool: cognito.UserPool;
  readonly appClient: cognito.UserPoolClient;
  readonly trigger: lambda.Function;

  constructor(scope: Construct, id: string, props: MeridianIdentityStackProps) {
    super(scope, id, props);
    if (!DOMAIN_PREFIX.test(props.domainPrefix)
        || RESERVED_DOMAIN_WORDS.some((word) => props.domainPrefix.includes(word))) {
      throw new Error('domainPrefix must be lowercase letters, digits and hyphens, and must not contain aws, amazon or cognito');
    }
    if (!props.callbackUrls.length) throw new Error('At least one callback URL is required');
    [...props.callbackUrls, ...props.logoutUrls].forEach(assertReturnUrl);

    this.trigger = new lambda.Function(this, 'PreTokenGeneration', {
      description: 'Adds the bound traveler_id to the Cognito access token; refuses a user with no binding',
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'pre_token_generation.lambda_handler',
      code: lambda.Code.fromAsset(path.resolve(__dirname, '..', '..', 'functions', 'pre_token_generation')),
      timeout: Duration.seconds(4),
      memorySize: 256,
      environment: {
        AURORA_CLUSTER_ARN: props.clusterArn,
        AURORA_SECRET_ARN: props.identitySecretArn,
        AURORA_DATABASE: props.database,
      },
    });
    this.trigger.role!.addManagedPolicy(
      iam.ManagedPolicy.fromManagedPolicyArn(
        this, 'IdentityLoginAccess',
        `arn:aws:iam::${this.account}:policy/${IDENTITY_LOGIN_POLICY_NAME}`,
      ),
    );

    this.userPool = new cognito.UserPool(this, 'Pool', {
      userPoolName: 'meridian-travelers',
      featurePlan: cognito.FeaturePlan.ESSENTIALS,
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      standardAttributes: { email: { required: true, mutable: false } },
      passwordPolicy: {
        minLength: 14, requireLowercase: true, requireUppercase: true, requireDigits: true, requireSymbols: true,
      },
      mfa: cognito.Mfa.OFF,
      accountRecovery: cognito.AccountRecovery.NONE,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.userPool.addTrigger(
      cognito.UserPoolOperation.PRE_TOKEN_GENERATION_CONFIG, this.trigger, cognito.LambdaVersion.V2_0,
    );
    this.userPool.addDomain('Domain', { cognitoDomain: { domainPrefix: props.domainPrefix } });

    this.appClient = this.userPool.addClient('Web', {
      userPoolClientName: 'meridian-web',
      generateSecret: false,
      // ADMIN_USER_PASSWORD_AUTH is a server-side flow: only a caller with IAM permission can use it,
      // never a browser. Proof scripts use it to mint real tokens for the seeded users.
      authFlows: { userSrp: true, adminUserPassword: true },
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: props.callbackUrls,
        logoutUrls: props.logoutUrls,
      },
      supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
      accessTokenValidity: Duration.hours(1),
      idTokenValidity: Duration.hours(1),
      refreshTokenValidity: Duration.days(1),
      enableTokenRevocation: true,
      preventUserExistenceErrors: true,
      writeAttributes: new cognito.ClientAttributes(),
    });

    new CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new CfnOutput(this, 'AppClientId', { value: this.appClient.userPoolClientId });
    new CfnOutput(this, 'HostedUiDomain', { value: `${props.domainPrefix}.auth.${this.region}.amazoncognito.com` });
    new CfnOutput(this, 'Issuer', { value: `https://cognito-idp.${this.region}.amazonaws.com/${this.userPool.userPoolId}` });
  }
}
