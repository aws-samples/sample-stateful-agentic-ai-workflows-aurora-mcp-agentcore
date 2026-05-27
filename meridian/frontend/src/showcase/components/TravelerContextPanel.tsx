import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';

export function TravelerContextPanel({
  state,
  onOpenMemory,
  compact = false,
}: {
  state: MeridianShowcaseState;
  onOpenMemory: () => void;
  compact?: boolean;
}) {
  const facts = state.memoryFacts.slice(0, compact ? 4 : 7);

  return (
    <section className={`mds-panel mds-traveler-panel${compact ? ' is-compact' : ''}`}>
      <div className="mds-panel-head">
        <strong>Traveler context</strong>
        <button type="button" onClick={onOpenMemory}>
          Memory
        </button>
      </div>
      <div className="mds-profile-line">
        <span className="mds-avatar" aria-hidden="true" />
        <div>
          <strong>Alex Morgan</strong>
          <small>{state.travelerId}</small>
        </div>
      </div>
      <div className="mds-fact-list">
        {facts.map((fact) => (
          <div className="mds-fact-row" key={fact.key}>
            <span>{fact.key}</span>
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
