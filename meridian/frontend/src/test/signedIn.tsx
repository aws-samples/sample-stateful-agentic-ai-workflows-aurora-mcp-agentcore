import type { ReactNode } from 'react';
import { SessionContext } from '../auth/SessionContext';
import type { TravelerIdentity } from '../showcase/lib/travelerIdentity';

/** Jordan Morgan as the showcase describes a signed-in traveler. */
export const JORDAN_IDENTITY: TravelerIdentity = {
  id: 'trv_meridian_demo', name: 'Jordan Morgan', initials: 'JM', avatarUrl: null,
};

/** A hook or component wrapper that signs the test in as a traveler. */
export function signedInAs(travelerId: string, displayName: string | null = null) {
  return function SignedIn({ children }: { children: ReactNode }) {
    return (
      <SessionContext.Provider
        value={{
          traveler: { travelerId, displayName, avatarUrl: null },
          source: 'cognito',
          signOut: null,
        }}
      >
        {children}
      </SessionContext.Provider>
    );
  };
}
