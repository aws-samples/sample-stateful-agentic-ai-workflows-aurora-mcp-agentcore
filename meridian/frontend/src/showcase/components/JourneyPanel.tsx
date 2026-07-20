import { Bookmark, GitCompareArrows, MapPin } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import type { LoyaltyProgram } from '../../types';
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
    <aside className="mds-journey-panel" aria-label="Trip workspace">
      <header><span>Your journey</span><b>{state.savedTrips.length} saved</b></header>
      {trip ? (
        <>
          <button className="mds-journey-hero" type="button" onClick={() => state.openTripDetails(trip)}>
            <TripVisual product={trip} compact />
            <span><small><MapPin size={13} />{trip.destination || trip.region || trip.category}</small><strong>{trip.name}</strong><b>From ${trip.price.toLocaleString()} per traveler</b></span>
          </button>
          <div className="mds-journey-actions">
            <button type="button" onClick={() => state.saveTrip(trip)}><Bookmark size={16} />{state.savedTripIds.has(trip.product_id) ? 'Saved' : 'Save'}</button>
            <button type="button" onClick={state.openComparison}><GitCompareArrows size={16} />Compare {state.comparedTrips.length || ''}</button>
          </div>
        </>
      ) : <div className="mds-journey-empty"><MapPin size={20} /><b>No shortlist yet</b><span>Your matched trips will stay here as you plan.</span></div>}
      <section className="mds-journey-profile">
        <span>Travel context</span>
        <dl>
          <div><dt>From</dt><dd>{state.travelerProfile?.home_airport ?? 'JFK'}</dd></div>
          <div><dt>Travelers</dt><dd>{state.travelerProfile?.party_size ?? 1}</dd></div>
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
