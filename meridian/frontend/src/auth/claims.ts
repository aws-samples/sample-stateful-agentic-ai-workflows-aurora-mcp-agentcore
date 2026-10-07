/**
 * Read a token's claims for display. This does not verify anything: the API verifies the
 * access token on every request, and what the page shows is only a convenience.
 */
export function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const part = token.split('.')[1];
  if (!part) return null;
  try {
    const base64 = part
      .replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(part.length / 4) * 4, '=');
    const bytes = Uint8Array.from(atob(base64), char => char.charCodeAt(0));
    const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export interface SignedInTraveler {
  travelerId: string;
  /** From the ID token's name claim; null when there is no sign-in and the profile names them. */
  displayName: string | null;
  /** From the ID token's picture claim; null shows initials. */
  avatarUrl: string | null;
}

const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null;

/** The traveler the access token names, with the name and photo the ID token carries. */
export function travelerFromTokens(
  accessToken: string, idToken: string | null,
): SignedInTraveler | null {
  const travelerId = text(decodeJwtPayload(accessToken)?.traveler_id);
  if (!travelerId) return null;
  const profile = idToken ? decodeJwtPayload(idToken) : null;
  return {
    travelerId,
    displayName: text(profile?.name) ?? text(profile?.email),
    avatarUrl: text(profile?.picture),
  };
}
