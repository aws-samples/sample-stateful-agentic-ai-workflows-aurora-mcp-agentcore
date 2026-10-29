import { afterEach, describe, expect, expectTypeOf, it, vi } from 'vitest';

import {
  healthOriginFor,
  healthUrlsFor,
  resolveBackendOriginFor,
  fetchHealth,
  fetchJourneys,
  fetchRlsProbe,
  fetchSessionReceipt,
  stopRuntimeSession,
  updateMemoryFact,
} from './client';
import { setAccessTokenProvider, setBearerOrigin, withBearer } from '../auth/accessToken';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('bounded evidence and profile operations', () => {
  it.each([
    ['saved journeys', () => fetchJourneys()],
    ['permission evidence', () => fetchRlsProbe()],
    ['session receipt', () => fetchSessionReceipt()],
    ['preference update', () => updateMemoryFact('demo', 'seat', 'aisle')],
  ])('stops waiting for a stalled %s request', async (_label, operation) => {
    vi.useFakeTimers();
    let signal!: AbortSignal;
    const fetch = vi.fn((_url, init) => {
      signal = init.signal;
      return new Promise(() => {});
    });
    vi.stubGlobal('fetch', fetch);
    const assertion = expect(operation()).rejects.toMatchObject({ name: 'TimeoutError' });
    await vi.advanceTimersByTimeAsync(55_000);
    await assertion;
    expect(signal.aborted).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1); // No automatic replay of a possible write.
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('session receipt', () => {
  it('keeps an unavailable count as null rather than zero', async () => {
    const body = {
      traveler_id: 't', since: 'last 90 minutes', durable_checkpoints: false,
      lines: [{ label: 'Authorization decisions', table: 'traveler_access_audit',
        count: null, detail: 'audit counts unavailable (ref abc)', scoped: false }],
    };
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })));
    const receipt = await fetchSessionReceipt();
    expectTypeOf(receipt.lines[0].count).toEqualTypeOf<number | null>();
    expect(receipt.lines[0].count).toBeNull();
  });
});

describe('stopRuntimeSession', () => {
  it('POSTs to the journey stop-session route', async () => {
    const body = { stopped: true, runtime_session_id: 'rt-wf-1', outcome: 'stopped',
      stopped_at: '2026-10-07 10:00:00+00', stopped_during: 'waiting', last_step: null };
    const fetch = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal('fetch', fetch);

    await expect(stopRuntimeSession('jrn 1')).resolves.toEqual(body);
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/journeys\/jrn%201\/stop-session$/);
    expect(init.method).toBe('POST');
  });
});

describe('fetchHealth', () => {
  it.each([401, 403, 503])('does not mask an API failure (%s) with public liveness', async status => {
    const fetch = vi.fn(async (url: string) => new Response(
      JSON.stringify(url.endsWith('/api/health') ? { error: 'Service unavailable' } : { status: 'healthy' }),
      { status: url.endsWith('/api/health') ? status : 200 },
    ));
    vi.stubGlobal('fetch', fetch);

    await expect(fetchHealth()).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe('resolveBackendOriginFor', () => {
  it('uses and normalizes an explicit backend origin', () => {
    expect(
      resolveBackendOriginFor(' https://api.meridian.example/ ', false, {
        protocol: 'https:',
        hostname: 'app.meridian.example',
      }),
    ).toBe('https://api.meridian.example');
  });

  it('uses the FastAPI development origin when no browser location exists', () => {
    expect(resolveBackendOriginFor(undefined, false)).toBe('http://localhost:8013');
  });

  it('uses the documented backend port for a local production preview', () => {
    expect(
      resolveBackendOriginFor(undefined, false, {
        protocol: 'http:',
        hostname: '127.0.0.1',
      }),
    ).toBe('http://127.0.0.1:8013');
  });

  it('keeps deployed applications on the page origin by default', () => {
    expect(
      resolveBackendOriginFor(undefined, false, {
        protocol: 'https:',
        hostname: 'meridian.example',
      }),
    ).toBe('https://meridian.example');
  });
});

describe('healthUrlsFor', () => {
  it('probes health only on the resolved backend origin', () => {
    expect(healthUrlsFor('https://api.meridian.example/')).toEqual([
      'https://api.meridian.example/api/health',
    ]);
  });

  it('does not add a loopback fallback for deployed origins', () => {
    expect(healthUrlsFor('https://api.meridian.example')).not.toContain(
      'http://127.0.0.1:8013/health',
    );
  });
});

describe('healthOriginFor', () => {
  it('uses the configured API base when it is hosted separately', () => {
    expect(
      healthOriginFor(
        'https://api.meridian.example/api',
        'https://showcase.meridian.example',
      ),
    ).toBe('https://api.meridian.example');
  });

  it('resolves a relative API base against the page origin', () => {
    expect(
      healthOriginFor('/api', 'http://localhost:8013', 'http://localhost:5173'),
    ).toBe('http://localhost:5173');
  });

  it('keeps the backend origin for a relative API base when there is no page', () => {
    expect(healthOriginFor('/api', 'https://showcase.meridian.example'))
      .toBe('https://showcase.meridian.example');
  });

  it('sends the token to a relative API base on the page origin', () => {
    const origin = healthOriginFor('/api', 'http://localhost:8013', window.location.origin);
    setAccessTokenProvider(() => 'access.jwt');
    setBearerOrigin(origin);
    try {
      expect(new Headers(withBearer('/api/me')).get('Authorization')).toBe('Bearer access.jwt');
    } finally {
      setAccessTokenProvider(null);
      setBearerOrigin(null);
    }
  });
});
