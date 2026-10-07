import { describe, expect, it } from 'vitest';
import { assertSignInConfigured } from './requireSignIn.mjs';

describe('assertSignInConfigured', () => {
  const both = { VITE_COGNITO_DOMAIN: 'd.auth.example.test', VITE_COGNITO_CLIENT_ID: 'client-web' };

  it('allows any build when sign-in is not required', () => {
    expect(() => assertSignInConfigured({})).not.toThrow();
    expect(() => assertSignInConfigured({ VITE_REQUIRE_SIGN_IN: '0' })).not.toThrow();
    expect(() => assertSignInConfigured({ VITE_REQUIRE_SIGN_IN: '' })).not.toThrow();
  });

  it('allows a required build that has both settings', () => {
    expect(() => assertSignInConfigured({ ...both, VITE_REQUIRE_SIGN_IN: '1' })).not.toThrow();
  });

  it.each([
    ['both', {}],
    ['the domain', { VITE_COGNITO_CLIENT_ID: 'client-web' }],
    ['the client id', { VITE_COGNITO_DOMAIN: 'd.auth.example.test' }],
    ['blank values', { VITE_COGNITO_DOMAIN: ' ', VITE_COGNITO_CLIENT_ID: '' }],
  ])('fails a required build missing %s, naming the variables but no values', (_, env) => {
    const attempt = () => assertSignInConfigured({ ...env, VITE_REQUIRE_SIGN_IN: '1' });
    expect(attempt).toThrow(/VITE_COGNITO_DOMAIN/);
    expect(attempt).toThrow(/VITE_COGNITO_CLIENT_ID/);
    expect(attempt).toThrow(/VITE_REQUIRE_SIGN_IN/);
    expect(attempt).not.toThrow(/client-web|auth\.example/);
  });
});
