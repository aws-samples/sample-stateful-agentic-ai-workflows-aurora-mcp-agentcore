import { LogOut } from 'lucide-react';
import { useSession } from '../../auth/SessionContext';
import { travelBriefLabel, type TravelerIdentity } from '../lib/travelerIdentity';
import { TravelerAvatar } from './TravelerAvatar';
import './headerAccount.css';

/**
 * The signed-in traveler and a labelled Sign out button, in the header on every surface.
 * It renders nothing when this build has no sign-in of its own. With onOpenTravelBrief, the
 * traveler is a button to their travel brief.
 */
export function HeaderAccount({ traveler, onOpenTravelBrief }: {
  traveler: TravelerIdentity; onOpenTravelBrief?: () => void;
}) {
  const { signOut } = useSession();
  if (!signOut) return null;
  const identity = (
    <>
      <span className="mds-header-avatar" aria-hidden="true">
        <TravelerAvatar traveler={traveler} width={28} height={28} />
      </span>
      <span className="mds-header-name">{traveler.name}</span>
    </>
  );
  return (
    <div className="mds-header-account" role="group" aria-label="Account">
      {onOpenTravelBrief
        ? (
          <button
            type="button" className="mds-header-identity" onClick={onOpenTravelBrief}
            aria-label={travelBriefLabel(traveler)}
          >
            {identity}
          </button>
        )
        : <span className="mds-header-identity">{identity}</span>}
      <button type="button" className="mds-header-signout" onClick={signOut}>
        <LogOut size={15} aria-hidden="true" />
        Sign out
      </button>
    </div>
  );
}
