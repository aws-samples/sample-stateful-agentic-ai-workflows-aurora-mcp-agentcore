import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthSession, MESSAGES, type SessionDeps } from './session';
import { decoyTokens, fakeJwt, jordanTokens } from '../test/tokens';

const config = {
  domain: 'd.auth.example.test', clientId: 'client-web',
  redirectUri: 'https://s.test/showcase', logoutUri: 'https://s.test/showcase',
};

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
    values,
  };
}

function tokenResponse(
  tokens: { accessToken: string; idToken: string }, extra: Record<string, unknown> = {},
) {
  return {
    ok: true, status: 200,
    json: async () => ({
      access_token: tokens.accessToken, id_token: tokens.idToken,
      refresh_token: 'refresh-1', expires_in: 3600, ...extra,
    }),
  };
}

let clock = 0;
let timers: { callback: () => void; delay: number; id: number }[] = [];
let storage = memoryStorage();
let navigate = vi.fn();
let fetchFn = vi.fn();

function build(overrides: Partial<SessionDeps> = {}) {
  return new AuthSession({
    config, fetchFn: fetchFn as unknown as typeof fetch, now: () => clock, storage, navigate,
    schedule: (callback, delay) => {
      const id = timers.length + 1;
      timers.push({ callback, delay, id });
      return id;
    },
    cancel: handle => { timers = timers.filter(timer => timer.id !== handle); },
    ...overrides,
  });
}

async function signIn(session: AuthSession, tokens = jordanTokens()) {
  await session.startSignIn();
  const saved = JSON.parse(storage.values.get('meridian:auth:pkce')!);
  fetchFn.mockResolvedValueOnce(tokenResponse(tokens));
  await session.handleCallback(`?code=code-1&state=${saved.state}&view=proof`);
  return saved;
}

beforeEach(() => {
  clock = 1_000_000; timers = []; storage = memoryStorage(); navigate = vi.fn(); fetchFn = vi.fn();
});

describe('starting sign-in', () => {
  it('sends the browser to the hosted page with a PKCE challenge and a state', async () => {
    await build().startSignIn();
    const url = new URL(navigate.mock.calls[0][0]);
    expect(url.origin + url.pathname).toBe('https://d.auth.example.test/oauth2/authorize');
    const query = Object.fromEntries(url.searchParams);
    expect(query).toMatchObject({
      response_type: 'code', client_id: 'client-web', redirect_uri: 'https://s.test/showcase',
      scope: 'openid email profile', code_challenge_method: 'S256',
    });
    const saved = JSON.parse(storage.values.get('meridian:auth:pkce')!);
    expect(saved.state).toBe(query.state);
    expect(query.code_challenge).not.toBe(saved.verifier);
    expect(query.code_challenge).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });
});

