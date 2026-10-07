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

let bearerOrigin: string | null = null;

/** The one origin that may receive the token: the API's. Null withholds it from every request. */
export function setBearerOrigin(origin: string | null): void {
  bearerOrigin = origin;
}

function originOf(url: string): string | null {
  try {
    const base = typeof window === 'undefined' ? undefined : window.location.href;
    return new URL(url, base).origin;
  } catch {
    return null;
  }
}

/**
 * Headers with the bearer token added when there is one, the caller set none, and the request
 * goes to the API's own origin. A request to any other address never carries the token.
 */
export function withBearer(url: string, headers?: HeadersInit): HeadersInit | undefined {
  const token = getAccessToken();
  if (!token || bearerOrigin === null || originOf(url) !== bearerOrigin) return headers;
  const merged = new Headers(headers);
  if (!merged.has('Authorization')) merged.set('Authorization', `Bearer ${token}`);
  return merged;
}
