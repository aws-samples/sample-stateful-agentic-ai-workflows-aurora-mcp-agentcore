import * as path from 'node:path';
import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  type StackProps,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_logs as logs,
  aws_secretsmanager as secretsmanager,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { DEFAULT_ROTATION_DAYS, type RotatableLogin } from './login-rotation-config';

export interface LoginRotationTarget extends RotatableLogin {
  /** The login secret's complete ARN. */
  secretArn: string;
}

export interface MeridianLoginRotationStackProps extends StackProps {
  clusterArn: string;
  database: string;
  /** The meridian_rotator login's secret (scripts/provision_rotation_login.py). */
  rotatorSecretArn: string;
  /** The logins to rotate. Each gets its own Lambda, so a grant never covers a second login. */
  logins: LoginRotationTarget[];
  /** Days between rotations. Default 30. */
  rotationDays?: number;
  /** Days the rotation Lambdas' logs are kept. Default 90. */
  logRetentionDays?: number;
}

/**
 * Secrets Manager rotation, with the alternating users strategy, for the Aurora workload login
 * secrets named in `logins`. This stack exists only when the `rotateLogins` context names a login
 * (bin/meridian-login-rotation.ts); no other stack in this app refers to it.
 *
 * Each login gets one Lambda (infra/functions/login_rotation) that can read and write that
 * login's secret, read the rotator secret, and call the Data API on the cluster, and nothing else.
 * It runs outside any VPC because the Data API needs none. The schedule does not rotate on
 * creation: the first rotation is an operator's decision (docs/OPERATIONS.md).
 */
export class MeridianLoginRotationStack extends Stack {
  readonly functions: Record<string, lambda.Function> = {};

  constructor(scope: Construct, id: string, props: MeridianLoginRotationStackProps) {
    super(scope, id, { ...props, terminationProtection: true });
    const days = props.rotationDays ?? DEFAULT_ROTATION_DAYS;
    if (!Number.isInteger(days) || days < 1 || days > 365) {
      throw new Error('rotationDays must be a whole number from 1 to 365');
    }
    if (props.logins.length === 0) throw new Error('at least one login is required');
    const rotator = secretsmanager.Secret.fromSecretCompleteArn(this, 'RotatorSecret', props.rotatorSecretArn);
    for (const login of props.logins) {
      this.functions[login.key] = this.rotateLogin(login, props, rotator, days);
    }
  }

  private rotateLogin(
    login: LoginRotationTarget,
    props: MeridianLoginRotationStackProps,
    rotator: secretsmanager.ISecret,
    days: number,
  ): lambda.Function {
    const name = login.key.charAt(0).toUpperCase() + login.key.slice(1);
    const fn = new lambda.Function(this, `${name}Rotation`, {
      description: `Rotates the ${login.role} password with alternating users`,
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'login_rotation.lambda_handler',
      code: lambda.Code.fromAsset(
        path.resolve(__dirname, '..', '..', 'functions', 'login_rotation'),
        { exclude: ['__pycache__', '*.pyc', '*.pyo', '.DS_Store'] },
      ),
      logGroup: new logs.LogGroup(this, `${name}RotationLogs`, {
        retention: (props.logRetentionDays ?? 90) as logs.RetentionDays,
        removalPolicy: RemovalPolicy.DESTROY,
      }),
      timeout: Duration.seconds(60),
      memorySize: 256,
      environment: {
        ROTATION_CLUSTER_ARN: props.clusterArn,
        ROTATION_DATABASE: props.database,
        ROTATION_ROTATOR_SECRET_ARN: props.rotatorSecretArn,
        ROTATION_LOGIN_ROLE: login.role,
      },
    });
    const secret = secretsmanager.Secret.fromSecretCompleteArn(this, `${name}Secret`, login.secretArn);
    // addRotationSchedule below grants the Lambda Describe, Get, Put and UpdateSecretVersionStage on
    // this one secret (and GetRandomPassword, which has no resource to scope to).
    rotator.grantRead(fn);
    fn.addToRolePolicy(new iam.PolicyStatement({
      sid: 'RunStatementsThroughTheDataApi',
      actions: ['rds-data:ExecuteStatement'],
      resources: [props.clusterArn],
    }));
    secret.addRotationSchedule(`${name}Schedule`, {
      rotationLambda: fn,
      automaticallyAfter: Duration.days(days),
      rotateImmediatelyOnUpdate: false,
    });
    new CfnOutput(this, `${name}FunctionName`, { value: fn.functionName });
    return fn;
  }
}
