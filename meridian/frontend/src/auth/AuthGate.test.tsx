import { StrictMode, useEffect } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getAccessToken } from './accessToken';
import { AuthGate } from './AuthGate';
import { useSession } from './SessionContext';
import { AuthSession } from './session';
import { fakeJwt, jordanTokens } from '../test/tokens';
import { fetchSessionIdentity } from '../api/client';

vi.mock('../api/client', () => ({ fetchSessionIdentity: vi.fn() }));
vi.mock('./session', async importOriginal => ({
  ...(await importOriginal<typeof import('./session')>()),
  createBrowserSession: vi.fn(),
}));
const { createBrowserSession } = await import('./session');

const config = {
  domain: 'd.auth.example.test', clientId: 'client-web',
  redirectUri: 'https://s.test/showcase', logoutUri: 'https://s.test/showcase',
};
const values = new Map<string, string>();
const storage = {
  getItem: (key: string) => values.get(key) ?? null,
  setItem: (key: string, value: string) => { values.set(key, value); },
  removeItem: (key: string) => { values.delete(key); },
};
let navigate = vi.fn();
let fetchFn = vi.fn();

function Who() {
  const { traveler, source, signOut } = useSession();
  return (
    <div>
      <span data-testid="who">
        {`${source}:${traveler?.travelerId ?? 'nobody'}:${traveler?.displayName ?? ''}`}
      </span>
      {signOut && <button type="button" onClick={signOut}>Sign out</button>}
    </div>
  );
}

const probeMounted = vi.fn();
const probeFetch = vi.fn();

/** A child that would call the API the moment it mounted. */
function Probe() {
  useEffect(() => {
    probeMounted();
    void probeFetch();
  }, []);
  return <span data-testid="probe" />;
}

function renderGuarded() {
  return render(<AuthGate config={config}><Who /><Probe /></AuthGate>);
}

async function startAndReturn(search: (state: string) => string) {
  const session = install();
  await session.startSignIn();
  const { state } = JSON.parse(values.get('meridian:auth:pkce')!);
  window.history.replaceState(null, '', `/showcase${search(state)}`);
  return session;
}

function expectWithheld() {
  expect(probeMounted).not.toHaveBeenCalled();
  expect(probeFetch).not.toHaveBeenCalled();
  expect(screen.queryByTestId('probe')).not.toBeInTheDocument();
  expect(screen.queryByTestId('who')).not.toBeInTheDocument();
}

function install() {
  const session = new AuthSession({
    config, fetchFn: fetchFn as unknown as typeof fetch, now: () => 1_000_000, storage, navigate,
    schedule: () => 1, cancel: () => undefined,
  });
  vi.mocked(createBrowserSession).mockReturnValue(session);
  return session;
}

function tokenResponse(accessToken: string, idToken: string) {
  return {
    ok: true, status: 200,
    json: async () => ({
      access_token: accessToken, id_token: idToken, refresh_token: 'r', expires_in: 3600,
    }),
  };
}

beforeEach(() => {
  values.clear(); navigate = vi.fn(); fetchFn = vi.fn();
  window.history.replaceState(null, '', '/showcase?view=proof');
});
afterEach(() => vi.clearAllMocks());