describe('returning from the hosted page', () => {
  it('ignores an address with no sign-in result', async () => {
    expect(await build().handleCallback('?view=proof&theme=dark')).toBe(false);
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it('exchanges the code once, with the saved verifier, and signs the traveler in', async () => {
    const session = build();
    const saved = await signIn(session);
    expect(Object.fromEntries(fetchFn.mock.calls[0][1].body)).toMatchObject({
      code: 'code-1', code_verifier: saved.verifier,
    });
    expect(session.getState()).toEqual({
      status: 'signed-in', message: null,
      traveler: {
        travelerId: 'trv_meridian_demo', displayName: 'Jordan Morgan',
        avatarUrl: '/travel/jordan-morgan.jpg',
      },
    });
    expect(session.getAccessToken()).toBe(jordanTokens().accessToken);
  });

  it('shows the decoy as the decoy', async () => {
    const session = build();
    await signIn(session, decoyTokens());
    expect(session.getState().traveler)
      .toMatchObject({ travelerId: 'trv_demo_decoy', displayName: 'Jordan Lee' });
  });

  it('keeps the one-time PKCE values out of storage once they are used', async () => {
    await signIn(build());
    expect(storage.values.size).toBe(0);
  });

  it('exchanges a code only once when the effect runs twice', async () => {
    const session = build();
    await session.startSignIn();
    const { state } = JSON.parse(storage.values.get('meridian:auth:pkce')!);
    fetchFn.mockResolvedValue(tokenResponse(jordanTokens()));
    await Promise.all([
      session.handleCallback(`?code=c&state=${state}`),
      session.handleCallback(`?code=c&state=${state}`),
    ]);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(session.getState().status).toBe('signed-in');
  });

  it('refuses a return whose state does not match what this browser started', async () => {
    const session = build();
    await session.startSignIn();
    await session.handleCallback('?code=c&state=forged');
    expect(fetchFn).not.toHaveBeenCalled();
    expect(session.getState())
      .toMatchObject({ status: 'signed-out', message: MESSAGES.unverified });
  });

  it('refuses a return when this browser started nothing', async () => {
    const session = build();
    await session.handleCallback('?code=c&state=anything');
    expect(session.getState().message).toBe(MESSAGES.unverified);
  });

  it('says so when the hosted page refused the sign-in', async () => {
    const session = build();
    const search = '?error=access_denied&error_description=PreTokenGeneration+failed';
    expect(await session.handleCallback(search)).toBe(true);
    expect(session.getState()).toMatchObject({ status: 'signed-out', message: MESSAGES.refused });
  });

  it('signs out with a message when the token exchange fails', async () => {
    const session = build();
    await session.startSignIn();
    const { state } = JSON.parse(storage.values.get('meridian:auth:pkce')!);
    fetchFn.mockResolvedValueOnce({
      ok: false, status: 400, json: async () => ({ error: 'invalid_grant' }),
    });
    await session.handleCallback(`?code=c&state=${state}`);
    expect(session.getState()).toMatchObject({ status: 'signed-out', message: MESSAGES.failed });
    expect(session.getAccessToken()).toBeNull();
  });

  it('does not sign in an account whose token names no traveler', async () => {
    const session = build();
    await signIn(session, {
      accessToken: fakeJwt({ sub: 's' }), idToken: fakeJwt({ name: 'No Traveler' }),
    });
    expect(session.getState())
      .toMatchObject({ status: 'signed-out', message: MESSAGES.unlinked });
    expect(session.getAccessToken()).toBeNull();
  });
});

describe('keeping the token fresh', () => {
  it('schedules a refresh a minute before the access token expires', async () => {
    await signIn(build());
    expect(timers).toHaveLength(1);
    expect(timers[0].delay).toBe(3_600_000 - 60_000);
  });

  it('replaces the tokens when the timer fires and schedules the next refresh', async () => {
    const session = build();
    await signIn(session);
    const renewed = {
      accessToken: fakeJwt({ traveler_id: 'trv_meridian_demo', n: 2 }),
      idToken: jordanTokens().idToken,
    };
    fetchFn.mockResolvedValueOnce(tokenResponse(renewed, { refresh_token: undefined }));
    clock += 3_540_000;
    timers[0].callback();
    await vi.waitFor(() => expect(session.getAccessToken()).toBe(renewed.accessToken));
    expect(Object.fromEntries(fetchFn.mock.calls[1][1].body)).toMatchObject({
      grant_type: 'refresh_token', refresh_token: 'refresh-1',
    });
    expect(timers[timers.length - 1].delay).toBe(3_540_000);
  });

  it('signs out with a message when the refresh is refused', async () => {
    const session = build();
    await signIn(session);
    fetchFn.mockResolvedValueOnce({ ok: false, status: 400, json: async () => ({}) });
    await session.refreshNow();
    expect(session.getState()).toMatchObject({ status: 'signed-out', message: MESSAGES.ended });
    expect(session.getAccessToken()).toBeNull();
    expect(timers).toEqual([]);
  });

  it('refreshes on return to the tab only when the token is about to expire', async () => {
    const session = build();
    await signIn(session);
    await session.refreshIfDue();
    expect(fetchFn).toHaveBeenCalledTimes(1);
    clock += 3_550_000;
    fetchFn.mockResolvedValueOnce(tokenResponse(jordanTokens()));
    await session.refreshIfDue();
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });
});

describe('signing out', () => {
  it('forgets the tokens, stops the timer and ends the hosted session', async () => {
    const session = build();
    await signIn(session);
    session.signOut();
    expect(session.getAccessToken()).toBeNull();
    expect(timers).toEqual([]);
    expect(session.getState()).toEqual({ status: 'signed-out', traveler: null, message: null });
    const url = new URL(navigate.mock.calls[navigate.mock.calls.length - 1][0]);
    expect(url.origin + url.pathname).toBe('https://d.auth.example.test/logout');
    expect(Object.fromEntries(url.searchParams))
      .toEqual({ client_id: 'client-web', logout_uri: 'https://s.test/showcase' });
  });

  it('tells listeners about every change', async () => {
    const session = build();
    const listener = vi.fn();
    const unsubscribe = session.subscribe(listener);
    await signIn(session);
    const calls = listener.mock.calls.length;
    expect(calls).toBeGreaterThanOrEqual(2);
    unsubscribe();
    session.signOut();
    expect(listener).toHaveBeenCalledTimes(calls);
  });
});

function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const refused = (status: number) =>
  ({ ok: false, status, json: async () => ({ error: 'invalid_grant' }) });
const lastDelay = () => timers[timers.length - 1].delay;

describe('one refresh at a time', () => {
  it('shares one request between concurrent refreshes', async () => {
    const session = build();
    await signIn(session);
    const gate = deferred();
    fetchFn.mockReturnValueOnce(gate.promise);
    const first = session.refreshNow();
    const second = session.refreshNow();
    gate.resolve(tokenResponse(jordanTokens()));
    await Promise.all([first, second]);
    expect(fetchFn).toHaveBeenCalledTimes(2);
    expect(session.getState().status).toBe('signed-in');
  });

  it('stays signed out when sign-out happens while a refresh is in flight', async () => {
    const session = build();
    await signIn(session);
    const gate = deferred();
    fetchFn.mockReturnValueOnce(gate.promise);
    const pending = session.refreshNow();
    session.signOut();
    gate.resolve(tokenResponse(jordanTokens()));
    await pending;
    expect(session.getAccessToken()).toBeNull();
    expect(session.getState().status).toBe('signed-out');
    expect(timers).toEqual([]);
  });

  it('stays signed out when sign-out happens while the code is being exchanged', async () => {
    const session = build();
    await session.startSignIn();
    const { state } = JSON.parse(storage.values.get('meridian:auth:pkce')!);
    const gate = deferred();
    fetchFn.mockReturnValueOnce(gate.promise);
    const pending = session.handleCallback(`?code=c&state=${state}`);
    expect(session.getState().status).toBe('signing-in');
    session.signOut();
    gate.resolve(tokenResponse(jordanTokens()));
    await pending;
    expect(session.getAccessToken()).toBeNull();
    expect(session.getState()).toEqual({ status: 'signed-out', traveler: null, message: null });
  });

  it('keeps a newer session when an older refresh fails afterwards', async () => {
    const session = build();
    await signIn(session);
    const gate = deferred();
    fetchFn.mockReturnValueOnce(gate.promise);
    const stale = session.refreshNow();
    session.signOut();
    storage.values.clear();
    await session.startSignIn();
    const { state } = JSON.parse(storage.values.get('meridian:auth:pkce')!);
    fetchFn.mockResolvedValueOnce(tokenResponse(decoyTokens()));
    await session.handleCallback(`?code=code-2&state=${state}`);
    gate.resolve(refused(400));
    await stale;
    expect(session.getState()).toMatchObject({ status: 'signed-in' });
    expect(session.getAccessToken()).toBe(decoyTokens().accessToken);
  });

  it('does not start a refresh for a signed-out session or leave a message', async () => {
    const session = build();
    await session.refreshNow();
    expect(fetchFn).not.toHaveBeenCalled();
    expect(session.getState()).toEqual({ status: 'signed-out', traveler: null, message: null });
  });

  it('drops the old token when a new sign-in begins while signed in', async () => {
    const session = build();
    await signIn(session);
    storage.values.clear();
    await session.startSignIn();
    const { state } = JSON.parse(storage.values.get('meridian:auth:pkce')!);
    const gate = deferred();
    fetchFn.mockReturnValueOnce(gate.promise);
    const pending = session.handleCallback(`?code=code-2&state=${state}`);
    expect(session.getState().status).toBe('signing-in');
    expect(session.getAccessToken()).toBeNull();
    gate.resolve(tokenResponse(decoyTokens()));
    await pending;
    expect(session.getAccessToken()).toBe(decoyTokens().accessToken);
  });
});

describe('a bad expires_in', () => {
  it.each(['abc', 0, -1, '', 1e12])('never sets a zero-delay timer for %s', async value => {
    const session = build();
    await signIn(session);
    fetchFn.mockResolvedValueOnce(tokenResponse(jordanTokens(), { expires_in: value }));
    await session.refreshNow();
    expect(session.getState().status).toBe('signed-in');
    expect(lastDelay()).toBeGreaterThanOrEqual(5_000);
    expect(lastDelay()).toBeLessThanOrEqual(2_147_483_647);
  });

  it('clamps a huge lifetime to the longest timer the platform allows', async () => {
    const session = build();
    await signIn(session);
    fetchFn.mockResolvedValueOnce(tokenResponse(jordanTokens(), { expires_in: 1e12 }));
    await session.refreshNow();
    expect(lastDelay()).toBe(2_147_483_647);
  });
});

describe('a refresh that fails', () => {
  it('keeps the session through a network error while the token is valid', async () => {
    const session = build();
    await signIn(session);
    fetchFn.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    clock += 3_540_000;
    await session.refreshNow();
    expect(session.getState().status).toBe('signed-in');
    expect(session.getAccessToken()).toBe(jordanTokens().accessToken);
    expect(lastDelay()).toBe(5_000);
  });

  it('keeps the session through a server error while the token is valid', async () => {
    const session = build();
    await signIn(session);
    fetchFn.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) });
    await session.refreshNow();
    expect(session.getState().status).toBe('signed-in');
    expect(lastDelay()).toBe(5_000);
  });

  it('signs out once the access token has expired and the refresh still fails', async () => {
    const session = build();
    await signIn(session);
    fetchFn.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await session.refreshNow();
    clock += 3_600_001;
    fetchFn.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    timers[timers.length - 1].callback();
    await vi.waitFor(() => expect(session.getState().status).toBe('signed-out'));
    expect(session.getState().message).toBe(MESSAGES.ended);
    expect(session.getAccessToken()).toBeNull();
  });

  it('signs out at once when the refresh token is refused', async () => {
    const session = build();
    await signIn(session);
    fetchFn.mockResolvedValueOnce(refused(400));
    await session.refreshNow();
    expect(session.getState()).toMatchObject({ status: 'signed-out', message: MESSAGES.ended });
  });
});

