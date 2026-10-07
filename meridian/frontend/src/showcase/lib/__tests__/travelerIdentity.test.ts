import { describe, expect, it } from 'vitest';
import {
  firstNameOf,
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
      id: 'trv_demo_decoy', name: 'Jordan Lee', initials: 'JL', avatarUrl: null, idVerified: false,
    });
  });

  it('falls back to the Aurora profile name, then to a neutral label', () => {
    const signedIn = { travelerId: 'trv_x', displayName: null, avatarUrl: null };
    expect(travelerIdentity(signedIn, ' Jordan Morgan ').name).toBe('Jordan Morgan');
    expect(travelerIdentity(signedIn, '').name).toBe(UNKNOWN_TRAVELER_NAME);
    expect(travelerIdentity(null, undefined)).toEqual({
      id: null, name: UNKNOWN_TRAVELER_NAME, initials: 'YA', avatarUrl: null, idVerified: false,
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

  it('shows the id the API confirmed over the one in the sign-in claims', () => {
    const claims = { travelerId: 'trv_claimed', displayName: 'Alex Lee', avatarUrl: null };
    expect(travelerIdentity(claims)).toMatchObject({ id: 'trv_claimed', idVerified: false });
    expect(travelerIdentity(claims, null, 'trv_from_api'))
      .toMatchObject({ id: 'trv_from_api', idVerified: true });
  });

  it('takes initials from the first character of a name, emoji included', () => {
    expect(initialsOf('\u{1F680}rocket Lee')).toBe('\u{1F680}L');
    expect(initialsOf('\u00e9mile zola')).toBe('\u00c9Z');
  });

  it('prefers the Aurora profile name over an email standing in for a name', () => {
    const emailOnly = { travelerId: 't', displayName: 'alex.lee@example.com', avatarUrl: null };
    expect(travelerIdentity(emailOnly, 'Alex Lee').name).toBe('Alex Lee');
    const identity = travelerIdentity(emailOnly);
    expect(identity.name).toBe('alex.lee@example.com');
    expect(identity.initials).toBe('AL');
  });

  it('gives the first name, or nothing when the account is unnamed', () => {
    expect(firstNameOf(travelerIdentity(
      { travelerId: 't', displayName: 'Alex Lee', avatarUrl: null },
    ))).toBe('Alex');
    expect(firstNameOf(travelerIdentity(null))).toBeNull();
    expect(firstNameOf(travelerIdentity(
      { travelerId: 't', displayName: 'alex.lee@example.com', avatarUrl: null },
    ))).toBeNull();
  });
});