describe('AuthGate with sign-in configured', () => {
  it('shows the sign-in screen, not the app, until someone signs in', () => {
    install();
    render(<AuthGate config={config}><Who /></AuthGate>);
    expect(screen.getByRole('heading', { name: 'Sign in to Meridian' })).toBeInTheDocument();
    expect(screen.queryByTestId('who')).not.toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
  });

  it('sends the browser to the hosted page when the button is pressed', async () => {
    install();
    render(<AuthGate config={config}><Who /></AuthGate>);
    await act(async () => { screen.getByRole('button', { name: 'Sign in' }).click(); });
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(navigate.mock.calls[0][0])
      .toMatch(/^https:\/\/d\.auth\.example\.test\/oauth2\/authorize\?/);
  });

  it('completes the return, installs the token, hides the code and keeps the rest', async () => {
    const session = install();
    await session.startSignIn();
    const { state } = JSON.parse(values.get('meridian:auth:pkce')!);
    const tokens = jordanTokens();
    fetchFn.mockResolvedValue(tokenResponse(tokens.accessToken, tokens.idToken));
    window.history.replaceState(null, '', `/showcase?view=proof&code=c1&state=${state}`);
    const replaceState = vi.spyOn(window.history, 'replaceState');

    render(<AuthGate config={config}><Who /></AuthGate>);
    expect(screen.getByRole('button', { name: 'Signing you in' })).toBeDisabled();

    await waitFor(() => expect(screen.getByTestId('who'))
      .toHaveTextContent('cognito:trv_meridian_demo:Jordan Morgan'));
    expect(window.location.search).toBe('?view=proof');
    expect(replaceState).toHaveBeenCalledWith(null, '', '/showcase?view=proof');
    expect(getAccessToken()).toBe(tokens.accessToken);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    replaceState.mockRestore();
  });

  it('shows a refused sign-in as a message on the sign-in screen', async () => {
    install();
    window.history.replaceState(null, '', '/showcase?error=access_denied');
    render(<AuthGate config={config}><Who /></AuthGate>);
    expect(await screen.findByRole('alert')).toHaveTextContent('Sign-in was not completed');
    expect(window.location.search).toBe('');
  });

  it('removes the code and state from the address after a failed exchange too', async () => {
    const session = install();
    await session.startSignIn();
    const { state } = JSON.parse(values.get('meridian:auth:pkce')!);
    fetchFn.mockRejectedValue(new Error('network down'));
    window.history.replaceState(null, '', `/showcase?view=proof&code=c9&state=${state}#top`);
    render(<AuthGate config={config}><Who /></AuthGate>);
    expect(await screen.findByRole('alert')).toHaveTextContent('We could not complete sign-in');
    expect(window.location.search).toBe('?view=proof');
    expect(window.location.hash).toBe('#top');
  });

  it('never shows or logs the code, the state or a token', async () => {
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const)
      .map(method => vi.spyOn(console, method).mockImplementation(() => undefined));
    const session = install();
    await session.startSignIn();
    const { state } = JSON.parse(values.get('meridian:auth:pkce')!);
    const tokens = jordanTokens();
    fetchFn.mockResolvedValue(tokenResponse(tokens.accessToken, tokens.idToken));
    window.history.replaceState(null, '', `/showcase?code=secret-code-77&state=${state}`);

    const { container } = render(<AuthGate config={config}><Who /></AuthGate>);
    await screen.findByTestId('who');

    const page = container.innerHTML;
    [ 'secret-code-77', state, tokens.accessToken, tokens.idToken, 'r' ]
      .filter(secret => secret.length > 1)
      .forEach(secret => expect(page).not.toContain(secret));
    spies.forEach(spy => expect(spy).not.toHaveBeenCalled());
    spies.forEach(spy => spy.mockRestore());
  });

  it('signs out: forgets the token and goes to the hosted sign-out', async () => {
    const session = install();
    await session.startSignIn();
    const { state } = JSON.parse(values.get('meridian:auth:pkce')!);
    fetchFn.mockResolvedValue(
      tokenResponse(fakeJwt({ traveler_id: 'trv_x' }), fakeJwt({ name: 'X' })),
    );
    window.history.replaceState(null, '', `/showcase?code=c1&state=${state}`);
    render(<AuthGate config={config}><Who /></AuthGate>);
    await screen.findByTestId('who');

    await act(async () => { screen.getByRole('button', { name: 'Sign out' }).click(); });
    expect(getAccessToken()).toBeNull();
    expect(screen.getByRole('heading', { name: 'Sign in to Meridian' })).toBeInTheDocument();
    expect(navigate.mock.calls[navigate.mock.calls.length - 1][0])
      .toMatch(/\/logout\?client_id=client-web/);
  });
});

