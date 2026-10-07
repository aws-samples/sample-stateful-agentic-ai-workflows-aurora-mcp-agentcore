import type { TravelerIdentity } from '../lib/travelerIdentity';
import './travelerAvatar.css';

/** The signed-in photo, or initials when there is none. Decorative: the name sits beside it. */
export function TravelerAvatar({ traveler, width, height }: {
  traveler: TravelerIdentity; width: number; height: number;
}) {
  return traveler.avatarUrl
    ? <img src={traveler.avatarUrl} alt="" width={width} height={height} loading="lazy" />
    : <span className="mds-traveler-initials" aria-hidden="true">{traveler.initials}</span>;
}
