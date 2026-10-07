/** Unsigned tokens for tests of code that only reads claims. The API verifies real signatures. */
function segment(value: unknown): string {
  return btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function fakeJwt(payload: Record<string, unknown>): string {
  return `${segment({ alg: 'RS256', kid: 'test' })}.${segment(payload)}.signature`;
}

export const jordanTokens = () => ({
  accessToken: fakeJwt({
    sub: 'sub-jordan', traveler_id: 'trv_meridian_demo', token_use: 'access',
  }),
  idToken: fakeJwt({
    sub: 'sub-jordan', name: 'Jordan Morgan', email: 'jordan.morgan@example.com',
    picture: '/travel/jordan-morgan.jpg', token_use: 'id',
  }),
});

export const decoyTokens = () => ({
  accessToken: fakeJwt({ sub: 'sub-decoy', traveler_id: 'trv_demo_decoy', token_use: 'access' }),
  idToken: fakeJwt({
    sub: 'sub-decoy', name: 'Jordan Lee', email: 'jordan.lee@example.com', token_use: 'id',
  }),
});
