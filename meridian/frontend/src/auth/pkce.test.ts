import { describe, expect, it } from 'vitest';
import { base64Url, challengeFor, createPkce, randomToken } from './pkce';

describe('PKCE', () => {
  it('derives the RFC 7636 appendix B challenge', async () => {
    expect(await challengeFor('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'))
      .toBe('E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
  });

  it('encodes without padding or URL-unsafe characters', () => {
    expect(base64Url(new Uint8Array([251, 255, 254]))).toBe('-__-');
  });

  it('makes long, URL-safe, different verifiers', async () => {
    const first = await createPkce();
    const second = await createPkce();
    expect(first.verifier).toMatch(/^[A-Za-z0-9_-]{43,128}$/);
    expect(first.verifier).not.toBe(second.verifier);
    expect(first.challenge).toBe(await challengeFor(first.verifier));
  });

  it('draws state values from the random source', () => {
    expect(randomToken(16)).toMatch(/^[A-Za-z0-9_-]{22}$/);
  });
});
