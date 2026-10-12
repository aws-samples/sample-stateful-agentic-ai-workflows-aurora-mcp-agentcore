import type { AuthConfig } from './config';

export interface TokenSet {
  accessToken: string;
  idToken: string | null;
  refreshToken: string | null;
  /** Milliseconds since the epoch. */
  expiresAt: number;
}

export class AuthError extends Error {
  /** The HTTP status of the sign-in service's answer, or null when there was none. */
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = 'AuthError';
    this.status = status;
  }
}

/**
 * True when the sign-in service said no (a 4xx other than a timeout or rate limit), so trying
 * again with the same token cannot help. A network failure, a 5xx or a malformed answer is
 * transient and is not a refusal.
 */
export function isRefusal(error: unknown): boolean {
  if (!(error instanceof AuthError) || error.status === null) return false;
  return error.status >= 400 && error.status < 500 && error.status !== 408
    && error.status !== 429;
}

const DEFAULT_LIFETIME_SECONDS = 3600;

type Fetch = typeof fetch;

async function requestTokens(
  config: AuthConfig, body: Record<string, string>, fetchFn: Fetch, now: () => number,
): Promise<TokenSet> {
  const response = await fetchFn(`https://${config.domain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: config.clientId, ...body }),
  });
  const payload: unknown = await response.json().catch(() => null);
  const fields = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  if (!response.ok || typeof fields.access_token !== 'string') {
    throw new AuthError(
      `The sign-in service refused the request (${response.status}).`, response.status,
    );
  }
  assertBearer(fields.token_type, response.status);
  return {
    accessToken: fields.access_token,
    idToken: typeof fields.id_token === 'string' ? fields.id_token : null,
    refreshToken: typeof fields.refresh_token === 'string' ? fields.refresh_token : null,
    expiresAt: expiryFrom(fields.expires_in, now(), response.status),
  };
}

function assertBearer(tokenType: unknown, status: number): void {
  if (tokenType === undefined || tokenType === null) return;
  if (typeof tokenType !== 'string' || tokenType.toLowerCase() !== 'bearer') {
    throw new AuthError('The sign-in service returned a token that is not a bearer token.', status);
  }
}

function expiryFrom(expiresIn: unknown, nowMs: number, status: number): number {
  const seconds = expiresIn === undefined ? DEFAULT_LIFETIME_SECONDS : expiresIn;
  const expiresAt = typeof seconds === 'number' ? nowMs + seconds * 1000 : Number.NaN;
  if (typeof seconds !== 'number' || !(seconds > 0) || !Number.isFinite(expiresAt)) {
    throw new AuthError('The sign-in service returned an unusable token lifetime.', status);
  }
  return expiresAt;
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

/** Ask the hosted page to revoke a refresh token. The caller decides what a failure means. */
export async function revokeRefreshToken(
  config: AuthConfig, refreshToken: string, fetchFn: Fetch,
): Promise<void> {
  await fetchFn(`https://${config.domain}/oauth2/revoke`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: config.clientId, token: refreshToken }),
    keepalive: true,
  });
}
