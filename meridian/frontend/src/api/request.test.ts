import { afterEach, expect, it, vi } from 'vitest';
import { requestJson, runWithDeadline } from './request';

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
