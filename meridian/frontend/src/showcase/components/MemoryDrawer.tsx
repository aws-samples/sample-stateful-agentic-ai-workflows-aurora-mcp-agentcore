import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';

export function MemoryDrawer({ state, open, onClose }: { state: MeridianShowcaseState; open: boolean; onClose: () => void }) {
  if (!open) return null;

  return (
    <div className="mds-drawer-backdrop" onClick={onClose} role="presentation">
      <aside className="mds-drawer" role="dialog" aria-modal="true" aria-label="Traveler memory" onClick={(e) => e.stopPropagation()}>
        <header>
          <div>
            <span>Traveler memory</span>
            <strong>Alex Morgan</strong>
          </div>
          <button type="button" onClick={onClose} aria-label="Close memory drawer">
            x
          </button>
        </header>
        <div className="mds-drawer-list">
          {state.memoryFacts.map((fact) => (
            <div className="mds-drawer-row" key={fact.key}>
              <div>
                <span>{fact.key}</span>
                <b>{fact.value}</b>
                <small>{fact.source ?? 'memory'} · conf {fact.confidence?.toFixed(2) ?? 'n/a'}</small>
              </div>
              <div>
                <button type="button" disabled title="Demo only - memory mutation API not exposed">
                  edit
                </button>
                <button type="button" disabled title="Demo only - memory mutation API not exposed">
                  forget
                </button>
              </div>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
