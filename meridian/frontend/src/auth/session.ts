import type { AuthConfig } from './config';
import { travelerFromTokens, type SignedInTraveler } from './claims';
import { createPkce, randomToken } from './pkce';
import {
  exchangeCode, isRefusal, refreshTokens, revokeRefreshToken, type TokenSet,
} from './tokenClient';

export type SessionStatus = 'signed-out' | 'signing-in' | 'signed-in';

export interface SessionState {
  status: SessionStatus;
  traveler: SignedInTraveler | null;
  /** Why the person is signed out, when it was not their choice. */
  message: string | null;
}

export interface SessionDeps {
  config: AuthConfig;
  fetchFn: typeof fetch;
  now: () => number;
  /** sessionStorage: it holds only the one-time PKCE values across the redirect, never a token. */
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
  navigate: (url: string) => void;
  schedule: (callback: () => void, delayMs: number) => unknown;
  cancel: (handle: unknown) => void;
}

const PKCE_KEY = 'meridian:auth:pkce';
const SCOPES = 'openid email profile';
const REFRESH_MARGIN_MS = 60_000;
const MIN_REFRESH_DELAY_MS = 5_000;
/** The longest delay setTimeout honors; a larger one fires at once. */
const MAX_TIMER_DELAY_MS = 2 ** 31 - 1;

export const MESSAGES = {
  refused: 'Sign-in was not completed. Try again, or ask the presenter to check your access.',
  unverified: 'Sign-in could not be verified. Try again.',
  failed: 'We could not complete sign-in. Try again.',
  unlinked: 'This account has no traveler attached. Ask the presenter to check your access.',
  ended: 'Your session ended. Sign in again.',
} as const;

const SIGNED_OUT: SessionState = { status: 'signed-out', traveler: null, message: null };

/**
 * The browser side of sign-in: authorization code with PKCE against the Cognito hosted page.
 *
 * Tokens live only in this object's memory. A reload signs the page out locally, and the hosted
 * page's own session lets the person back in with one click. The access token is refreshed a
 * minute before it expires.
 */
export class AuthSession {
  private state: SessionState = SIGNED_OUT;
  private tokens: TokenSet | null = null;
  private timer: unknown = null;
  private inflightRefresh: Promise<void> | null = null;
  /** Bumped whenever the session ends or a new sign-in starts, so late answers can be ignored. */
  private generation = 0;
  private readonly listeners = new Set<() => void>();
  private readonly callbacks = new Map<string, Promise<boolean>>();

  constructor(private readonly deps: SessionDeps) {}

  getState = (): SessionState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  getAccessToken = (): string | null => this.tokens?.accessToken ?? null;

  /** Send the browser to the hosted sign-in page. */
  async startSignIn(): Promise<void> {
    const { config } = this.deps;
    try {
      const { verifier, challenge } = await createPkce();
      const state = randomToken(16);
      this.deps.storage.setItem(PKCE_KEY, JSON.stringify({ state, verifier }));
      const query = new URLSearchParams({
        response_type: 'code', client_id: config.clientId, redirect_uri: config.redirectUri,
        scope: SCOPES, state, code_challenge: challenge, code_challenge_method: 'S256',
      });
      this.deps.navigate(`https://${config.domain}/oauth2/authorize?${query}`);
    } catch {
      this.signedOut(MESSAGES.failed);
    }
  }

  /**
   * Finish sign-in when the browser comes back from the hosted page.
   *
   * @returns true when the address carried a sign-in result, whether it worked or not. The same
   * code is exchanged once however many times this is called, because React may run the effect
   * twice.
   */
  handleCallback(search: string): Promise<boolean> {
    const params = new URLSearchParams(search);
    const code = params.get('code');
    if (params.get('error')) {
      this.discardPkceFor(params.get('state'));
      this.signedOut(MESSAGES.refused);
      return Promise.resolve(true);
    }
    if (!code) return Promise.resolve(false);
    const pending = this.callbacks.get(code) ?? this.completeSignIn(code, params.get('state'));
    this.callbacks.set(code, pending);
    return pending;
  }

  /** Refresh now when the access token is within a minute of expiring. */
  async refreshIfDue(): Promise<void> {
    if (this.tokens && this.tokens.expiresAt - this.deps.now() <= REFRESH_MARGIN_MS) {
      await this.refreshNow();
    }
  }

  /**
   * Refresh the tokens. Callers that arrive while a refresh is running share it, so one expiry
   * never sends two requests.
   */
  refreshNow(): Promise<void> {
    if (!this.tokens) return Promise.resolve();
    if (!this.inflightRefresh) {
      const running = this.runRefresh().finally(() => {
        if (this.inflightRefresh === running) this.inflightRefresh = null;
      });
      this.inflightRefresh = running;
    }
    return this.inflightRefresh;
  }

