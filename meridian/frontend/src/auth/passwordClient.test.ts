import { describe, expect, it, vi } from 'vitest';
import { passwordEndpoint, passwordSignIn, refreshPasswordTokens } from './passwordClient';

const config = {
  domain: 'meridian-example.auth.us-east-1.amazoncognito.com', clientId: 'public-client',
  redirectUri: 'http://localhost:5173/showcase', logoutUri: 'http://localhost:5173/showcase',
};
const result = { AccessToken: 'access', IdToken: 'id', RefreshToken: 'refresh', ExpiresIn: 3600 };
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe('inline Cognito sign-in', () => {
  it('sends credentials only to the regional Cognito API, without browser cookies', async () => {
    const fetchFn = vi.fn().mockResolvedValue(reply({ AuthenticationResult: result }));
    const tokens = await passwordSignIn(config, ' jordan@example.test ', ' keep spaces ', fetchFn, () => 10);
    expect(fetchFn).toHaveBeenCalledWith('https://cognito-idp.us-east-1.amazonaws.com/', expect.objectContaining({
      method: 'POST', credentials: 'omit', redirect: 'error',
      headers: expect.objectContaining({ 'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth' }),
    }));
    expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual({
      ClientId: 'public-client', AuthFlow: 'USER_PASSWORD_AUTH',
      AuthParameters: { USERNAME: 'jordan@example.test', PASSWORD: ' keep spaces ' },
    });
    expect(tokens).toMatchObject({ accessToken: 'access', expiresAt: 3600010, source: 'cognito-api' });
  });

  it('never guesses a credential endpoint for a custom or lookalike host', async () => {
    const bad = { ...config, domain: 'meridian.auth.us-east-1.amazoncognito.com.evil.test' };
    const fetchFn = vi.fn();
    expect(passwordEndpoint(bad)).toBeNull();
    await expect(passwordSignIn(bad, 'user', 'password', fetchFn, Date.now)).rejects.toThrow();
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it.each(['NotAuthorizedException', 'UserNotFoundException'])(
    'does not expose account existence or service text for %s', async name => {
      const fetchFn = vi.fn().mockResolvedValue(reply({ __type: name, message: 'private details' }, 400));
      await expect(passwordSignIn(config, 'user', 'password', fetchFn, Date.now))
        .rejects.toThrow('Check your email and password and try again.');
    },
  );

  it('maps a refused Lambda trigger to the unlinked-account message, not "unavailable"', async () => {
    const fetchFn = vi.fn().mockResolvedValue(reply({
      __type: 'UserLambdaValidationException', message: 'PreTokenGeneration failed: private',
    }, 400));
    await expect(passwordSignIn(config, 'user', 'password', fetchFn, Date.now))
      .rejects.toThrow('This account has no traveler attached. Ask whoever runs this deployment');
  });

  it('does not treat an MFA or password-change challenge as a signed-in session', async () => {
    const fetchFn = vi.fn().mockResolvedValue(reply({ ChallengeName: 'SOFTWARE_TOKEN_MFA', Session: 'private' }));
    await expect(passwordSignIn(config, 'user', 'password', fetchFn, Date.now))
      .rejects.toThrow('Additional verification is required');
  });

  it('refreshes with the API and carries a rotated refresh token forward', async () => {
    const fetchFn = vi.fn().mockResolvedValue(reply({ AuthenticationResult: { ...result, RefreshToken: 'rotated' } }));
    const tokens = await refreshPasswordTokens(config, 'original', fetchFn, Date.now);
    expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual({ ClientId: 'public-client', RefreshToken: 'original' });
    expect(fetchFn.mock.calls[0][1].headers['X-Amz-Target']).toContain('GetTokensFromRefreshToken');
    expect(tokens.refreshToken).toBe('rotated');
  });

  it.each([{ ...result, AccessToken: '' }, { ...result, ExpiresIn: -1 }, {}])(
    'rejects an unusable authentication result', async authentication => {
      const fetchFn = vi.fn().mockResolvedValue(reply({ AuthenticationResult: authentication }));
      await expect(passwordSignIn(config, 'user', 'password', fetchFn, Date.now)).rejects.toThrow();
    },
  );
});
