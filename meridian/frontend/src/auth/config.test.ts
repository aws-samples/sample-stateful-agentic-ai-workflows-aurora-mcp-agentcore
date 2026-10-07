import { describe, expect, it } from 'vitest';
import { readAuthConfig } from './config';

const ORIGIN = 'https://site.example.test';

describe('readAuthConfig', () => {
  it('means no sign-in when nothing is set', () => {
    expect(readAuthConfig({}, ORIGIN)).toBeNull();
    expect(readAuthConfig({ VITE_COGNITO_DOMAIN: ' ', VITE_COGNITO_CLIENT_ID: '' }, ORIGIN))
      .toBeNull();
  });

  it('returns to the showcase route on the page origin', () => {
    expect(readAuthConfig({
      VITE_COGNITO_DOMAIN: 'meridian-x.auth.us-east-1.amazoncognito.com',
      VITE_COGNITO_CLIENT_ID: 'client-web',
    }, ORIGIN)).toEqual({
      domain: 'meridian-x.auth.us-east-1.amazoncognito.com', clientId: 'client-web',
      redirectUri: 'https://site.example.test/showcase',
      logoutUri: 'https://site.example.test/showcase',
    });
  });

  it('accepts a domain written as a URL', () => {
    const config = readAuthConfig(
      { VITE_COGNITO_DOMAIN: 'https://d.auth.example.test/', VITE_COGNITO_CLIENT_ID: 'c' },
      ORIGIN,
    );
    expect(config?.domain).toBe('d.auth.example.test');
  });

  it.each([
    [{ VITE_COGNITO_DOMAIN: 'd.example.test' }],
    [{ VITE_COGNITO_CLIENT_ID: 'client-web' }],
  ])('fails loudly when only one setting is present', env => {
    expect(() => readAuthConfig(env, ORIGIN)).toThrow(/half configured/);
  });

  it.each([
    'evil.test/path', 'user@evil.test', 'evil.test:8443', 'evil.test?x=1', 'evil .test',
    'evil.test#frag', '-bad.test', 'a..b.test', 'https://u:p@evil.test/', 'a\\b.test',
  ])('rejects the domain %s', domain => {
    expect(() => readAuthConfig(
      { VITE_COGNITO_DOMAIN: domain, VITE_COGNITO_CLIENT_ID: 'c' }, ORIGIN,
    )).toThrow(/VITE_COGNITO_DOMAIN/);
  });
});
