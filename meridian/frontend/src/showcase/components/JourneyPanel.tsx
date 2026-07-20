import {
  AlertTriangle,
  Bookmark,
  CalendarDays,
  CheckCircle2,
  GitCompareArrows,
  Loader2,
  MapPin,
  Plane,
  Users,
} from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import type { LoyaltyProgram } from '../../types';
import { deriveRecoveryStage } from '../lib/recoveryState';
import { TripVisual } from './TripVisual';

function normalizeLoyalty(
  programs: Record<string, LoyaltyProgram | string>,
): LoyaltyProgram[] {
  return Object.entries(programs).flatMap(([key, value]) => {
    if (typeof value !== 'string') return value.program && value.tier ? [value] : [];
    const program = /marriott/i.test(key)
      ? 'Marriott Bonvoy'
      : /united/i.test(key)
        ? 'United MileagePlus'
        : key.replace(/_/g, ' ');
    return [{ program, tier: value }];
  });
}

export function JourneyPanel({ state }: { state: MeridianShowcaseState }) {
  const trip = state.selectedTrip ?? state.savedTrips[0] ?? null;
  const recoveryStage = deriveRecoveryStage(state);
  const partySize = state.travelerProfile?.party_size ?? 2;
  const loyalty = normalizeLoyalty(
    state.travelerProfile?.loyalty_programs ?? {},
  ).sort((left, right) => {
    const rank = (program: LoyaltyProgram) =>
      /marriott/i.test(program.program)
        ? 0
        : /united/i.test(program.program)
          ? 1
          : 2;
    return rank(left) - rank(right);
  });
  return (
    <aside className="mds-journey-panel" aria-label="Current trip and travel context">
      <header>
        <span>Current trip</span>
        <b className={`mds-current-trip-state is-${recoveryStage}`}>
          {recoveryStage === 'ready'
            ? 'Plan ready'
            : recoveryStage === 'running'
              ? 'Recovering'
              : 'Action needed'}
        </b>
      </header>
      <section className={`mds-current-trip-card is-${recoveryStage}`}>
        <div className="mds-current-trip-topline">
          <span>ANA · NH 109</span>
          <b>
            {recoveryStage === 'ready' ? (
              <CheckCircle2 size={14} aria-hidden="true" />
            ) : recoveryStage === 'running' ? (
              <Loader2 size={14} aria-hidden="true" />
            ) : (
              <AlertTriangle size={14} aria-hidden="true" />
            )}
            {recoveryStage === 'ready'
              ? 'Recovery plan ready'
              : recoveryStage === 'running'
                ? 'Checking alternatives'
                : 'Cancelled'}
          </b>
        </div>
        <div className="mds-current-trip-route" aria-label="New York JFK to Tokyo Haneda">
          <div>
            <strong>JFK</strong>
            <span>New York</span>
          </div>
          <span className="mds-current-trip-line" aria-hidden="true">
            <i />
            <Plane size={18} />
            <i />
          </span>
          <div>
            <strong>HND</strong>
            <span>Tokyo</span>
          </div>
        </div>
        <div className="mds-current-trip-meta">
          <span><CalendarDays size={14} />Today · 10:40 AM</span>
          <span><Users size={14} />{partySize} travelers</span>
        </div>
        <div className="mds-current-trip-loyalty">
          <span>United MileagePlus</span>
          <b>Premier 1K recognized</b>
        </div>
        <ol className={`mds-recovery-steps is-${recoveryStage}`} aria-label="Recovery workflow progress">
          <li className="is-complete"><i />Disruption</li>
          <li className={recoveryStage === 'action' ? '' : 'is-complete'}><i />Alternatives</li>
          <li className={recoveryStage === 'ready' ? 'is-complete' : ''}><i />Checkpointed plan</li>
        </ol>
      </section>
      {trip ? (
        <section className="mds-journey-recommendation">
          <header>
            <span>Recommended stay</span>
            <b>{state.savedTrips.length} saved</b>
          </header>
          <button className="mds-journey-hero" type="button" onClick={() => state.openTripDetails(trip)}>
            <TripVisual product={trip} compact />
            <span><small><MapPin size={13} />{trip.destination || trip.region || trip.category}</small><strong>{trip.name}</strong><b>From ${trip.price.toLocaleString()} per traveler</b></span>
          </button>
          <div className="mds-journey-actions">
            <button type="button" onClick={() => state.saveTrip(trip)}><Bookmark size={16} />{state.savedTripIds.has(trip.product_id) ? 'Saved' : 'Save'}</button>
            <button type="button" onClick={state.openComparison}><GitCompareArrows size={16} />Compare {state.comparedTrips.length || ''}</button>
          </div>
        </section>
      ) : null}
      <section className="mds-journey-profile">
        <span>Travel context</span>
        <dl>
          <div><dt>From</dt><dd>{state.travelerProfile?.home_airport ?? 'JFK'}</dd></div>
          <div><dt>Travelers</dt><dd>{partySize}</dd></div>
          <div><dt>Budget</dt><dd>{state.travelerProfile?.budget_max ? `$${state.travelerProfile.budget_max.toLocaleString()}` : 'Flexible'}</dd></div>
        </dl>
        {loyalty.length > 0 && (
          <div className="mds-journey-loyalty">
            <span>Loyalty status</span>
            <div className="mds-journey-loyalty-list">
              {loyalty.map((program) => (
                <div
                  className="mds-journey-loyalty-program"
                  key={`${program.program}-${program.member_id ?? program.tier}`}
                >
                  <b>{program.program} · {program.tier}</b>
                  <small>
                    {program.member_id}
                    {program.points_balance
                      ? ` · ${program.points_balance.toLocaleString()} ${
                          /united|mileage/i.test(program.program)
                            ? 'miles'
                            : 'points'
                        }`
                      : ''}
                  </small>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>
    </aside>
  );
}
