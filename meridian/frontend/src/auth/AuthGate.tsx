import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { fetchSessionIdentity } from '../api/client';
import { setUnauthorizedHandler } from '../api/request';
import { setAccessTokenProvider } from './accessToken';
import type { SignedInTraveler } from './claims';
import type { AuthConfig } from './config';
import { SessionContext } from './SessionContext';
import { createBrowserSession } from './session';
import { SignInScreen } from './SignInScreen';

const SIGN_IN_RESULT_PARAMS = ['code', 'state', 'error', 'error_description'];

const hasSignInResult = (search: string): boolean => {
  const params = new URLSearchParams(search);
  return params.has('code') || params.has('error');
};

/** The address without the sign-in result, so a reload or a copied link never replays it. */
function withoutSignInResult(location: Location): string {
  const params = new URLSearchParams(location.search);
  SIGN_IN_RESULT_PARAMS.forEach(name => params.delete(name));
  const query = params.toString();
  return `${location.pathname}${query ? `?${query}` : ''}${location.hash}`;
}

/**
 * Asks the API who it believes is signed in. The sign-in claims are unverified hints; this answer
 * is what the page may present as confirmed. A failed lookup leaves the claim unconfirmed.
 */
function useVerifiedTravelerId(signedInId: string | null): string | null {
  const [verified, setVerified] = useState<{ for: string; id: string } | null>(null);
  useEffect(() => {
    if (!signedInId) return undefined;
    const controller = new AbortController();
    Promise.resolve()
      .then(() => fetchSessionIdentity(controller.signal))
      .then(identity => setVerified({ for: signedInId, id: identity.traveler_id }))
      // Unconfirmed is a state the panels show; the failure itself needs no second report here.
      .catch(() => undefined);
    return () => controller.abort();
  }, [signedInId]);
  return verified && verified.for === signedInId ? verified.id : null;
}

function CognitoGate({ config, children }: { config: AuthConfig; children: ReactNode }) {
  const [session] = useState(() => createBrowserSession(config));
  const [returning, setReturning] = useState(() => hasSignInResult(window.location.search));
  const [starting, setStarting] = useState(false);
  const state = useSyncExternalStore(session.subscribe, session.getState);

  // A new state, such as the message after a failed start, lets the button work again.
  useEffect(() => setStarting(false), [state]);

  useEffect(() => {
    setAccessTokenProvider(session.getAccessToken);
    return () => setAccessTokenProvider(null);
  }, [session]);

  // An expired token is refreshed once; a refusal after that signs the person out.
  useEffect(() => {
    setUnauthorizedHandler({
      refresh: () => session.refreshNow(),
      signOut: () => session.signOut(),
    });
    return () => setUnauthorizedHandler(null);
  }, [session]);

  const signedInId = state.status === 'signed-in' ? state.traveler?.travelerId ?? null : null;
  const verifiedTravelerId = useVerifiedTravelerId(signedInId);

  useEffect(() => {
    let live = true;
    Promise.resolve()
      .then(() => session.handleCallback(window.location.search))
      .catch(() => false)
      .then(handled => {
        if (!live) return;
        if (handled) window.history.replaceState(null, '', withoutSignInResult(window.location));
        setReturning(false);
      });
    return () => { live = false; };
  }, [session]);

  useEffect(() => {
    const refreshOnReturn = () => { if (!document.hidden) void session.refreshIfDue(); };
    document.addEventListener('visibilitychange', refreshOnReturn);
    return () => document.removeEventListener('visibilitychange', refreshOnReturn);
  }, [session]);

  if (state.status === 'signed-in') {
    return (
      <SessionContext.Provider
        value={{
          traveler: state.traveler, verifiedTravelerId,
          source: 'cognito', signOut: () => session.signOut(),
        }}
      >
        {children}
      </SessionContext.Provider>
    );
  }
  return (
    <SignInScreen
      message={state.message}
      busy={returning || starting || state.status === 'signing-in'}
      onSignIn={() => { setStarting(true); void session.startSignIn(); }}
    />
  );
}

/**
 * A build without sign-in settings: the API decides who is calling. The page asks it once and
 * shows the answer. When the API cannot say, the page still runs and asks for no traveler by name.
 */
function ApiIdentityGate({ children }: { children: ReactNode }) {
  const [traveler, setTraveler] = useState<SignedInTraveler | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchSessionIdentity(controller.signal)
      .then(identity => setTraveler({
        travelerId: identity.traveler_id, displayName: null, avatarUrl: null,
      }))
      // An unreachable API is an expected state here: every panel reports it on its own.
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  return (
    <SessionContext.Provider
      value={{
        traveler, verifiedTravelerId: traveler?.travelerId ?? null,
        source: traveler ? 'api' : 'none', signOut: null,
      }}
    >
      {children}
    </SessionContext.Provider>
  );
}

/** Puts the showcase behind sign-in when the build has the settings for it. */
export function AuthGate({ config, children }: { config: AuthConfig | null; children: ReactNode }) {
  return config
    ? <CognitoGate config={config}>{children}</CognitoGate>
    : <ApiIdentityGate>{children}</ApiIdentityGate>;
}
