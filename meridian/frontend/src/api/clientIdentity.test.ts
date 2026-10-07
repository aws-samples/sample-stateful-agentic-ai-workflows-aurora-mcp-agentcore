import { afterEach, describe, expect, it, vi } from 'vitest';
import { setAccessTokenProvider } from '../auth/accessToken';
import {
  fetchHealth,
  fetchJourneys,
  fetchMemoryProfile,
  fetchProducts,
  fetchRlsProbe,
  fetchSessionIdentity,
  fetchSessionReceipt,
  sendChatMessage,
  stopRuntimeSession,
} from './client';

afterEach(() => { setAccessTokenProvider(null); vi.unstubAllGlobals(); });

function stubFetch(body: unknown = {}) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
  vi.stubGlobal('fetch', fetch);
  return () => {
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    return { url, init, headers: new Headers(init.headers) };
  };
}

describe('the bearer token', () => {
  it.each([
    ['health', () => fetchHealth(), {}],
    ['catalog', () => fetchProducts(), { products: [] }],
    ['journeys', () => fetchJourneys(), { journeys: [] }],
    ['memory', () => fetchMemoryProfile(), { traveler_id: 'x', facts: [] }],
    ['identity', () => fetchSessionIdentity(), { traveler_id: 'x', authentication: 'cognito' }],
    ['journey stop', () => stopRuntimeSession('jrn_1'), {}],
    ['chat', () => sendChatMessage({ message: 'hi', phase: 1 }), { message: 'ok' }],
  ])('goes with the %s request', async (_label, call, body) => {
    setAccessTokenProvider(() => 'access.jwt');
    const request = stubFetch(body);
    await call();
    expect(request().headers.get('Authorization')).toBe('Bearer access.jwt');
  });

  it('goes with the streamed concierge request too', async () => {
    setAccessTokenProvider(() => 'access.jwt');
    const request = stubFetch();
    await sendChatMessage({ message: 'hi', phase: 4 }, undefined, () => undefined)
      .catch(() => undefined);
    expect(request().url).toMatch(/\/chat\/stream$/);
    expect(request().headers.get('Authorization')).toBe('Bearer access.jwt');
  });

  it('is absent when nobody signed in', async () => {
    const request = stubFetch({ products: [] });
    await fetchProducts();
    expect(request().headers.has('Authorization')).toBe(false);
  });
});

describe('the traveler is never named by the page', () => {
  it('reads memory for the signed-in traveler by default', async () => {
    const request = stubFetch({ traveler_id: 'x', facts: [] });
    await fetchMemoryProfile();
    expect(request().url).toMatch(/\/memory\/me$/);
  });

  it.each([
    ['permission evidence', () => fetchRlsProbe()],
    ['session receipt', () => fetchSessionReceipt()],
  ])('asks for the %s of the signed-in traveler by default', async (_label, call) => {
    const request = stubFetch({});
    await call();
    expect(JSON.parse(request().init.body as string).traveler_id).toBe('me');
  });
});

describe('fetchSessionIdentity', () => {
  it('returns who the API says is calling', async () => {
    stubFetch({ traveler_id: 'trv_demo_decoy', authentication: 'cognito' });
    await expect(fetchSessionIdentity())
      .resolves.toEqual({ traveler_id: 'trv_demo_decoy', authentication: 'cognito' });
  });

  it.each([{}, { traveler_id: '' }, { traveler_id: 7 }])(
    'refuses an answer that names no traveler: %j',
    async body => {
      stubFetch(body);
      await expect(fetchSessionIdentity()).rejects.toThrow('did not name a traveler');
    },
  );
});
