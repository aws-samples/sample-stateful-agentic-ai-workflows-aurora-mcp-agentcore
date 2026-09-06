import { ArrowRight, MessageCircle } from 'lucide-react';

import { ChatTranscript } from '../components/ChatTranscript';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { tripVisualPhoto } from '../lib/tripVisualPhoto';
import type { Product } from '../../types';

function money(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value);
}

/** The traveler's side of the Concierge surface.
 *
 * It occupies the same slot as the ladder's evidence rail and the same dock
 * carries its composer, so moving between the two surfaces changes what is on
 * screen without moving the screen around. Concierge is the calm version of
 * the same shape; the ladder is where it opens up.
 *
 * It drives the real chat. The design prototype's scripted exchange is a
 * visual reference, not a second implementation to keep in sync.
 */
export function ConciergeRail({ state }: { state: MeridianShowcaseState }) {
  const pool: Product[] =
    state.recommendations.length > 0 ? state.recommendations : state.catalog;
  const options = pool.slice(0, 3);
  const hasTurn = state.messages.length > 0;

  return (
    <div className="mds-concierge-rail">
      <header className="mds-concierge-rail-head">
        <span className="mds-concierge-rail-glyph" aria-hidden="true">
          <MessageCircle size={17} />
        </span>
        <div>
          <strong>Your travel concierge</strong>
          <p>A little continuity, wherever you go.</p>
        </div>
      </header>

      {hasTurn ? (
        <div className="mds-concierge-rail-thread">
          <ChatTranscript state={state} compact />
        </div>
      ) : (
        <p className="mds-concierge-rail-idle">
          Ask for a change of plan, a quieter stay, or somewhere new. Alex’s
          saved preferences come along.
        </p>
      )}

      {options.length > 0 && (
        <section
          className="mds-concierge-rail-options"
          aria-label="Made for your next chapter"
        >
          <header>
            <strong>Made for your next chapter</strong>
            <span>
              {options.length} {options.length === 1 ? 'stay' : 'stays'}
            </span>
          </header>
          <ul>
            {options.map((option) => {
              const photo = tripVisualPhoto(option);
              return (
                <li key={option.product_id}>
                  <button
                    type="button"
                    onClick={() => state.openTripDetails(option)}
                    aria-label={`Open ${option.name}`}
                  >
                    <span className="mds-concierge-rail-thumb" aria-hidden="true">
                      {photo.src && <img src={photo.src} alt="" loading="lazy" />}
                    </span>
                    <span className="mds-concierge-rail-option-copy">
                      <strong>{option.name}</strong>
                      <small>
                        {option.available_sizes?.[0] ?? 'Flexible'} ·{' '}
                        {money(option.price)}
                      </small>
                    </span>
                    <ArrowRight size={15} aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <p className="mds-concierge-rail-foot">
        Recommendations come from the Meridian catalog in Aurora.
      </p>
    </div>
  );
}

export default ConciergeRail;
