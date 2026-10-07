import { describe, expect, it, vi } from 'vitest';
import { AuthError, exchangeCode, refreshTokens } from './tokenClient';

const config = {
  domain: 'd.auth.example.test', clientId: 'client-web',
  redirectUri: 'https://s.test/showcase', logoutUri: 'https://s.test/showcase',
};
const reply = (body: unknown, status = 200) =>
  vi.fn().mockResolvedValue({ ok: status < 400, status, json: async () => body });

describe('token client', () => {
  it('trades the code and verifier at the hosted token endpoint', async () => {
    const fetchFn = reply({
      access_token: 'a', id_token: 'i', refresh_token: 'r', expires_in: 3600,
    });
    const tokens = await exchangeCode(config, 'code-1', 'verifier-1', fetchFn, () => 1_000);
    expect(tokens).toEqual({
      accessToken: 'a', idToken: 'i', refreshToken: 'r', expiresAt: 1_000 + 3_600_000,
    });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe('https://d.auth.example.test/oauth2/token');
    expect(init.method).toBe('POST');
    expect(Object.fromEntries(init.body)).toEqual({
      client_id: 'client-web', grant_type: 'authorization_code', code: 'code-1',
      redirect_uri: 'https://s.test/showcase', code_verifier: 'verifier-1',
    });
  });

  it('keeps the refresh token when the refresh response omits it', async () => {
    const fetchFn = reply({ access_token: 'a2', id_token: 'i2', expires_in: 3600 });
    const tokens = await refreshTokens(config, 'r1', fetchFn, () => 0);
    expect(tokens.refreshToken).toBe('r1');
    expect(Object.fromEntries(fetchFn.mock.calls[0][1].body)).toMatchObject({
      grant_type: 'refresh_token', refresh_token: 'r1',
    });
  });

  it('turns a refusal into an AuthError that carries no token', async () => {
    await expect(exchangeCode(config, 'c', 'v', reply({ error: 'invalid_grant' }, 400), () => 0))
      .rejects.toThrow(AuthError);
  });

  it('refuses a success response without an access token', async () => {
    await expect(refreshTokens(config, 'r', reply({}), () => 0)).rejects.toThrow(AuthError);
  });
});
