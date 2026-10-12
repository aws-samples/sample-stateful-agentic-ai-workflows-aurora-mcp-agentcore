import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  getAccessToken, setAccessTokenProvider, setBearerOrigin, withBearer,
} from './accessToken';

const API = 'https://api.example.test';

beforeEach(() => setBearerOrigin(API));
afterEach(() => { setAccessTokenProvider(null); setBearerOrigin(null); });

describe('access token provider', () => {
  it('has no token until a session installs one', () => {
    expect(getAccessToken()).toBeNull();
    expect(withBearer(`${API}/api/me`, { 'Content-Type': 'application/json' }))
      .toEqual({ 'Content-Type': 'application/json' });
    expect(withBearer(`${API}/api/me`)).toBeUndefined();
  });

  it('adds the bearer token and keeps the headers the caller set', () => {
    setAccessTokenProvider(() => 'access.jwt');
    const headers = new Headers(withBearer(`${API}/api/me`, { 'Content-Type': 'application/json' }));
    expect(headers.get('Authorization')).toBe('Bearer access.jwt');
    expect(headers.get('Content-Type')).toBe('application/json');
  });

  it('never overwrites an Authorization header the caller set', () => {
    setAccessTokenProvider(() => 'access.jwt');
    const headers = new Headers(withBearer(`${API}/api/me`, { Authorization: 'Bearer other' }));
    expect(headers.get('Authorization')).toBe('Bearer other');
  });

  it('forgets the provider when it is removed', () => {
    setAccessTokenProvider(() => 'access.jwt');
    setAccessTokenProvider(null);
    expect(getAccessToken()).toBeNull();
  });
});

describe('where the token may go', () => {
  beforeEach(() => setAccessTokenProvider(() => 'access.jwt'));

  it.each([
    'https://other.example.test/api/me',
    'http://api.example.test/api/me',
    'https://api.example.test:8443/api/me',
    'https://api.example.test.evil.test/api/me',
    'https://user@evil.test/api/me',
    '//evil.test/api/me',
  ])('is withheld from %s', url => {
    expect(new Headers(withBearer(url)).has('Authorization')).toBe(false);
  });

  it('goes to the API origin, whatever the path', () => {
    expect(new Headers(withBearer(`${API}/api/health`)).get('Authorization'))
      .toBe('Bearer access.jwt');
  });

  it('is withheld everywhere until an API origin is set', () => {
    setBearerOrigin(null);
    expect(new Headers(withBearer(`${API}/api/me`)).has('Authorization')).toBe(false);
  });

  it('treats a relative address as the page origin', () => {
    setBearerOrigin(window.location.origin);
    expect(new Headers(withBearer('/api/me')).get('Authorization')).toBe('Bearer access.jwt');
  });
});
