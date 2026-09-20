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

export async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  return runWithDeadline(async signal => {
    const response = await fetch(url, { cache: 'no-store', ...init, signal });
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
