import { Check, Pencil, Trash2 } from 'lucide-react';
import { useState } from 'react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { ShowcaseSheet } from './ShowcaseSheet';

export function MemoryDrawer({ state, open, onClose }: { state: MeridianShowcaseState; open: boolean; onClose: () => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  return (
    <ShowcaseSheet
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose();
      }}
      title="Traveler memory"
      subtitle="Alex Morgan"
      description="Review and update Aurora-backed traveler preferences"
      closeLabel="Close memory drawer"
    >
      <p className="mds-memory-disclosure">Preferences are scoped to this traveler in Aurora and used only for personalized planning.</p>
      {state.memoryMutationError && <div className="mds-error-banner" role="alert">{state.memoryMutationError}</div>}
      <div className="mds-drawer-list">
        {state.memoryFacts.length === 0 && <div className="mds-navpanel-empty"><b>No saved preferences</b><span>Preferences learned from a live Production turn appear here.</span></div>}
        {state.memoryFacts.map((fact) => (
          <div className="mds-drawer-row" key={fact.key}>
            <div>
              <span>{fact.key.replace(/_/g, ' ')}</span>
              {editing === fact.key ? (
                <input value={value} onChange={(e) => setValue(e.target.value)} aria-label={`Edit ${fact.key}`} autoFocus />
              ) : <b>{fact.value}</b>}
              <small>{fact.source ?? 'memory'} · confidence {fact.confidence?.toFixed(2) ?? 'n/a'}</small>
            </div>
            <div>
              {editing === fact.key ? (
                <button type="button" disabled={!value.trim() || busy === fact.key} onClick={async () => { setBusy(fact.key); if (await state.updateMemoryPreference(fact.key, value.trim())) setEditing(null); setBusy(null); }} aria-label={`Save ${fact.key}`}><Check size={15} /></button>
              ) : (
                <button type="button" onClick={() => { setEditing(fact.key); setValue(fact.value); }} aria-label={`Edit ${fact.key}`}><Pencil size={15} /></button>
              )}
              <button type="button" disabled={busy === fact.key} onClick={async () => { setBusy(fact.key); await state.deleteMemoryPreference(fact.key); setBusy(null); }} aria-label={`Forget ${fact.key}`}><Trash2 size={15} /></button>
            </div>
          </div>
        ))}
      </div>
    </ShowcaseSheet>
  );
}
