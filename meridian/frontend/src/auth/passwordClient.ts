import type { AuthConfig } from './config';
import { AuthError, type TokenSet } from './tokenClient';

export const VERIFICATION_REQUIRED =
  'Additional verification is required. Use secure sign-in to continue.';

/** Only AWS's standard hosted domain can select a regional credential endpoint. */
export function passwordEndpoint(config: AuthConfig): string | null {
  const match = /^[a-z0-9-]+\.auth\.([a-z]{2}(?:-[a-z]+)+-\d)\.amazoncognito\.com$/u.exec(config.domain);
  return match ? `https://cognito-idp.${match[1]}.amazonaws.com/` : null;
}

type Fields = Record<string, unknown>;
const fieldsOf = (value: unknown): Fields => value && typeof value === 'object'
  ? value as Fields : {};

function serviceError(payload: Fields, status: number): AuthError {
  const name = typeof payload.__type === 'string' ? payload.__type.split('#').pop() : '';
  if (name === 'TooManyRequestsException' || name === 'LimitExceededException') {
    return new AuthError('Too many attempts. Wait a moment and try again.', 429);
  }
  if (name === 'NotAuthorizedException' || name === 'UserNotFoundException') {
    return new AuthError('Check your email and password and try again.', status);
  }
  if (name === 'PasswordResetRequiredException' || name === 'UserNotConfirmedException') {
    return new AuthError(VERIFICATION_REQUIRED, status);
  }
  return new AuthError('Sign-in is unavailable right now. Please try again.', status);
}

/** No credential can follow a redirect or go to the application backend. */
async function request(
  config: AuthConfig, operation: string, body: Fields, fetchFn: typeof fetch,
): Promise<Fields> {
  const endpoint = passwordEndpoint(config);
  if (!endpoint) throw new AuthError('Use secure sign-in for this deployment.');
  const response = await fetchFn(endpoint, {
    method: 'POST', credentials: 'omit', redirect: 'error',
    headers: {
      'Content-Type': 'application/x-amz-json-1.1',
      'X-Amz-Target': `AWSCognitoIdentityProviderService.${operation}`,
    },
    body: JSON.stringify({ ClientId: config.clientId, ...body }),
    signal: AbortSignal.timeout(20_000),
  });
  const payload = fieldsOf(await response.json().catch(() => null));
  if (!response.ok) throw serviceError(payload, response.status);
  return payload;
}

function tokensFrom(payload: Fields, now: () => number, previousRefresh?: string): TokenSet {
  if (payload.ChallengeName) throw new AuthError(VERIFICATION_REQUIRED);
  const result = fieldsOf(payload.AuthenticationResult);
  const expiry = typeof result.ExpiresIn === 'number' ? now() + result.ExpiresIn * 1000 : NaN;
  if (typeof result.AccessToken !== 'string' || !result.AccessToken
    || !Number.isFinite(expiry) || expiry <= now()
    || (result.TokenType !== undefined && String(result.TokenType).toLowerCase() !== 'bearer')) {
    throw new AuthError('The sign-in service returned an unusable session. Try again.');
  }
  return {
    accessToken: result.AccessToken,
    idToken: typeof result.IdToken === 'string' ? result.IdToken : null,
    refreshToken: typeof result.RefreshToken === 'string' ? result.RefreshToken : previousRefresh ?? null,
    expiresAt: expiry, source: 'cognito-api',
  };
}

export async function passwordSignIn(
  config: AuthConfig, username: string, password: string, fetchFn: typeof fetch, now: () => number,
): Promise<TokenSet> {
  return tokensFrom(await request(config, 'InitiateAuth', {
    AuthFlow: 'USER_PASSWORD_AUTH', AuthParameters: { USERNAME: username.trim(), PASSWORD: password },
  }, fetchFn), now);
}

/** Works with either rotated or non-rotated Cognito refresh tokens. */
export async function refreshPasswordTokens(
  config: AuthConfig, refreshToken: string, fetchFn: typeof fetch, now: () => number,
): Promise<TokenSet> {
  return tokensFrom(await request(config, 'GetTokensFromRefreshToken', {
    RefreshToken: refreshToken,
  }, fetchFn), now, refreshToken);
}

export async function revokePasswordTokens(
  config: AuthConfig, refreshToken: string, fetchFn: typeof fetch,
): Promise<void> {
  await request(config, 'RevokeToken', { Token: refreshToken }, fetchFn);
}
