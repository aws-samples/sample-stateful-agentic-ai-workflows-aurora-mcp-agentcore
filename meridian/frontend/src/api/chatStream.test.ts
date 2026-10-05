import { describe, expect, it, vi } from 'vitest';
import { readChatStream } from './chatStream';

const encoder = new TextEncoder();
const frame = (value: unknown) => `data: ${JSON.stringify(value)}\r\n\r\n`;
const complete = { type: 'complete', response: { message: 'Malé 東京', activities: [] } };
function response(chunks: Uint8Array[]) {
  return new Response(new ReadableStream({ start(controller) { chunks.forEach(chunk => controller.enqueue(chunk)); controller.close(); } }), { headers: { 'Content-Type': 'text/event-stream' } });
}

describe('live concierge stream', () => {
  it('decodes split Unicode and frames, and reconciles a single authoritative result', async () => {
    const bytes = encoder.encode(': heartbeat\r\n\r\n' + frame({ type: 'delta', text: 'Malé 東京' }) + frame(complete));
    const onEvent = vi.fn();
    expect(await readChatStream(response([...bytes].map(byte => Uint8Array.of(byte))), onEvent)).toEqual(complete.response);
    expect(onEvent).toHaveBeenCalledExactlyOnceWith({ type: 'delta', text: 'Malé 東京' });
  });
  it('delivers text while the response is still open', async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
    const onEvent = vi.fn();
    const pending = readChatStream(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }), onEvent);
    controller.enqueue(encoder.encode(frame({ type: 'delta', text: 'First words' })));
    await vi.waitFor(() => expect(onEvent).toHaveBeenCalledOnce());
    controller.enqueue(encoder.encode(frame(complete)));
    expect(await pending).toEqual(complete.response);
  });
  it('keeps truncation and server errors distinct from completion', async () => {
    await expect(readChatStream(response([encoder.encode(frame({ type: 'delta', text: 'Partial' }))]), vi.fn())).rejects.toThrow('interrupted');
    await expect(readChatStream(response([encoder.encode(frame({ type: 'error', message: 'Unavailable' }))]), vi.fn())).rejects.toThrow('Unavailable');
  });
  it('cancels the reader when the user stops waiting', async () => {
    const cancel = vi.fn();
    const controller = new AbortController();
    const pending = readChatStream(new Response(new ReadableStream({ cancel }), { headers: { 'Content-Type': 'text/event-stream' } }), vi.fn(), controller.signal);
    controller.abort(new DOMException('Stopped', 'AbortError'));
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(cancel).toHaveBeenCalledOnce();
  });
  it('does not turn a proxy error into another model request', async () => {
    await expect(readChatStream(new Response('{"detail":"Unavailable"}', { status: 503 }), vi.fn())).rejects.toThrow('Unavailable');
    await expect(readChatStream(new Response('<html>proxy</html>'), vi.fn())).rejects.toThrow('live stream');
  });
});


it('delivers candidate IDs without accepting malformed payloads as cards', async () => {
  const onEvent = vi.fn();
  await readChatStream(response([encoder.encode(
    frame({ type: 'candidates', package_ids: ['CTY-002', 'CTY-002'] })
    + frame({ type: 'candidates', package_ids: [42] }) + frame(complete),
  )]), onEvent);
  expect(onEvent).toHaveBeenCalledExactlyOnceWith({ type: 'candidates', package_ids: ['CTY-002'] });
});

it('reports the error field of a refused stream request', async () => {
  const refused = new Response(JSON.stringify({ error: 'Traveler not authorized' }), {
    status: 403, headers: { 'Content-Type': 'application/json' },
  });
  await expect(readChatStream(refused, vi.fn())).rejects.toThrow('Traveler not authorized');
});
