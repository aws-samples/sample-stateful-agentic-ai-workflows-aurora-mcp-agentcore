export interface AuditExportConfig {
  account: string;
  region: string;
  clusterArn: string;
  auditExportSecretArn: string;
  database: string;
}

function arnParts(arn: string): string[] {
  return arn.split(':');
}

/**
 * Reads the deploy target from meridian/.env and refuses to continue unless the signed-in AWS
 * account is the cluster's. `cdk deploy -a` sets CDK_DEFAULT_ACCOUNT from the credentials in use.
 */
export function resolveAuditExportConfig(
  dotenv: Record<string, string>,
  defaultAccount: string | undefined,
): AuditExportConfig {
  const clusterArn = dotenv.AURORA_CLUSTER_ARN ?? '';
  const auditExportSecretArn = dotenv.AURORA_AUDIT_EXPORT_SECRET_ARN ?? '';
  const [, , , region, account] = arnParts(clusterArn);
  if (arnParts(clusterArn).length < 7 || !/^\d{12}$/.test(account)) {
    throw new Error('meridian/.env needs AURORA_CLUSTER_ARN');
  }
  if (!auditExportSecretArn) {
    throw new Error('meridian/.env needs AURORA_AUDIT_EXPORT_SECRET_ARN; run scripts/provision_service_logins.py --login audit --apply --i-understand-this-changes-aws --write-env');
  }
  const [, , , secretRegion, secretAccount] = arnParts(auditExportSecretArn);
  if (secretAccount !== account) {
    throw new Error('AURORA_AUDIT_EXPORT_SECRET_ARN is in a different account than AURORA_CLUSTER_ARN');
  }
  if (secretRegion !== region) {
    throw new Error('AURORA_AUDIT_EXPORT_SECRET_ARN is in a different Region than AURORA_CLUSTER_ARN');
  }
  if (!defaultAccount) {
    throw new Error('CDK_DEFAULT_ACCOUNT is not set; deploy with `cdk deploy -a "node dist/bin/meridian-audit-export.js"` using credentials for the cluster account');
  }
  if (defaultAccount !== account) {
    throw new Error('The AWS credentials are for a different account than AURORA_CLUSTER_ARN; check AWS_PROFILE');
  }
  return { account, region, clusterArn, auditExportSecretArn, database: dotenv.AURORA_DATABASE ?? 'meridian' };
}
