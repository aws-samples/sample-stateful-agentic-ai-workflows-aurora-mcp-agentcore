import { ArrowRight, CalendarDays, Check, Heart, Plane, ShieldCheck, UsersRound, Utensils } from 'lucide-react';
import { ALEX_IMAGE_URL } from '../lib/personas';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { TripHoldReceipt } from '../components/TripHoldReceipt';
import { BudgetCeiling } from '../components/BudgetCeiling';

function dateLabel(value: string) {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(new Date(`${value}T12:00:00`));
}

export function ConciergeRail({ state, onSaved, onRecovery }: {
  state: MeridianShowcaseState;
  onSaved?: () => void;
  onRecovery?: () => void;
}) {
  const profile = state.travelerProfile ?? state.previewProfile;
  const filters = state.chatFilters;
  const party = state.travelersCount;
  const dates = filters.startDate
    ? `${dateLabel(filters.startDate)}${filters.endDate ? ` – ${dateLabel(filters.endDate)}` : ' onward'}`
    : 'Open to ideas';
  const preferences = [
    { value: profile?.seat_preference, icon: Plane },
    { value: profile?.dietary_notes, icon: Utensils },
    { value: profile?.trip_goal, icon: CalendarDays },
  ].filter(item => item.value);

  return <div className="mc-brief">
    <header className="mc-brief-header"><h2>Your travel brief</h2><span>Always part of the conversation.</span></header>
    {state.tripHolds?.slice(-1).map(hold => <TripHoldReceipt key={hold.order.order_id} hold={hold} compact />)}
    <div className="mc-traveler"><img src={ALEX_IMAGE_URL} alt="" width="44" height="44" /><div><strong>Alex Morgan</strong><span>{profile ? 'Your preferences, remembered' : 'Let’s get to know your travel style'}</span></div></div>
    <div className="mc-departure"><div><span>Flying from</span><strong>{profile?.home_airport ?? 'Not set'}</strong></div><Plane size={26} strokeWidth={1.3} aria-hidden="true" /><div><span>Next stop</span><strong>Possibility.</strong></div></div>
    <dl className="mc-brief-details">
      <div><dt><UsersRound size={16} aria-hidden="true" />Travelers</dt><dd>{party ? `${party} ${party === 1 ? 'adult' : 'adults'}` : 'Not set'}</dd></div>
      <div><dt><CalendarDays size={16} aria-hidden="true" />Travel dates</dt><dd>{dates}</dd></div>
      <div><dt>Usual budget</dt><dd><BudgetCeiling perTravelerCents={state.budgetCeilingPerTravelerCents} travelers={party} /></dd></div>
    </dl>
    <section className="mc-preferences" aria-label="Remembered preferences"><h3><Check size={16} aria-hidden="true" />The details that matter</h3>
      {preferences.length ? <ul>{preferences.map(({ value, icon: Icon }) => <li key={value}><Icon size={16} aria-hidden="true" /><span>{value}</span></li>)}</ul> : <p>Share your seat, dining, and stay preferences with the concierge.</p>}
    </section>
    {onSaved && <button type="button" className="mc-saved-link" onClick={onSaved}><Heart size={17} aria-hidden="true" /><span>Saved for later</span><b>{state.savedTrips.length}</b><ArrowRight size={15} aria-hidden="true" /></button>}
    {onRecovery && <section className="mc-recovery-teaser"><img src="/travel/recovery-flight.jpg" alt="" loading="lazy" width="1600" height="900" /><div><h3>A change of plans?</h3><p>Let’s work out your next move.</p><button type="button" onClick={onRecovery}>Visit recovery desk<ArrowRight size={16} aria-hidden="true" /></button></div></section>}
    <p className="mc-brief-foot"><ShieldCheck size={17} aria-hidden="true" /><span>You make the final call.<br />We’ll ask before placing a hold.</span></p>
  </div>;
}
