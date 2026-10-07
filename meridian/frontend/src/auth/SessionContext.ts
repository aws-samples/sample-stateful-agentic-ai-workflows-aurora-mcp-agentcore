import { createContext, useContext } from 'react';
import type { SignedInTraveler } from './claims';

export interface SessionValue {
  /** Who the page is signed in as; null when there is no sign-in and the API has not said. */
  traveler: SignedInTraveler | null;
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
