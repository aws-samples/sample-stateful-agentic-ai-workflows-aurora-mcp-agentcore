import { LogOut } from 'lucide-react';
import { useSession } from '../../auth/SessionContext';
import type { TravelerIdentity } from '../lib/travelerIdentity';
import { TravelerAvatar } from './TravelerAvatar';
import './headerAccount.css';

/**
 * The signed-in traveler and a labelled Sign out button, in the header on every surface.
 * It renders nothing when this build has no sign-in of its own.
 */
export function HeaderAccount({ traveler }: { traveler: TravelerIdentity }) {
  const { signOut } = useSession();
  if (!signOut) return null;
  return (
    <div className="mds-header-account" role="group" aria-label="Account">
      <span className="mds-header-avatar" aria-hidden="true">
        <TravelerAvatar traveler={traveler} width={28} height={28} />
      </span>
      <span className="mds-header-name">{traveler.name}</span>
      <button type="button" className="mds-header-signout" onClick={signOut}>
        <LogOut size={15} aria-hidden="true" />
        Sign out
      </button>
    </div>
  );
}
