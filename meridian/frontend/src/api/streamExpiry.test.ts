import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setAccessTokenProvider } from '../auth/accessToken';
import { sendChatMessage } from './client';
import { SESSION_ENDED_MESSAGE, STREAM_REFRESHED_MESSAGE, setUnauthorizedHandler } from './request';

const encoder = new TextEncoder();
const frame = (value: unknown) => `data: ${JSON.stringify(value)}\n\n`;
const expired = frame({ type: 'error', code: 'token_expired', message: 'eyJ.leaked.jwt' });
const complete = frame({ type: 'complete', response: { message: 'Done', activities: [] } });
const stream = (...frames: string[]) => new Response(encoder.encode(frames.join('')), {
  headers: { 'Content-Type': 'text/event-stream' },
});
const request = { message: 'hi', phase: 4 } as const;
let token: string;

function handler() {
  const hooks = { refresh: vi.fn(async () => { token = 'new.jwt'; }), signOut: vi.fn() };
  setUnauthorizedHandler(hooks);
  return hooks;
}

beforeEach(() => {
  token = 'old.jwt';
  setAccessTokenProvider(() => token);
});
afterEach(() => {
  setUnauthorizedHandler(null);
  setAccessTokenProvider(null);
  vi.unstubAllGlobals();
});

describe('a token that expires during a streamed turn', () => {
  it('retries once with the new token when nothing was delivered yet', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(stream(expired)).mockResolvedValueOnce(stream(complete));
    vi.stubGlobal('fetch', fetchMock);
    const hooks = handler();
    const result = await sendChatMessage(request, undefined, vi.fn());
    expect(result.message).toBe('Done');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(hooks.refresh).toHaveBeenCalledOnce();
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get('Authorization')).toBe('Bearer new.jwt');
  });

  it('retries once after an HTTP 401 before the stream starts', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('{}', { status: 401 })).mockResolvedValueOnce(stream(complete));
    vi.stubGlobal('fetch', fetchMock);
    const hooks = handler();
    await sendChatMessage(request, undefined, vi.fn());
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(hooks.refresh).toHaveBeenCalledOnce();
  });

  it('does not resend after content, refreshes, and asks the person to send again', async () => {
    const fetchMock = vi.fn().mockResolvedValue(stream(frame({ type: 'delta', text: 'Booking' }), expired));
    vi.stubGlobal('fetch', fetchMock);
    const hooks = handler();
    const error = await sendChatMessage(request, undefined, vi.fn()).catch(e => e);
    expect(error.message).toBe(STREAM_REFRESHED_MESSAGE);
    await vi.waitFor(() => expect(hooks.refresh).toHaveBeenCalledOnce());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(hooks.signOut).not.toHaveBeenCalled();
  });

  it('signs out when the refresh is refused', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream(expired)));
    const hooks = { refresh: vi.fn().mockRejectedValue(new Error('refused')), signOut: vi.fn() };
    setUnauthorizedHandler(hooks);
    const error = await sendChatMessage(request, undefined, vi.fn()).catch(e => e);
    expect(error.message).toBe(SESSION_ENDED_MESSAGE);
    expect(hooks.signOut).toHaveBeenCalledOnce();
  });

  it('signs out and stops when the retried stream expires again', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => stream(expired));
    vi.stubGlobal('fetch', fetchMock);
    const hooks = handler();
    const error = await sendChatMessage(request, undefined, vi.fn()).catch(e => e);
    expect(error.message).toBe(SESSION_ENDED_MESSAGE);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(hooks.signOut).toHaveBeenCalledOnce();
  });

  it('never shows token or event text in any message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => stream(expired)));
    handler();
    const error = await sendChatMessage(request, undefined, vi.fn()).catch(e => e);
    expect(error.message).not.toMatch(/eyJ|leaked|old\.jwt|new\.jwt/);
    expect(STREAM_REFRESHED_MESSAGE).not.toMatch(/[·–—]|demo/i);
  });
});
