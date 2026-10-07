import { describe, expect, it } from 'vitest';
import {
  initialsOf,
  travelBriefLabel,
  travelerIdentity,
  UNKNOWN_TRAVELER_NAME,
} from '../travelerIdentity';

describe('travelerIdentity', () => {
  it('prefers the name from the sign-in over the Aurora profile', () => {
    const identity = travelerIdentity(
      { travelerId: 'trv_demo_decoy', displayName: 'Jordan Lee', avatarUrl: null },
      'Someone Else',
    );
    expect(identity).toEqual({
      id: 'trv_demo_decoy', name: 'Jordan Lee', initials: 'JL', avatarUrl: null,
    });
  });

  it('falls back to the Aurora profile name, then to a neutral label', () => {
    const signedIn = { travelerId: 'trv_x', displayName: null, avatarUrl: null };
    expect(travelerIdentity(signedIn, ' Jordan Morgan ').name).toBe('Jordan Morgan');
    expect(travelerIdentity(signedIn, '').name).toBe(UNKNOWN_TRAVELER_NAME);
    expect(travelerIdentity(null, undefined)).toEqual({
      id: null, name: UNKNOWN_TRAVELER_NAME, initials: 'YA', avatarUrl: null,
    });
  });

  it('carries the photo from the sign-in', () => {
    const signedIn = { travelerId: 'trv_x', displayName: 'A', avatarUrl: '/travel/a.jpg' };
    expect(travelerIdentity(signedIn).avatarUrl).toBe('/travel/a.jpg');
  });

  it('makes at most two capital initials', () => {
    expect(initialsOf('jordan lee morgan')).toBe('JL');
    expect(initialsOf('  ')).toBe('M');
  });

  it('names the brief button after the person, or says "your" when no name is known', () => {
    const named = { travelerId: 't', displayName: 'Jordan Lee', avatarUrl: null };
    expect(travelBriefLabel(travelerIdentity(named))).toBe('Open Jordan Lee travel brief');
    expect(travelBriefLabel(travelerIdentity(null))).toBe('Open your travel brief');
  });
});
