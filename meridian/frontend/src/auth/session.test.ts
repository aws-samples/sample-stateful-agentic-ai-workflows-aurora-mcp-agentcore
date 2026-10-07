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
