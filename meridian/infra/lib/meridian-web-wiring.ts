import type { IdentityMode } from './meridian-web-stack';

/** The roles stack settings that depend on the identity mode and the publish-time environment. */
export interface RolesStackWiring {
  masterSecretArn: string | undefined;
  tighten: boolean;
}

/**
 * The master secret is passed only in jwt mode, where the role keeps it for a rollback.
 * `MERIDIAN_TIGHTEN_ROLE=1` drops it and is ignored in iam mode.
 */
export function rolesStackWiring(
  dotenv: Record<string, string>,
  mode: IdentityMode,
  processEnv: Record<string, string | undefined>,
): RolesStackWiring {
  const jwt = mode === 'jwt';
  return {
    masterSecretArn: jwt ? dotenv.AURORA_SECRET_ARN : undefined,
    tighten: jwt && processEnv.MERIDIAN_TIGHTEN_ROLE === '1',
  };
}
