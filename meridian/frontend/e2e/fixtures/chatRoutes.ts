import type { Route } from '@playwright/test';

/** True for the plain chat route and the streamed one Phases 4 and 5 use. */
export function isChatCall(url: URL): boolean {
  return url.pathname === '/api/chat' || url.pathname === '/api/chat/stream';
}

function frames(events: object[]): string {
  return [
    'data: {"type":"status","text":"Connecting to your concierge…"}\n\n',
    ': keepalive\n\n',
    ...events.map(event => `data: ${JSON.stringify(event)}\n\n`),
  ].join('');
}

function streamed(route: Route, events: object[]) {
  return route.fulfill({
    status: 200,
    headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
    body: frames(events),
  });
}

/**
 * Answer a chat request the way the backend does: a plain JSON body on `/api/chat`, and on
 * `/api/chat/stream` the same SSE frames (status, keepalive, one `complete` event).
 *
 * @param route The intercepted chat request.
 * @param reply The ChatResponse body.
 */
export function fulfillChat(route: Route, reply: object) {
  if (new URL(route.request().url()).pathname === '/api/chat/stream') {
    return streamed(route, [{ type: 'complete', response: reply }]);
  }
  return route.fulfill({ json: reply });
}

/**
 * Fail a chat request the way the backend does: an HTTP status on `/api/chat`, and on the stream
 * a terminal `error` event that carries the same status and wording.
 *
 * @param route The intercepted chat request.
 * @param status The HTTP status the plain route gives.
 * @param message The worded reason.
 */
export function failChat(route: Route, status: number, message: string) {
  if (new URL(route.request().url()).pathname === '/api/chat/stream') {
    return streamed(route, [{ type: 'error', message, status }]);
  }
  return route.fulfill({ status, json: { error: message } });
}
