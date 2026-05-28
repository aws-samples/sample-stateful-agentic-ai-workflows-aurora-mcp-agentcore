import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { ALEX_IMAGE_URL, ALEX_NAME } from '../lib/personas';

// Snake-case schema keys read as "authentic Aurora data" for some fields
// (no_red_eye, vegetarian_friendly) but feel awkward for multi-word
// concepts (loyalty_programs, travel_style, recent_trips). Whitelist the
// ones we want to keep snake_case; humanize the rest.
const VERBATIM_KEYS = new Set([
  'no_red_eye',
  'vegetarian_friendly',
  'home_airport',
  'budget_cap',
  'avoid_connections',
]);

function formatFactKey(key: string): string {
  if (VERBATIM_KEYS.has(key)) return key;
  return key.replace(/_/g, ' ');
}

export function TravelerContextPanel({
  state,
  onOpenMemory,
  compact = false,
}: {
  state: MeridianShowcaseState;
  onOpenMemory: () => void;
  compact?: boolean;
}) {
  // Show every preference fact Aurora returned (capped only so the panel
  // doesn't scroll forever). Underscored keys are kept verbatim - the
  // schema renders them snake_case which reads as authentic data, but
  // multi-word slugs like "travel_style" get a single space for clarity.
  const facts = state.memoryFacts.slice(0, compact ? 6 : 14);

  return (
    <section className={`mds-panel mds-traveler-panel${compact ? ' is-compact' : ''}`}>
      <div className="mds-panel-head">
        <strong>For you</strong>
        <button type="button" onClick={onOpenMemory}>
          Memory
        </button>
      </div>
      <div className="mds-profile-line">
        <span className="mds-avatar is-photo" aria-hidden="true">
          <img src={ALEX_IMAGE_URL} alt={ALEX_NAME} loading="lazy" />
        </span>
        <div>
          <strong>Alex Morgan</strong>
          <small>{state.travelerId}</small>
        </div>
      </div>
      <div className="mds-fact-list">
        {facts.map((fact) => (
          <div className="mds-fact-row" key={fact.key}>
            <span>{formatFactKey(fact.key)}</span>
            <b>{fact.value}</b>
          </div>
        ))}
      </div>
      <div className="mds-run-config">
        <div>
          <span>Mode</span>
          <b>{state.phaseLabel}</b>
        </div>
        <div>
          <span>Model</span>
          <b>{state.modelLabel}</b>
        </div>
        <div>
          <span>Embed</span>
          <b>{state.embedLabel}</b>
        </div>
      </div>
    </section>
  );
}
