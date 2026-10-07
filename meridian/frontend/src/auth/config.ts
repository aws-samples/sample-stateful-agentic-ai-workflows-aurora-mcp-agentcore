/** Where the browser signs in, read from the build's VITE_COGNITO_* settings. */
export interface AuthConfig {
  /** The hosted sign-in host, for example meridian-x.auth.us-east-1.amazoncognito.com. */
  domain: string;
  clientId: string;
  /** Where Cognito sends the browser back with the authorization code. */
  redirectUri: string;
  /** Where Cognito sends the browser after sign-out. */
  logoutUri: string;
}

export interface AuthEnv {
  VITE_COGNITO_DOMAIN?: string;
  VITE_COGNITO_CLIENT_ID?: string;
}

/** The showcase route handles the return from Cognito, so no extra route is needed. */
export const RETURN_PATH = '/showcase';

/**
 * Read the sign-in settings. Both unset means this build has no sign-in and the API decides who
 * the caller is on its own; exactly one set is a configuration mistake and fails loudly.
 */
export function readAuthConfig(env: AuthEnv, origin: string): AuthConfig | null {
  const domain = env.VITE_COGNITO_DOMAIN?.trim().replace(/^https?:\/\//, '').replace(/\/+$/, '');
  const clientId = env.VITE_COGNITO_CLIENT_ID?.trim();
  if (!domain && !clientId) return null;
  if (!domain || !clientId) {
    throw new Error(
      'Sign-in is half configured: set both VITE_COGNITO_DOMAIN and VITE_COGNITO_CLIENT_ID.',
    );
  }
  const returnUrl = `${origin}${RETURN_PATH}`;
  return { domain, clientId, redirectUri: returnUrl, logoutUri: returnUrl };
}
