import { Expand, Monitor } from 'lucide-react';
import type { PresentationMode } from '../hooks/usePresentationMode';

export function PresenterControls({ mode }: { mode: PresentationMode }) {
  return (
    <section className="mc-presenter-controls" aria-label="Presenter controls" hidden={mode.fullscreen}>
      <div className="mc-presenter-caption">
        <strong><Monitor size={17} aria-hidden="true" /> Presenter controls</strong>
        <span>Hidden in fullscreen · Esc to return</span>
      </div>
      <div className="mc-presenter-actions">
        <label className="mc-projector-option">
          <input type="checkbox" checked={mode.projector} onChange={(event) => mode.setProjector(event.target.checked)} />
          Projector readability
        </label>
        <button type="button" aria-pressed={mode.preview} onClick={() => mode.setPreview(!mode.preview)}>
          Preview audience layout
        </button>
        <button className="mc-present-button" type="button" onClick={() => void mode.present()}>
          <Expand size={17} aria-hidden="true" /> Present fullscreen
        </button>
      </div>
      {mode.error && <p className="mc-presenter-error" role="alert">{mode.error}</p>}
    </section>
  );
}
