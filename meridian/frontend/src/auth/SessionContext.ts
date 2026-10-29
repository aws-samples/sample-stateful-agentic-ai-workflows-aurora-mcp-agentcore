import { createContext, useContext } from 'react';
import type { SignedInTraveler } from './claims';

export interface SessionValue {
  /** Who the page is signed in as; null when there is no sign-in and the API has not said. */
  traveler: SignedInTraveler | null;
  /**
   * The traveler id the API itself reported for this caller; null until it has. The id in
   * `traveler` is only what the sign-in claims, so show this one as confirmed.
   */
  verifiedTravelerId?: string | null;
  /** True while the API has not yet answered who is calling, so writes keyed by traveler wait. */
  identityPending?: boolean;
  /** cognito: signed in on this page. api: no sign-in here, and the API named the caller. */
  source: 'cognito' | 'api' | 'none';
  /** Ends the session; null when there is no session of our own to end. */
  signOut: (() => void) | null;
}

export const SessionContext = createContext<SessionValue>({
  traveler: null, source: 'none', signOut: null,
});

export function useSession(): SessionValue {
  return useContext(SessionContext);
}
