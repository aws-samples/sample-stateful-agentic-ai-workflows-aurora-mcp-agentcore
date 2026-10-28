import { afterEach, expect, it, vi } from 'vitest';
import {
  ApiError, STREAM_HARD_CAP_MS, STREAM_IDLE_TIMEOUT_MS, requestJson, runWithDeadline,
  runWithStreamDeadline,
} from './request';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it('bounds a stalled body read, aborts its signal, and ignores late success', async () => {
  vi.useFakeTimers();
  let signal!: AbortSignal;
  vi.stubGlobal('fetch', vi.fn((_url, init) => {
    signal = init.signal;
    return Promise.resolve({ ok: true, json: () => new Promise(() => {}) });
  }));
  const request = requestJson('/chat');
  const assertion = expect(request).rejects.toMatchObject({ name: 'TimeoutError' });
  await vi.advanceTimersByTimeAsync(55_000);
  await assertion;
  expect(signal.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
});

it('supports stop waiting and never dispatches an already-aborted request', async () => {
  const controller = new AbortController();
  controller.abort();
  const operation = vi.fn();
  await expect(runWithDeadline(operation, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  expect(operation).not.toHaveBeenCalled();
});

it('keeps the server conflict explanation instead of a generic offline label', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ error: 'This recovery is already running.' }) }));
  await expect(requestJson('/chat')).rejects.toThrow('This recovery is already running.');
});

it('accepts an empty successful DELETE response without attempting JSON parsing', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
  await expect(requestJson('/memory/fact', { method: 'DELETE' })).resolves.toBeUndefined();
});

it('marks a well-formed server reply as worded', async () => {
  const body = { error: 'The hold was placed, but saving failed.', code: 'governed_write' };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 503 })));
  await expect(requestJson('/hold')).rejects.toMatchObject({ status: 503, worded: true });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ error: 'Not your hold.' }), { status: 403 })));
  await expect(requestJson('/hold')).rejects.toMatchObject({ status: 403, worded: true });
});

it.each([
  ['a proxy HTML page', '<html>504</html>', 504],
  ['an empty body', '', 502],
  ['JSON without an error string', '{"message":"Bad gateway"}', 502],
  ['a proxy JSON body with a string error', '{"error":"Bad gateway"}', 502],
  ['an error that is not a string', '{"error":{"x":1},"code":"governed_write"}', 503],
  ['a code that is not a string', '{"error":"x","code":7}', 400],
])('marks %s as not worded', async (_name, text, status) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(text, { status })));
  await expect(requestJson('/hold')).rejects.toMatchObject({ status, worded: false });
});

it('rejects a 200 whose body is not JSON with an error that is not a server reply', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>ok</html>', { status: 200 })));
  const failure = await requestJson('/hold').catch(error => error);
  expect(failure).toBeInstanceOf(SyntaxError);
  expect(failure).not.toBeInstanceOf(ApiError);
});

it('keeps waiting while the stream touches the idle timer, and stops at the hard ceiling', async () => {
  vi.useFakeTimers();
  let signal!: AbortSignal;
  const waiting = runWithStreamDeadline((own, touch) => {
    signal = own;
    const keepalive = setInterval(touch, 10_000);
    own.addEventListener('abort', () => clearInterval(keepalive));
    return new Promise<never>(() => {});
  });
  const assertion = expect(waiting).rejects.toMatchObject({ name: 'TimeoutError' });
  await vi.advanceTimersByTimeAsync(STREAM_HARD_CAP_MS - 1);
  expect(signal.aborted).toBe(false);
  await vi.advanceTimersByTimeAsync(1);
  await assertion;
  expect(signal.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
});

it('stops waiting after one idle limit without a chunk', async () => {
  vi.useFakeTimers();
  const waiting = runWithStreamDeadline(() => new Promise<never>(() => {}));
  const assertion = expect(waiting).rejects.toMatchObject({ name: 'TimeoutError' });
  await vi.advanceTimersByTimeAsync(STREAM_IDLE_TIMEOUT_MS);
  await assertion;
});

it('keeps the plain request limit at 55 s and the stream ceiling under the 120 s of App Runner', () => {
  expect(STREAM_HARD_CAP_MS).toBeLessThan(120_000);
  expect(STREAM_IDLE_TIMEOUT_MS).toBeGreaterThan(2 * 10_000);
});
