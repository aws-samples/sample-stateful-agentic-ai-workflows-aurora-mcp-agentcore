import type { AuthConfig } from './config';

export interface TokenSet {
  accessToken: string;
  idToken: string | null;
  refreshToken: string | null;
  /** Milliseconds since the epoch. */
  expiresAt: number;
}

export class AuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AuthError';
  }
}

type Fetch = typeof fetch;

async function requestTokens(
  config: AuthConfig, body: Record<string, string>, fetchFn: Fetch, now: () => number,
): Promise<TokenSet> {
  const response = await fetchFn(`https://${config.domain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: config.clientId, ...body }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || typeof payload.access_token !== 'string') {
    throw new AuthError(`The sign-in service refused the request (${response.status}).`);
  }
  return {
    accessToken: payload.access_token,
    idToken: typeof payload.id_token === 'string' ? payload.id_token : null,
    refreshToken: typeof payload.refresh_token === 'string' ? payload.refresh_token : null,
    expiresAt: now() + Number(payload.expires_in ?? 3600) * 1000,
  };
}

/** Trade the authorization code, with the PKCE verifier, for tokens. */
export function exchangeCode(
  config: AuthConfig, code: string, verifier: string, fetchFn: Fetch, now: () => number,
): Promise<TokenSet> {
  return requestTokens(config, {
    grant_type: 'authorization_code', code, redirect_uri: config.redirectUri,
    code_verifier: verifier,
  }, fetchFn, now);
}

/** Trade the refresh token for new tokens. Cognito keeps the same refresh token. */
export async function refreshTokens(
  config: AuthConfig, refreshToken: string, fetchFn: Fetch, now: () => number,
): Promise<TokenSet> {
  const tokens = await requestTokens(
    config, { grant_type: 'refresh_token', refresh_token: refreshToken }, fetchFn, now,
  );
  return { ...tokens, refreshToken: tokens.refreshToken ?? refreshToken };
}
