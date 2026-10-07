import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setAccessTokenProvider, setBearerOrigin } from '../auth/accessToken';
import { authorizedFetch, requestJson, setUnauthorizedHandler } from './request';

const API = 'https://api.example.test';
let token: string | null;

const reply = (status: number, body: unknown = {}) =>
  new Response(JSON.stringify(body), { status });

/** Answers 401 to any request that carries the old token and 200 to one with the new token. */
function serverThatWantsToken(wanted: string) {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    const sent = new Headers(init?.headers).get('Authorization');
    return sent === `Bearer ${wanted}` ? reply(200, { ok: true }) : reply(401, { error: 'Expired.' });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function handler(onRefresh: () => Promise<void> | void) {
  const hooks = { refresh: vi.fn(async () => { await onRefresh(); }), signOut: vi.fn() };
  setUnauthorizedHandler(hooks);
  return hooks;
}

beforeEach(() => {
  token = 'old.jwt';
  setAccessTokenProvider(() => token);
  setBearerOrigin(API);
});
afterEach(() => {
  setUnauthorizedHandler(null);
  setAccessTokenProvider(null);
  setBearerOrigin(null);
  vi.unstubAllGlobals();
});

describe('a 401 from the API', () => {
  it('refreshes the session once and retries the request with the new token', async () => {
    const fetchMock = serverThatWantsToken('new.jwt');
    const hooks = handler(() => { token = 'new.jwt'; });
    await expect(requestJson(`${API}/api/me`)).resolves.toEqual({ ok: true });
    expect(hooks.refresh).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const retry = new Headers(fetchMock.mock.calls[1][1]?.headers);
    expect(retry.get('Authorization')).toBe('Bearer new.jwt');
    expect(hooks.signOut).not.toHaveBeenCalled();
  });

  it('signs out when the retry is refused too, and never sends a third request', async () => {
    const fetchMock = serverThatWantsToken('never.jwt');
    const hooks = handler(() => { token = 'new.jwt'; });
    const failure = await requestJson(`${API}/api/me`).catch((error: Error) => error);
    expect(failure).toBeInstanceOf(Error);
    expect((failure as Error).message).toBe('Your session ended. Sign in again.');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(hooks.refresh).toHaveBeenCalledTimes(1);
    expect(hooks.signOut).toHaveBeenCalledTimes(1);
  });

  it('shares one refresh among concurrent 401s', async () => {
    const fetchMock = serverThatWantsToken('new.jwt');
    let release!: () => void;
    const hooks = handler(() => new Promise<void>(resolve => {
      release = () => { token = 'new.jwt'; resolve(); };
    }));
    const calls = Promise.all([
      requestJson(`${API}/api/a`), requestJson(`${API}/api/b`), requestJson(`${API}/api/c`),
    ]);
    await vi.waitFor(() => expect(hooks.refresh).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    release();
    await expect(calls).resolves.toHaveLength(3);
    expect(hooks.refresh).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(6);
    expect(hooks.signOut).not.toHaveBeenCalled();
  });

  it('signs out once even when several retries are refused together', async () => {
    serverThatWantsToken('never.jwt');
    const hooks = handler(() => { token = 'new.jwt'; });
    await Promise.allSettled([requestJson(`${API}/api/a`), requestJson(`${API}/api/b`)]);
    expect(hooks.signOut).toHaveBeenCalledTimes(1);
  });

  it('does not retry when the refresh ended the session', async () => {
    const fetchMock = serverThatWantsToken('new.jwt');
    const hooks = handler(() => { token = null; });
    await expect(requestJson(`${API}/api/me`)).rejects.toThrow('Your session ended');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(hooks.signOut).not.toHaveBeenCalled();
  });

  it('signs out when the refresh itself fails', async () => {
    const fetchMock = serverThatWantsToken('new.jwt');
    const hooks = handler(() => { throw new Error('network'); });
    await expect(requestJson(`${API}/api/me`)).rejects.toThrow('Your session ended');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(hooks.signOut).toHaveBeenCalledTimes(1);
  });

  it('retries without refreshing when another request already got a newer token', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const sent = new Headers(init?.headers).get('Authorization');
      if (sent === 'Bearer old.jwt') { token = 'new.jwt'; return reply(401); }
      return reply(200, { ok: true });
    });
    vi.stubGlobal('fetch', fetchMock);
    const hooks = handler(() => undefined);
    await expect(requestJson(`${API}/api/me`)).resolves.toEqual({ ok: true });
    expect(hooks.refresh).not.toHaveBeenCalled();
  });

  it('never puts a token in the error text', async () => {
    serverThatWantsToken('never.jwt');
    handler(() => { token = 'new.jwt'; });
    const failure = await requestJson(`${API}/api/me`).catch((error: Error) => error);
    expect((failure as Error).message).not.toMatch(/old\.jwt|new\.jwt|Bearer/);
  });

  it('keeps the plain status message when nothing handles sign-in', async () => {
    setAccessTokenProvider(null);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 401 })));
    await expect(requestJson(`${API}/api/me`)).rejects.toThrow('Request failed (401).');
  });

  it('leaves a 401 from another origin alone', async () => {
    const fetchMock = vi.fn(async () => reply(401, { error: 'Nope.' }));
    vi.stubGlobal('fetch', fetchMock);
    const hooks = handler(() => undefined);
    await expect(requestJson('https://elsewhere.example.test/x')).rejects.toThrow('Nope.');
    expect(hooks.refresh).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('applies to the streamed request too', async () => {
    const fetchMock = serverThatWantsToken('new.jwt');
    handler(() => { token = 'new.jwt'; });
    const response = await authorizedFetch(`${API}/api/chat/stream`, { method: 'POST' });
    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('the code in a 401 body', () => {
  const coded = (code: string) => reply(401, { detail: 'Refused.', code });

  it('token_expired refreshes once and retries with the new token', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => (
      new Headers(init?.headers).get('Authorization') === 'Bearer new.jwt'
        ? reply(200, { ok: true }) : coded('token_expired')));
    vi.stubGlobal('fetch', fetchMock);
    const hooks = handler(() => { token = 'new.jwt'; });
    await expect(requestJson(`${API}/api/me`)).resolves.toEqual({ ok: true });
    expect(hooks.refresh).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(hooks.signOut).not.toHaveBeenCalled();
  });

  it('sign_in_required tries one refresh, signs out and never resends', async () => {
    const fetchMock = vi.fn(async () => coded('sign_in_required'));
    vi.stubGlobal('fetch', fetchMock);
    const hooks = handler(() => { token = 'new.jwt'; });
    await expect(requestJson(`${API}/api/me`)).rejects.toThrow('Your session ended. Sign in again.');
    expect(hooks.refresh).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(hooks.signOut).toHaveBeenCalledTimes(1);
  });

  it('sign_in_required signs out once for concurrent requests', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => coded('sign_in_required')));
    const hooks = handler(() => { token = 'new.jwt'; });
    await Promise.allSettled([requestJson(`${API}/api/a`), requestJson(`${API}/api/b`)]);
    expect(hooks.signOut).toHaveBeenCalledTimes(1);
  });

  it('sign_in_required still signs out when the refresh fails', async () => {
    const fetchMock = vi.fn(async () => coded('sign_in_required'));
    vi.stubGlobal('fetch', fetchMock);
    const hooks = handler(() => { throw new Error('network'); });
    await expect(requestJson(`${API}/api/me`)).rejects.toThrow('Your session ended');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(hooks.signOut).toHaveBeenCalledTimes(1);
  });

  it('a 401 with no code keeps the refresh-and-retry behavior', async () => {
    const fetchMock = serverThatWantsToken('new.jwt');
    const hooks = handler(() => { token = 'new.jwt'; });
    await expect(requestJson(`${API}/api/me`)).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(hooks.refresh).toHaveBeenCalledTimes(1);
  });
});
