import { getAccessToken, withBearer } from '../auth/accessToken';

/** Browser deadlines end the wait, not a server-side transaction. */
export const REQUEST_TIMEOUT_MS = 55_000;

/**
 * A streamed call ends when nothing arrives for this long. The backend sends a keepalive every
 * 10 s, so a silent stream means the connection or the server is gone.
 */
export const STREAM_IDLE_TIMEOUT_MS = 30_000;

/** The longest a streamed call waits: under App Runner's 120 s limit on a whole request. */
export const STREAM_HARD_CAP_MS = 110_000;

interface Limits {
  /** Ends the wait after this long without `touch()`; unset means no idle timer. */
  idleMs?: number;
  capMs: number;
}

function runWithLimits<T>(
  operation: (signal: AbortSignal, touch: () => void) => Promise<T>,
  signal: AbortSignal | undefined,
  { idleMs, capMs }: Limits,
): Promise<T> {
  const controller = new AbortController();
  return new Promise<T>((resolve, reject) => {
    const abort = () => controller.abort(signal?.reason);
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const stop = (message: string) => () => controller.abort(new DOMException(message, 'TimeoutError'));
    const touch = () => {
      if (idleMs === undefined) return;
      clearTimeout(idleTimer);
      idleTimer = setTimeout(stop('The response went quiet.'), idleMs);
    };
    const finish = () => {
      clearTimeout(capTimer);
      clearTimeout(idleTimer);
      signal?.removeEventListener('abort', abort);
    };
    const capTimer = setTimeout(stop('The response took too long.'), capMs);
    touch();
    controller.signal.addEventListener('abort', () => {
      finish();
      reject(controller.signal.reason ?? new DOMException('Stopped waiting.', 'AbortError'));
    }, { once: true });
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) { abort(); return; }
    try {
      operation(controller.signal, touch).then(resolve, reject).finally(finish);
    } catch (error) {
      finish();
      reject(error);
    }
  });
}

export function runWithDeadline<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<T> {
  return runWithLimits(operation, signal, { capMs: timeoutMs });
}

/**
 * Waits for a call that streams. `touch` restarts the idle timer; the caller invokes it for
 * every chunk that arrives, keepalives included. The wait ends at the idle limit or the hard
 * cap, whichever comes first, with a TimeoutError.
 */
export function runWithStreamDeadline<T>(
  operation: (signal: AbortSignal, touch: () => void) => Promise<T>,
  signal?: AbortSignal,
  limits: Partial<Limits> = {},
): Promise<T> {
  return runWithLimits(operation, signal, {
    idleMs: limits.idleMs ?? STREAM_IDLE_TIMEOUT_MS,
    capMs: limits.capMs ?? STREAM_HARD_CAP_MS,
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

/** The body code of a 5xx the route itself worded (a hold or booking outcome). */
export const GOVERNED_WRITE_CODE = 'governed_write';

/**
 * The API answered with an error status. `worded` is true only for a well-formed server error
 * reply: a JSON object with a string `error` (and a string `code` when it has one), carrying a
 * 4xx status or, for a 5xx, the `governed_write` code. A proxy page, an empty body, JSON of any
 * other shape and a 5xx without that code leave it false; the message is then only a status.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly worded: boolean;

  constructor(message: string, status: number, worded = true) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.worded = worded;
  }
}

/** A failure raised here before any request was sent, so its text is true as written. */
export class NotSentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'NotSentError';
  }
}

export function isServerReply(body: unknown, status: number): boolean {
  if (typeof body !== 'object' || body === null) return false;
  const { error, code } = body as { error?: unknown; code?: unknown };
  if (typeof error !== 'string') return false;
  if (code !== undefined && typeof code !== 'string') return false;
  return status < 500 || code === GOVERNED_WRITE_CODE;
}

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
  if (signInRequired || !refreshed) throw new NotSentError(SESSION_ENDED_MESSAGE);
  const retry = await send().promise;
  if (retry.status === 401) {
    endSession(handler);
    throw new NotSentError(SESSION_ENDED_MESSAGE);
  }
  return retry;
}

export async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  return runWithDeadline(async signal => {
    const response = await authorizedFetch(url, { cache: 'no-store', ...init, signal });
    if (!response.ok) {
      let detail = `Request failed (${response.status}).`;
      let worded = false;
      try {
        const body = await response.json();
        if (typeof body.error === 'string') detail = body.error;
        else if (typeof body.detail === 'string') detail = body.detail;
        worded = isServerReply(body, response.status);
      } catch { /* Non-JSON proxy failures still have a useful HTTP status. */ }
      throw new ApiError(detail, response.status, worded);
    }
    // DELETE endpoints legitimately return no JSON body.
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }, init.signal ?? undefined);
}
