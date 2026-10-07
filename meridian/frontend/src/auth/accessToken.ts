/**
 * The one place the API client learns the signed-in person's access token.
 *
 * The auth session installs a provider when it signs in. Until then, and in the legacy
 * shared-token mode, nothing is installed and requests carry no Authorization header of
 * their own.
 */
type TokenProvider = () => string | null;

const NO_TOKEN: TokenProvider = () => null;
let provider: TokenProvider = NO_TOKEN;

export function setAccessTokenProvider(next: TokenProvider | null): void {
  provider = next ?? NO_TOKEN;
}

export function getAccessToken(): string | null {
  return provider();
}

/** Headers with the bearer token added when there is one and the caller set none. */
export function withBearer(headers?: HeadersInit): HeadersInit | undefined {
  const token = getAccessToken();
  if (!token) return headers;
  const merged = new Headers(headers);
  if (!merged.has('Authorization')) merged.set('Authorization', `Bearer ${token}`);
  return merged;
}
