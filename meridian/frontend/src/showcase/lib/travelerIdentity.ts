import type { SignedInTraveler } from '../../auth/claims';

/** Who the showcase says is using it: one object for every name or photo of that person. */
export interface TravelerIdentity {
  /** The traveler id, when known; null while this build has no sign-in and the API has not said. */
  id: string | null;
  /** True once the API itself confirmed the id; false while it is only a claim in the sign-in. */
  idVerified: boolean;
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

const isEmail = (name: string) => name.includes('@');

/** Up to two capital initials; an email stands in by the words of its part before the @. */
export function initialsOf(name: string): string {
  const spoken = isEmail(name) ? name.split('@')[0] : name;
  const parts = spoken.split(isEmail(name) ? /[\s._+-]+/ : /\s+/).filter(Boolean);
  const letters = parts.slice(0, 2).map(part => Array.from(part)[0].toUpperCase());
  return letters.join('') || 'M';
}

/**
 * The first name to use in a sentence, or null when the account has no real name yet (the
 * neutral label, or an email) and the sentence should say "you" instead.
 */
export function firstNameOf(traveler: TravelerIdentity): string | null {
  if (traveler.name === UNKNOWN_TRAVELER_NAME || isEmail(traveler.name)) return null;
  return traveler.name.split(/\s+/).find(Boolean) ?? null;
}

/**
 * The signed-in person's name comes from their sign-in; failing that, from the Aurora profile the
 * API returned for them; failing that, a neutral label. Nothing here names a particular person.
 * An email that stands in for a name yields to the profile name.
 *
 * The id is the one the API confirmed when it has, else the one in the sign-in claims, which
 * `idVerified` marks as unconfirmed.
 */
export function travelerIdentity(
  signedIn: SignedInTraveler | null,
  profileName?: string | null,
  verifiedId?: string | null,
): TravelerIdentity {
  const claimed = signedIn?.displayName ?? null;
  const profile = profileName?.trim() || null;
  const name = (claimed && !isEmail(claimed) ? claimed : null) ?? profile ?? claimed
    ?? UNKNOWN_TRAVELER_NAME;
  return {
    id: verifiedId ?? signedIn?.travelerId ?? null,
    idVerified: Boolean(verifiedId),
    name,
    initials: initialsOf(name),
    avatarUrl: signedIn?.avatarUrl ?? null,
  };
}
