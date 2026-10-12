import { getAccessToken, withBearer } from '../auth/accessToken';

/** Browser deadlines end the wait, not a server-side transaction. */
export const REQUEST_TIMEOUT_MS = 55_000;

export function runWithDeadline<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  return new Promise<T>((resolve, reject) => {
    const abort = () => controller.abort(signal?.reason);
    const finish = () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
    };
    const timer = setTimeout(() => controller.abort(new DOMException('The response took too long.', 'TimeoutError')), timeoutMs);
    controller.signal.addEventListener('abort', () => {
      finish();
      reject(controller.signal.reason ?? new DOMException('Stopped waiting.', 'AbortError'));
    }, { once: true });
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) { abort(); return; }
    try {
      operation(controller.signal).then(resolve, reject).finally(finish);
    } catch (error) {
      finish();
      reject(error);
    }
  });
}

/** What the page does when the API says the access token is no longer good. */
export interface UnauthorizedHandler {
  /** Gets a fresh access token; the session ends itself when the refresh is refused. */
  refresh: () => Promise<void>;
  /** Ends the session for good. */
  signOut: () => void;
}

export const SESSION_ENDED_MESSAGE = 'Your session ended. Sign in again.';

let unauthorized: UnauthorizedHandler | null = null;
let refreshing: Promise<boolean> | null = null;
let signedOut = false;

export const STREAM_REFRESHED_MESSAGE = 'Your sign-in was refreshed. Send that again to continue.';

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorized = handler;
  refreshing = null;
  signedOut = false;
}

function endSession(handler: UnauthorizedHandler): void {
  if (signedOut) return;
  signedOut = true;
  handler.signOut();
}

/** One refresh serves every request that was refused with the same token. */
function refreshOnce(handler: UnauthorizedHandler, refusedAuthorization: string): Promise<boolean> {
  const current = getAccessToken();
  if (current && `Bearer ${current}` !== refusedAuthorization) return Promise.resolve(true);
  if (!refreshing) {
    const running = handler.refresh().then(
      () => getAccessToken() !== null,
      () => { endSession(handler); return false; },
    ).finally(() => { if (refreshing === running) refreshing = null; });
    refreshing = running;
  }
  return refreshing;
}

/**
 * Refreshes the session after a stream reported an expired token, sharing any refresh already
 * running. Resolves false when there is no handler or the refresh was refused (which signs out).
 */
export function refreshAfterStreamExpiry(refusedToken: string | null): Promise<boolean> {
  const handler = unauthorized;
  if (!handler || !refusedToken) return Promise.resolve(false);
  return refreshOnce(handler, `Bearer ${refusedToken}`);
}

/** Ends the session after a refreshed token was refused again. */
export function endSessionAfterStreamExpiry(): void {
  if (unauthorized) endSession(unauthorized);
}

/** The backend sends code sign_in_required when no usable token reached it; a refresh cannot fix that. */
async function saysSignInRequired(response: Response): Promise<boolean> {
  try {
    const body = await response.clone().json();
    return body?.code === 'sign_in_required';
  } catch {
    return false;
  }
}

/**
 * fetch with the bearer token. A 401 on a request that carried our token refreshes the session
 * once and sends the request once more; a second 401 signs out. A 401 coded sign_in_required
 * gets the one refresh attempt and then signs out without a resend. It never loops.
 */
export async function authorizedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const send = () => {
    const headers = withBearer(url, init.headers);
    return { headers, promise: fetch(url, { ...init, headers }) };
  };
  const callerSetAuthorization = new Headers(init.headers).has('Authorization');
  const first = send();
  const response = await first.promise;
  const handler = unauthorized;
  const sent = new Headers(first.headers).get('Authorization');
  if (response.status !== 401 || !handler || !sent || callerSetAuthorization) return response;
  const signInRequired = await saysSignInRequired(response);
  const refreshed = await refreshOnce(handler, sent);
  if (signInRequired) endSession(handler);
  if (signInRequired || !refreshed) throw new Error(SESSION_ENDED_MESSAGE);
  const retry = await send().promise;
  if (retry.status === 401) {
    endSession(handler);
    throw new Error(SESSION_ENDED_MESSAGE);
  }
  return retry;
}

export async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  return runWithDeadline(async signal => {
    const response = await authorizedFetch(url, { cache: 'no-store', ...init, signal });
    if (!response.ok) {
      let detail = `Request failed (${response.status}).`;
      try {
        const body = await response.json();
        if (typeof body.error === 'string') detail = body.error;
        else if (typeof body.detail === 'string') detail = body.detail;
      } catch { /* Non-JSON proxy failures still have a useful HTTP status. */ }
      throw new Error(detail);
    }
    // DELETE endpoints legitimately return no JSON body.
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }, init.signal ?? undefined);
}
