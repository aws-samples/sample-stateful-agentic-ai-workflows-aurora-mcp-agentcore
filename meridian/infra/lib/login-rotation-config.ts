/** One workload login whose password Secrets Manager can rotate. */
export interface RotatableLogin {
  /** The name used in `-c rotateLogins=...`. */
  key: string;
  /** The Postgres role; its clone is `<role>_clone` (migration 034). */
  role: string;
  /** The meridian/.env line that holds the login secret's ARN. */
  envKey: string;
}

export const ROTATABLE_LOGINS: readonly RotatableLogin[] = [
  { key: 'backend', role: 'meridian_backend', envKey: 'AURORA_BACKEND_SECRET_ARN' },
  { key: 'gateway', role: 'meridian_gateway', envKey: 'AURORA_GATEWAY_SECRET_ARN' },
  { key: 'identity', role: 'meridian_identity', envKey: 'AURORA_IDENTITY_SECRET_ARN' },
  { key: 'catalog', role: 'meridian_catalog', envKey: 'AURORA_CATALOG_SECRET_ARN' },
  { key: 'workflow', role: 'meridian_workflow', envKey: 'AURORA_WORKFLOW_SECRET_ARN' },
  { key: 'audit', role: 'meridian_audit_export', envKey: 'AURORA_AUDIT_EXPORT_SECRET_ARN' },
];

export const DEFAULT_ROTATION_DAYS = 30;

export interface LoginRotationConfig {
  account: string;
  region: string;
  clusterArn: string;
  database: string;
  rotatorSecretArn: string;
  logins: Array<RotatableLogin & { secretArn: string }>;
}

/**
 * Reads the `rotateLogins` context switch. Rotation exists only when it names at least one login:
 * an absent, empty or blank value returns an empty list and the app creates no stack.
 */
export function parseRotateLogins(value: unknown): RotatableLogin[] {
  if (value === undefined || value === null || value === false) return [];
  if (typeof value !== 'string') {
    throw new Error('rotateLogins must be a comma-separated list such as -c rotateLogins=backend');
  }
  const keys = value.split(',').map((item) => item.trim()).filter(Boolean);
  const known = ROTATABLE_LOGINS.map((login) => login.key);
  const seen = new Set<string>();
  return keys.map((key) => {
    const login = ROTATABLE_LOGINS.find((candidate) => candidate.key === key);
    if (!login) throw new Error(`rotateLogins names "${key}"; choose from ${known.join(', ')}`);
    if (seen.has(key)) throw new Error(`rotateLogins names "${key}" twice`);
    seen.add(key);
    return login;
  });
}

export function parseRotationDays(value: unknown): number {
  if (value === undefined) return DEFAULT_ROTATION_DAYS;
  const days = Number(value);
  if (!Number.isInteger(days) || days < 1 || days > 365) {
    throw new Error('rotationDays must be a whole number from 1 to 365');
  }
  return days;
}

function arnParts(arn: string): string[] {
  return arn.split(':');
}

function requireSecretInCluster(name: string, arn: string, account: string, region: string): void {
  const [, , service, secretRegion, secretAccount] = arnParts(arn);
  if (service !== 'secretsmanager') throw new Error(`${name} must be a Secrets Manager secret ARN`);
  if (secretAccount !== account) throw new Error(`${name} is in a different account than AURORA_CLUSTER_ARN`);
  if (secretRegion !== region) throw new Error(`${name} is in a different Region than AURORA_CLUSTER_ARN`);
}

/**
 * Reads the deploy target from meridian/.env for the chosen logins and refuses to continue unless
 * the signed-in AWS account is the cluster's. A login without its secret ARN is an error, never
 * a skipped login: a rotation that silently covers fewer logins than were asked for is worse
 * than none.
 */
export function resolveLoginRotationConfig(
  dotenv: Record<string, string>,
  defaultAccount: string | undefined,
  logins: RotatableLogin[],
): LoginRotationConfig {
  if (logins.length === 0) throw new Error('rotateLogins names no login');
  const clusterArn = dotenv.AURORA_CLUSTER_ARN ?? '';
  const [, , , region, account] = arnParts(clusterArn);
  if (arnParts(clusterArn).length < 7 || !/^\d{12}$/.test(account)) {
    throw new Error('meridian/.env needs AURORA_CLUSTER_ARN');
  }
  const rotatorSecretArn = dotenv.AURORA_ROTATOR_SECRET_ARN ?? '';
  if (!rotatorSecretArn) {
    throw new Error('meridian/.env needs AURORA_ROTATOR_SECRET_ARN; run scripts/provision_rotation_login.py --apply --i-understand-this-changes-aws --write-env');
  }
  requireSecretInCluster('AURORA_ROTATOR_SECRET_ARN', rotatorSecretArn, account, region);
  const resolved = logins.map((login) => {
    const secretArn = dotenv[login.envKey] ?? '';
    if (!secretArn) throw new Error(`meridian/.env needs ${login.envKey} to rotate the ${login.key} login`);
    requireSecretInCluster(login.envKey, secretArn, account, region);
    return { ...login, secretArn };
  });
  if (!defaultAccount) {
    throw new Error('CDK_DEFAULT_ACCOUNT is not set; deploy with credentials for the cluster account');
  }
  if (defaultAccount !== account) {
    throw new Error('The AWS credentials are for a different account than AURORA_CLUSTER_ARN; check AWS_PROFILE');
  }
  return {
    account,
    region,
    clusterArn,
    database: dotenv.AURORA_DATABASE ?? 'meridian',
    rotatorSecretArn,
    logins: resolved,
  };
}