  /**
   * Forget the tokens and end the hosted page's session too.
   *
   * The refresh token is revoked first with a best-effort request: its failure is ignored and
   * never delays or blocks sign-out.
   */
  signOut(): void {
    const { config } = this.deps;
    this.revokeQuietly(this.tokens?.refreshToken ?? null);
    this.signedOut(null);
    const query = new URLSearchParams({ client_id: config.clientId, logout_uri: config.logoutUri });
    this.deps.navigate(`https://${config.domain}/logout?${query}`);
  }

  private revokeQuietly(refreshToken: string | null): void {
    if (!refreshToken) return;
    try {
      void Promise.resolve(revokeRefreshToken(this.deps.config, refreshToken, this.deps.fetchFn))
        .catch(() => undefined);
    } catch {
      // Best effort: a failed revoke must not stop the person from signing out.
    }
  }

  private async runRefresh(): Promise<void> {
    const generation = this.generation;
    const refreshToken = this.tokens?.refreshToken;
    if (!refreshToken) {
      this.signedOut(MESSAGES.ended);
      return;
    }
    try {
      const tokens = await refreshTokens(
        this.deps.config, refreshToken, this.deps.fetchFn, this.deps.now,
      );
      if (generation === this.generation) this.applyTokens(tokens);
    } catch (error) {
      if (generation === this.generation) this.handleRefreshFailure(error);
    }
  }

  /** A refusal ends the session; a hiccup retries until the access token has really expired. */
  private handleRefreshFailure(error: unknown): void {
    const expired = !this.tokens || this.tokens.expiresAt <= this.deps.now();
    if (isRefusal(error) || expired) {
      this.signedOut(MESSAGES.ended);
      return;
    }
    this.cancelTimer();
    this.timer = this.deps.schedule(() => { void this.refreshNow(); }, MIN_REFRESH_DELAY_MS);
  }

  private async completeSignIn(code: string, returnedState: string | null): Promise<boolean> {
    const saved = this.readPkce();
    if (!saved || !returnedState || saved.state !== returnedState) {
      this.signedOut(MESSAGES.unverified);
      return true;
    }
    this.deps.storage.removeItem(PKCE_KEY);
    if (this.tokens) this.signedOut(null);
    else this.invalidate();
    const generation = this.generation;
    this.setState({ status: 'signing-in', traveler: null, message: null });
    try {
      const tokens = await exchangeCode(
        this.deps.config, code, saved.verifier, this.deps.fetchFn, this.deps.now,
      );
      if (generation === this.generation) this.applyTokens(tokens);
    } catch {
      if (generation === this.generation) this.signedOut(MESSAGES.failed);
    }
    return true;
  }

  private discardPkceFor(returnedState: string | null): void {
    const saved = this.readPkce();
    if (saved && saved.state === returnedState) this.deps.storage.removeItem(PKCE_KEY);
  }

  private readPkce(): { state: string; verifier: string } | null {
    try {
      const parsed = JSON.parse(this.deps.storage.getItem(PKCE_KEY) ?? 'null');
      return typeof parsed?.state === 'string' && typeof parsed?.verifier === 'string'
        ? parsed : null;
    } catch {
      return null;
    }
  }

  private applyTokens(tokens: TokenSet): void {
    const traveler = travelerFromTokens(tokens.accessToken, tokens.idToken);
    if (!traveler) {
      this.signedOut(MESSAGES.unlinked);
      return;
    }
    this.tokens = tokens;
    this.setState({ status: 'signed-in', traveler, message: null });
    this.scheduleRefresh(tokens);
  }

  private scheduleRefresh(tokens: TokenSet): void {
    this.cancelTimer();
    const wanted = tokens.expiresAt - this.deps.now() - REFRESH_MARGIN_MS;
    const delay = Math.min(Math.max(wanted, MIN_REFRESH_DELAY_MS), MAX_TIMER_DELAY_MS);
    this.timer = this.deps.schedule(() => { void this.refreshNow(); }, delay);
  }

  private cancelTimer(): void {
    if (this.timer !== null) this.deps.cancel(this.timer);
    this.timer = null;
  }

  private invalidate(): void {
    this.generation += 1;
    this.tokens = null;
    this.inflightRefresh = null;
    this.cancelTimer();
  }

  private signedOut(message: string | null): void {
    this.invalidate();
    this.setState({ ...SIGNED_OUT, message });
  }

  private setState(next: SessionState): void {
    this.state = next;
    this.listeners.forEach(listener => listener());
  }
}

/** A session wired to the real browser. */
export function createBrowserSession(config: AuthConfig): AuthSession {
  return new AuthSession({
    config,
    fetchFn: (input, init) => fetch(input, init),
    now: () => Date.now(),
    storage: window.sessionStorage,
    navigate: url => window.location.assign(url),
    schedule: (callback, delayMs) => window.setTimeout(callback, delayMs),
    cancel: handle => window.clearTimeout(handle as number),
  });
}
