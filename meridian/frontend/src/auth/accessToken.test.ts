import { afterEach, describe, expect, it } from 'vitest';
import { getAccessToken, setAccessTokenProvider, withBearer } from './accessToken';

afterEach(() => setAccessTokenProvider(null));

describe('access token provider', () => {
  it('has no token until a session installs one', () => {
    expect(getAccessToken()).toBeNull();
    expect(withBearer({ 'Content-Type': 'application/json' }))
      .toEqual({ 'Content-Type': 'application/json' });
    expect(withBearer()).toBeUndefined();
  });

  it('adds the bearer token and keeps the headers the caller set', () => {
    setAccessTokenProvider(() => 'access.jwt');
    const headers = new Headers(withBearer({ 'Content-Type': 'application/json' }));
    expect(headers.get('Authorization')).toBe('Bearer access.jwt');
    expect(headers.get('Content-Type')).toBe('application/json');
  });

  it('never overwrites an Authorization header the caller set', () => {
    setAccessTokenProvider(() => 'access.jwt');
    const headers = new Headers(withBearer({ Authorization: 'Bearer other' }));
    expect(headers.get('Authorization')).toBe('Bearer other');
  });

  it('forgets the provider when it is removed', () => {
    setAccessTokenProvider(() => 'access.jwt');
    setAccessTokenProvider(null);
    expect(getAccessToken()).toBeNull();
  });
});
