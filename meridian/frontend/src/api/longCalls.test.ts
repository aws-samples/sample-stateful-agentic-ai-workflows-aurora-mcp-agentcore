import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ReadBackRequiredError } from './chatStream';
import { confirmBooking, processOrder, sendChatMessage } from './client';
import {
  ApiError, STREAM_HARD_CAP_MS, STREAM_IDLE_TIMEOUT_MS, runWithStreamDeadline,
} from './request';

const encoder = new TextEncoder();
const frame = (value: unknown) => `data: ${JSON.stringify(value)}\n\n`;
const done = { type: 'complete', response: { message: 'Hold placed.', order: null, activities: [] } };
const hold = { product_id: 'CTY-002', quantity: 2, phase: 4, size: '5 nights' } as const;
const booking = { booking_id: 'HLD-9', phase: 4 } as const;

/** A server-sent stream that sends a keepalive every 10 s, then `last` after `seconds`. */
function slowStream(seconds: number, last: unknown = done, { silent = false } = {}) {
  let timer: ReturnType<typeof setTimeout>;
  return new Response(new ReadableStream({
    start(controller) {
      let elapsed = 0;
      const tick = () => {
        elapsed += 10;
        if (elapsed >= seconds) {
          controller.enqueue(encoder.encode(frame(last)));
          controller.close();
          return;
        }
        if (!silent) controller.enqueue(encoder.encode(': keepalive\n\n'));
        timer = setTimeout(tick, 10_000);
      };
      timer = setTimeout(tick, 10_000);
    },
    cancel() { clearTimeout(timer); },
  }), { headers: { 'Content-Type': 'text/event-stream' } });
}

/** Settles with the outcome so a test can read it without an unhandled rejection. */
function outcome<T>(pending: Promise<T>) {
  const seen: { value?: T; error?: unknown; settled: boolean } = { settled: false };
  void pending.then(value => { seen.value = value; }, error => { seen.error = error; })
    .finally(() => { seen.settled = true; });
  return seen;
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('a streamed call that outlasts the old 55 s wait', () => {
  it('lets a hold that takes 70 s of virtual time resolve', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(slowStream(70)));
    const call = outcome(runWithStreamDeadline((signal, touch) => processOrder(hold as never, signal, touch)));
    await vi.advanceTimersByTimeAsync(56_000);
    expect(call.settled).toBe(false);
    await vi.advanceTimersByTimeAsync(14_000);
    expect(call.error).toBeUndefined();
    expect(call.value).toMatchObject({ message: 'Hold placed.' });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('lets a booking and a Phase 5 turn that take 100 s of virtual time resolve', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(slowStream(100))));
    const booked = outcome(runWithStreamDeadline(
      (signal, touch) => confirmBooking(booking as never, signal, touch)));
    const recovered = outcome(runWithStreamDeadline((signal, touch) => sendChatMessage(
      { message: 'Plan a recovery', phase: 5 }, signal, () => {}, touch)));
    await vi.advanceTimersByTimeAsync(100_000);
    expect(booked.error).toBeUndefined();
    expect(recovered.error).toBeUndefined();
    expect(booked.value).toMatchObject({ message: 'Hold placed.' });
    expect(recovered.value).toMatchObject({ message: 'Hold placed.' });
  });

  it('ends a stream that goes silent at the idle limit with a TimeoutError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(slowStream(100, done, { silent: true })));
    const call = outcome(runWithStreamDeadline((signal, touch) => processOrder(hold as never, signal, touch)));
    await vi.advanceTimersByTimeAsync(STREAM_IDLE_TIMEOUT_MS - 1);
    expect(call.settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(call.error).toMatchObject({ name: 'TimeoutError' });
  });

  it('ends a stream that keeps sending keepalives at the hard ceiling', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(slowStream(300)));
    const call = outcome(runWithStreamDeadline((signal, touch) => processOrder(hold as never, signal, touch)));
    await vi.advanceTimersByTimeAsync(STREAM_HARD_CAP_MS - 1);
    expect(call.settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(call.error).toMatchObject({ name: 'TimeoutError' });
    expect(STREAM_HARD_CAP_MS).toBeLessThan(120_000);
  });
});

describe('the error contract of a streamed write', () => {
  const failing = (event: unknown) => vi.fn().mockResolvedValue(new Response(
    encoder.encode(frame(event)), { headers: { 'Content-Type': 'text/event-stream' } }));

  it('keeps a worded hold failure worded, with its status', async () => {
    vi.stubGlobal('fetch', failing({
      type: 'error', status: 503, code: 'governed_write',
      message: 'We could not confirm whether the hold was placed. Check your trips before trying again.',
    }));
    const error = await processOrder(hold as never).catch(e => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 503, worded: true });
    expect(error.message).toContain('We could not confirm whether the hold was placed');
  });

  it('keeps a refusal worded and leaves an uncoded 5xx unworded', async () => {
    vi.stubGlobal('fetch', failing({ type: 'error', status: 403, message: 'Not your hold.' }));
    expect(await processOrder(hold as never).catch(e => e)).toMatchObject({ status: 403, worded: true });
    vi.stubGlobal('fetch', failing({ type: 'error', status: 500, message: 'Internal error.' }));
    expect(await processOrder(hold as never).catch(e => e)).toMatchObject({ status: 500, worded: false });
  });

  it('keeps the already-running reply of a Phase 5 resume as a 409', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: 'This recovery is already running.' }), { status: 409 })));
    const error = await sendChatMessage(
      { message: 'resume', phase: 5 }, undefined, () => {}).catch(e => e);
    expect(error).toMatchObject({ status: 409, message: 'This recovery is already running.' });
  });

  it('keeps an already-running resume delivered as a stream error event a 409', async () => {
    vi.stubGlobal('fetch', failing({
      type: 'error', status: 409, message: 'This recovery is already running.',
    }));
    const error = await sendChatMessage(
      { message: 'resume', phase: 5 }, undefined, () => {}).catch(e => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, worded: true, message: 'This recovery is already running.' });
  });

  it('turns the server ceiling into a read-back request, not a failure', async () => {
    vi.stubGlobal('fetch', failing({ type: 'error', code: 'read_back', message: 'Read it back.' }));
    const error = await processOrder(hold as never).catch(e => e);
    expect(error).toBeInstanceOf(ReadBackRequiredError);
    expect(error).not.toBeInstanceOf(ApiError);
  });

  it('sends a hold and a booking to their stream routes', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(slowStream(10)));
    vi.stubGlobal('fetch', fetchMock);
    const first = processOrder(hold as never);
    await vi.advanceTimersByTimeAsync(10_000);
    await first;
    const second = confirmBooking(booking as never);
    await vi.advanceTimersByTimeAsync(10_000);
    await second;
    expect(fetchMock.mock.calls.map(([url]) => String(url).split('/api')[1]))
      .toEqual(['/chat/order/stream', '/chat/book/stream']);
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get('Accept')).toBe('text/event-stream');
  });
});