describe('AuthGate withholds the app until signed in', () => {
  beforeEach(() => { probeMounted.mockClear(); probeFetch.mockClear(); });

  it('mounts nothing while the token exchange is still pending', async () => {
    await startAndReturn(state => `?view=proof&code=c1&state=${state}`);
    fetchFn.mockImplementation(() => new Promise(() => undefined));
    renderGuarded();
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(1));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole('button', { name: 'Signing you in' })).toBeDisabled();
    expectWithheld();
  });

  it('mounts nothing when the token exchange fails', async () => {
    await startAndReturn(state => `?code=c2&state=${state}`);
    fetchFn.mockRejectedValue(new Error('network down'));
    renderGuarded();
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expectWithheld();
  });

  it('mounts nothing when the hosted page refuses the sign-in', async () => {
    install();
    window.history.replaceState(null, '', '/showcase?error=access_denied');
    renderGuarded();
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expectWithheld();
  });

  it('mounts the app once, only after sign-in completes', async () => {
    await startAndReturn(state => `?code=c3&state=${state}`);
    const tokens = jordanTokens();
    fetchFn.mockResolvedValue(tokenResponse(tokens.accessToken, tokens.idToken));
    renderGuarded();
    expectWithheld();
    await screen.findByTestId('who');
    expect(probeMounted).toHaveBeenCalledTimes(1);
    expect(probeFetch).toHaveBeenCalledTimes(1);
  });
});

describe('AuthGate return handling', () => {
  it('exchanges the code once under StrictMode and keeps the rest of the address', async () => {
    await startAndReturn(state => `?view=proof&code=c1&state=${state}`);
    const tokens = jordanTokens();
    fetchFn.mockResolvedValue(tokenResponse(tokens.accessToken, tokens.idToken));
    render(<StrictMode><AuthGate config={config}><Who /></AuthGate></StrictMode>);
    await waitFor(() => expect(screen.getByTestId('who'))
      .toHaveTextContent('cognito:trv_meridian_demo:Jordan Morgan'));
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(window.location.search).toBe('?view=proof');
  });

  it('never renders an error_description from the address as markup', async () => {
    install();
    const payload = encodeURIComponent('<img src=x onerror=alert(1)>');
    window.history.replaceState(
      null, '', `/showcase?view=proof&error=access_denied&error_description=${payload}`,
    );
    const { container } = render(<AuthGate config={config}><Who /></AuthGate>);
    await screen.findByRole('alert');
    expect(container.innerHTML).not.toContain('onerror');
    expect(container.querySelector('img[src="x"]')).toBeNull();
    expect(window.location.search).toBe('?view=proof');
  });

  it('recovers when finishing the return throws', async () => {
    await startAndReturn(state => `?error=access_denied&state=${state}`);
    const storageFailure = vi.spyOn(storage, 'removeItem').mockImplementation(() => {
      throw new Error('storage blocked');
    });
    render(<AuthGate config={config}><Who /></AuthGate>);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled());
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    storageFailure.mockRestore();
  });

  it('starts the hosted sign-in once however many times the button is pressed', async () => {
    install();
    const setItem = vi.spyOn(storage, 'setItem');
    render(<AuthGate config={config}><Who /></AuthGate>);
    const button = screen.getByRole('button', { name: 'Sign in' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(button).toBeDisabled();
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(setItem).toHaveBeenCalledTimes(1);
    setItem.mockRestore();
  });

  it('lets the person try again when starting sign-in fails', async () => {
    install();
    const setItem = vi.spyOn(storage, 'setItem').mockImplementation(() => {
      throw new Error('storage blocked');
    });
    render(<AuthGate config={config}><Who /></AuthGate>);
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled();
    setItem.mockRestore();
  });
});

describe('AuthGate without sign-in settings', () => {
  it('shows the app at once and then who the API says is calling', async () => {
    vi.mocked(fetchSessionIdentity)
      .mockResolvedValue({ traveler_id: 'trv_meridian_demo', authentication: 'bearer' });
    render(<AuthGate config={null}><Who /></AuthGate>);
    expect(screen.getByTestId('who')).toHaveTextContent('none:nobody');
    await waitFor(() => expect(screen.getByTestId('who'))
      .toHaveTextContent('api:trv_meridian_demo:'));
    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument();
  });

  it('still shows the app when the API cannot say', async () => {
    vi.mocked(fetchSessionIdentity).mockRejectedValue(new Error('offline'));
    render(<AuthGate config={null}><Who /></AuthGate>);
    await waitFor(() => expect(fetchSessionIdentity).toHaveBeenCalled());
    expect(screen.getByTestId('who')).toHaveTextContent('none:nobody');
  });
});
