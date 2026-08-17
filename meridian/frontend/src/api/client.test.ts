import { describe, expect, it } from 'vitest';

import { resolveBackendOriginFor } from './client';

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
    expect(resolveBackendOriginFor(undefined, false)).toBe('http://localhost:8000');
  });

  it('uses port 8000 for a local production preview', () => {
    expect(
      resolveBackendOriginFor(undefined, false, {
        protocol: 'http:',
        hostname: '127.0.0.1',
      }),
    ).toBe('http://127.0.0.1:8000');
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
