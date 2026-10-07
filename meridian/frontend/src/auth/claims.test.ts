import { describe, expect, it } from 'vitest';
import { decodeJwtPayload, travelerFromTokens } from './claims';
import { decoyTokens, fakeJwt, jordanTokens } from '../test/tokens';

describe('claims', () => {
  it('reads a payload and refuses what is not a token', () => {
    expect(decodeJwtPayload(fakeJwt({ a: 1 }))).toEqual({ a: 1 });
    expect(decodeJwtPayload('nope')).toBeNull();
    expect(decodeJwtPayload('a.!!!.c')).toBeNull();
    expect(decodeJwtPayload(`a.${btoa('[1]')}.c`)).toBeNull();
  });

  it('names the traveler from the access token and the person from the ID token', () => {
    const { accessToken, idToken } = jordanTokens();
    expect(travelerFromTokens(accessToken, idToken)).toEqual({
      travelerId: 'trv_meridian_demo', displayName: 'Jordan Morgan',
      avatarUrl: '/travel/jordan-morgan.jpg',
    });
  });

  it('gives the decoy initials, not Jordan Morgan\'s photo', () => {
    const { accessToken, idToken } = decoyTokens();
    expect(travelerFromTokens(accessToken, idToken)).toEqual({
      travelerId: 'trv_demo_decoy', displayName: 'Jordan Lee', avatarUrl: null,
    });
  });

  it('falls back to the email, then to nothing, for the display name', () => {
    const access = fakeJwt({ traveler_id: 'trv_x' });
    expect(travelerFromTokens(access, fakeJwt({ email: 'x@example.test' }))?.displayName)
      .toBe('x@example.test');
    expect(travelerFromTokens(access, null)?.displayName).toBeNull();
  });

  it('has no traveler when the access token carries no traveler claim', () => {
    expect(travelerFromTokens(fakeJwt({ sub: 's' }), null)).toBeNull();
    expect(travelerFromTokens(fakeJwt({ traveler_id: '  ' }), null)).toBeNull();
  });

  it.each([
    ['https://photos.example.test/a.png', 'https://photos.example.test/a.png'],
    ['/travel/a.jpg', '/travel/a.jpg'],
    ['http://photos.example.test/a.png', null],
    ['javascript:alert(1)', null],
    ['data:image/png;base64,AAAA', null],
    ['//evil.test/a.png', null],
    ['/\\evil.test/a.png', null],
    ['relative/a.png', null],
  ])('keeps only https or same-origin picture %s', (picture, expected) => {
    const access = fakeJwt({ traveler_id: 'trv_x' });
    expect(travelerFromTokens(access, fakeJwt({ picture }))?.avatarUrl).toBe(expected);
  });
});
