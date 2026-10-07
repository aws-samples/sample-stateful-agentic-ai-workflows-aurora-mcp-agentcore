import type { ChatResponse } from '../types';

export type ChatStreamEvent =
  | { type: 'delta' | 'status'; text: string }
  | { type: 'conversation'; conversation_id: string }
  | { type: 'candidates'; package_ids: string[] };

/** The backend ended the stream because the caller's access token expired. */
export class TokenExpiredError extends Error {
  constructor(readonly contentDelivered: boolean) {
    super('The sign-in expired during the request.');
    this.name = 'TokenExpiredError';
  }
}

/** Read real server events; a closed connection is not a completed answer. */
export async function readChatStream(
  response: Response,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body.error === 'string') detail = body.error;
      else if (typeof body.detail === 'string') detail = body.detail;
    } catch { /* Keep the HTTP status. */ }
    throw new Error(detail);
  }
  if (!response.headers.get('content-type')?.includes('text/event-stream') || !response.body) {
    throw new Error('The concierge did not return a live stream. Check the connection and try again.');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let buffer = '';
  let data: string[] = [];
  let contentDelivered = false;
  const abort = () => { void reader.cancel().catch(() => {}); };
  signal?.addEventListener('abort', abort, { once: true });
  try {
    for (;;) {
      signal?.throwIfAborted();
      const { done, value } = await reader.read();
      signal?.throwIfAborted();
      buffer += decoder.decode(value, { stream: !done });
      if (buffer.length > 2_000_000) throw new Error('The concierge response exceeded the stream limit.');
      let end: number;
      while ((end = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, end).replace(/\r$/, '');
        buffer = buffer.slice(end + 1);
        if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
        else if (!line && data.length) {
          const event = JSON.parse(data.join('\n'));
          data = [];
          if (event.type === 'error' && event.code === 'token_expired') throw new TokenExpiredError(contentDelivered);
          if (event.type === 'error') throw new Error(typeof event.message === 'string' ? event.message : 'The response was interrupted.');
          if (event.type === 'complete') {
            if (typeof event.response?.message !== 'string' || !Array.isArray(event.response.activities)) {
              throw new Error('The concierge returned an incomplete result.');
            }
            return event.response as ChatResponse;
          }
          if ((event.type === 'delta' || event.type === 'status') && typeof event.text === 'string') { contentDelivered = true; onEvent(event); }
          else if (event.type === 'conversation' && typeof event.conversation_id === 'string') onEvent(event);
          else if (event.type === 'candidates' && Array.isArray(event.package_ids)
            && event.package_ids.length <= 20 && event.package_ids.every((id: unknown) => typeof id === 'string')) {
            contentDelivered = true;
            onEvent({ type: 'candidates', package_ids: [...new Set<string>(event.package_ids)] });
          }
        }
      }
      if (done) throw new Error('The response was interrupted before it finished. Check the connection before trying again.');
    }
  } finally {
    signal?.removeEventListener('abort', abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
