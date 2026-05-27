/* eslint-disable react-refresh/only-export-components */
/**
 * TravelerPersona — demo traveler identity + Aurora-backed memory (Phase 4)
 */
import { useEffect, useState } from 'react';
import { fetchMemoryProfile } from '../api/client';
import { DEMO_MEMORY_FACTS, DEMO_TRAVELER } from '../lib/proDemoData';
import type { LongTermMemoryFact, TravelerProfile } from '../types';

export const DEMO_TRAVELER_ID = 'trv_meridian_demo';

export function personaInitials(fullName: string): string {
  return fullName
    .split(/[&\s]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

export const DEMO_PERSONA_INITIALS = personaInitials('Alex & Jordan Chen');

export const DEMO_PERSONA_FALLBACK: TravelerProfile = {
  ...DEMO_TRAVELER,
  party_size: 2,
  seat_preference: 'Window on short-haul · aisle on long-haul',
};

interface TravelerPersonaProps {
  travelerId?: string;
  variant?: 'banner' | 'card' | 'featured';
  facts?: LongTermMemoryFact[];
  profile?: TravelerProfile | null;
  active?: boolean;
  onActivate?: () => void;
}

export function TravelerPersona({
  travelerId = DEMO_TRAVELER_ID,
  variant = 'banner',
  facts: factsProp,
  profile: profileProp,
  active = true,
  onActivate,
}: TravelerPersonaProps) {
  const [profile, setProfile] = useState<TravelerProfile>(
    profileProp ?? DEMO_PERSONA_FALLBACK,
  );
  const [facts, setFacts] = useState<LongTermMemoryFact[]>(factsProp ?? DEMO_MEMORY_FACTS);

  useEffect(() => {
    if (profileProp) setProfile(profileProp);
    if (factsProp?.length) setFacts(factsProp);
  }, [profileProp, factsProp]);

  useEffect(() => {
    if (profileProp && factsProp?.length) return;
    fetchMemoryProfile(travelerId)
      .then((res) => {
        if (res.profile) setProfile({ ...DEMO_PERSONA_FALLBACK, ...res.profile });
        if (res.facts.length) setFacts(res.facts);
      })
      .catch(() => {
        setProfile(DEMO_PERSONA_FALLBACK);
        setFacts(DEMO_MEMORY_FACTS);
      });
  }, [travelerId, profileProp, factsProp]);

  const name = profile.full_name ?? DEMO_PERSONA_FALLBACK.full_name!;
  const initials = personaInitials(name);

  const meta = [
    profile.home_airport ? `${profile.home_airport} home` : null,
    profile.party_size ? `${profile.party_size} travelers` : null,
    profile.trip_goal,
  ]
    .filter(Boolean)
    .join(' · ');

  const factLimit = variant === 'banner' ? 4 : 6;

  return (
    <div
      className={`traveler-persona traveler-persona--${variant}${active ? ' is-active' : ' is-idle'}`}
    >
      <div className="traveler-persona-avatar" aria-hidden="true">
        {initials}
      </div>
      <div className="traveler-persona-body">
        <div className="traveler-persona-head">
          <div>
            <div className="traveler-persona-eyebrow">
              {active ? 'Demo traveler · Phase 4 production' : 'Demo traveler · switch to Phase 4'}
            </div>
            <div className="traveler-persona-name">{name}</div>
          </div>
          <span className="traveler-persona-badge">
            {active ? 'Active in chat' : 'Aurora profile'}
          </span>
        </div>
        {meta && <p className="traveler-persona-meta">{meta}</p>}
        {(profile.dietary_notes || profile.seat_preference) && (
          <p className="traveler-persona-notes">
            {[profile.dietary_notes, profile.seat_preference].filter(Boolean).join(' · ')}
          </p>
        )}
        {facts.length > 0 ? (
          <div className="traveler-persona-facts">
            {facts.slice(0, factLimit).map((f) => (
              <span key={f.key} className="traveler-persona-fact" title={f.source}>
                {f.value}
              </span>
            ))}
          </div>
        ) : (
          <div className="traveler-persona-facts">
            <span className="traveler-persona-fact">No memory facts loaded yet</span>
          </div>
        )}
        {!active && onActivate && (
          <button type="button" className="traveler-persona-cta" onClick={onActivate}>
            Chat as Alex & Jordan → Phase 4
          </button>
        )}
      </div>
    </div>
  );
}
