import type { SignedInTraveler } from '../../auth/claims';

/** Who the showcase says is using it: one object for every name or photo of that person. */
export interface TravelerIdentity {
  /** The traveler id, when known; null while this build has no sign-in and the API has not said. */
  id: string | null;
  name: string;
  initials: string;
  /** A photo to show; null shows the initials. */
  avatarUrl: string | null;
}

export const UNKNOWN_TRAVELER_NAME = 'Your account';

/** The accessible name of the button that opens the traveler's brief. */
export function travelBriefLabel(traveler: TravelerIdentity): string {
  return traveler.name === UNKNOWN_TRAVELER_NAME
    ? 'Open your travel brief'
    : `Open ${traveler.name} travel brief`;
}

export function initialsOf(name: string): string {
  const letters = name.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0].toUpperCase());
  return letters.join('') || 'M';
}

/**
 * The signed-in person's name comes from their sign-in; failing that, from the Aurora profile the
 * API returned for them; failing that, a neutral label. Nothing here names a particular person.
 */
export function travelerIdentity(
  signedIn: SignedInTraveler | null, profileName?: string | null,
): TravelerIdentity {
  const name = signedIn?.displayName ?? (profileName?.trim() || UNKNOWN_TRAVELER_NAME);
  return {
    id: signedIn?.travelerId ?? null,
    name,
    initials: initialsOf(name),
    avatarUrl: signedIn?.avatarUrl ?? null,
  };
}