describe('the start of sign-in on an insecure page', () => {
  it('says sign-in failed when there is no SubtleCrypto', async () => {
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) });
    try {
      const session = build();
      await session.startSignIn();
      expect(navigate).not.toHaveBeenCalled();
      expect(session.getState())
        .toMatchObject({ status: 'signed-out', message: MESSAGES.failed });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('pending sign-in survives noise', () => {
  it('is not erased by a forged state', async () => {
    const session = build();
    await session.startSignIn();
    const before = storage.values.get('meridian:auth:pkce');
    await session.handleCallback('?code=c&state=forged');
    expect(storage.values.get('meridian:auth:pkce')).toBe(before);
    expect(session.getState().message).toBe(MESSAGES.unverified);
  });

  it('is not erased by an error that carries a different state', async () => {
    const session = build();
    await session.startSignIn();
    const before = storage.values.get('meridian:auth:pkce');
    await session.handleCallback('?error=access_denied&state=forged');
    expect(storage.values.get('meridian:auth:pkce')).toBe(before);
  });

  it('is cleared by an error that carries its own state', async () => {
    const session = build();
    await session.startSignIn();
    const { state } = JSON.parse(storage.values.get('meridian:auth:pkce')!);
    await session.handleCallback(`?error=access_denied&state=${state}`);
    expect(storage.values.size).toBe(0);
    expect(session.getState().message).toBe(MESSAGES.refused);
  });
});

describe('revoking on sign-out', () => {
  it('asks the hosted page to revoke the refresh token before leaving', async () => {
    const session = build();
    await signIn(session);
    fetchFn.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({}) });
    session.signOut();
    const [url, init] = fetchFn.mock.calls[fetchFn.mock.calls.length - 1];
    expect(url).toBe('https://d.auth.example.test/oauth2/revoke');
    expect(Object.fromEntries(init.body)).toEqual({ client_id: 'client-web', token: 'refresh-1' });
    expect(navigate).toHaveBeenCalledTimes(2);
  });

  it('signs out even when the revoke request fails or throws', async () => {
    const session = build();
    await signIn(session);
    fetchFn.mockRejectedValueOnce(new TypeError('offline'));
    session.signOut();
    await Promise.resolve();
    expect(session.getState().status).toBe('signed-out');
    const url = new URL(navigate.mock.calls[navigate.mock.calls.length - 1][0]);
    expect(url.pathname).toBe('/logout');
  });

  it('signs out even when fetch throws synchronously', async () => {
    const session = build();
    await signIn(session);
    fetchFn.mockImplementationOnce(() => { throw new Error('boom'); });
    expect(() => session.signOut()).not.toThrow();
    expect(navigate).toHaveBeenCalled();
  });

  it('skips the revoke when there is no refresh token', () => {
    build().signOut();
    expect(fetchFn).not.toHaveBeenCalled();
  });
});

