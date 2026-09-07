import { MERIDIAN_MARK_SRC } from '../../lib/meridianBrand';
import { AlertTriangle, ArrowRight, Plane, Ticket } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { deriveRecoveryStage } from '../lib/recoveryState';

/** The reported JFK–Tokyo scenario has no airline-issued flight/seat data.
 * Keep the familiar boarding-pass format without inventing a usable ticket. */
export function RecoveryBoardingPass({ state }: { state: MeridianShowcaseState }) {
  const stage = deriveRecoveryStage(state);
  const profile = state.travelerProfile ?? state.previewProfile;
  const recovering = stage !== 'action';
  return (
    <section className="mc-disrupted-journey" aria-label="Disrupted flight boarding pass">
      <div className="mc-cancellation-alert" role="status">
        <AlertTriangle size={21} aria-hidden="true" />
        <div><strong>Flight canceled. Your journey doesn’t end here.</strong><p>We’ll work through the alternatives and keep your preferences in the plan.</p></div>
        <span>Traveler-reported</span>
      </div>
      <div className="mc-boarding-pass">
        <div className="mc-pass-main">
          <header><span><img src={MERIDIAN_MARK_SRC} alt="" width="22" height="22" />Meridian</span><span>Original itinerary</span></header>
          <div className="mc-pass-route"><div><strong>JFK</strong><span>New York</span></div><span className="mc-pass-route-line"><Plane size={24} strokeWidth={1.5} aria-hidden="true" /></span><div><strong>Tokyo</strong><span>Japan</span></div></div>
          <dl className="mc-pass-details"><div><dt>Passenger</dt><dd>{profile?.full_name ?? 'Alex Morgan'}</dd></div><div><dt>Flight / seat</dt><dd>Not provided</dd></div><div><dt>Document</dt><dd>Itinerary preview</dd></div></dl>
        </div>
        <div className="mc-pass-stub"><Ticket size={23} aria-hidden="true" /><strong>Canceled</strong><span>Not valid for boarding</span><div>{recovering ? (stage === 'ready' ? 'Recovery plan ready' : stage === 'checkpointed' ? 'Your progress is saved' : 'Finding alternatives') : 'Ready for a new plan'}<ArrowRight size={14} aria-hidden="true" /></div></div>
      </div>
    </section>
  );
}
