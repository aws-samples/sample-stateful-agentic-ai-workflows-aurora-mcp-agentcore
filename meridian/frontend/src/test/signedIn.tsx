import type { ReactNode } from 'react';
import { SessionContext } from '../auth/SessionContext';
import type { TravelerIdentity } from '../showcase/lib/travelerIdentity';

/** Jordan Morgan as the showcase describes a signed-in traveler. */
export const JORDAN_IDENTITY: TravelerIdentity = {
  id: 'trv_meridian_demo', name: 'Jordan Morgan', initials: 'JM', avatarUrl: null, idVerified: true,
};

/** A signed-in traveler whose name is not Jordan, for proving no copy names anyone else. */
export const ALEX_IDENTITY: TravelerIdentity = {
  id: 'trv_alex', name: 'Alex Lee', initials: 'AL', avatarUrl: null, idVerified: true,
};

/** Nobody has said who is using the page yet. */
export const UNKNOWN_IDENTITY: TravelerIdentity = {
  id: null, name: 'Your account', initials: 'YA', avatarUrl: null, idVerified: false,
};

/** A hook or component wrapper that signs the test in as a traveler. */
export function signedInAs(
  travelerId: string, displayName: string | null = null, verifiedTravelerId: string | null = null,
) {
  return function SignedIn({ children }: { children: ReactNode }) {
    return (
      <SessionContext.Provider
        value={{
          traveler: { travelerId, displayName, avatarUrl: null },
          verifiedTravelerId,
          source: 'cognito',
          signOut: null,
        }}
      >
        {children}
      </SessionContext.Provider>
    );
  };
}
