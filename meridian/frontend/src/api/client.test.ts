import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  healthOriginFor,
  healthUrlsFor,
  resolveBackendOriginFor,
  fetchHealth,
} from './client';

afterEach(() => vi.unstubAllGlobals());

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

  it('keeps the resolved backend origin for a relative API base', () => {
    expect(
      healthOriginFor('/api', 'https://showcase.meridian.example'),
    ).toBe('https://showcase.meridian.example');
  });
});
