import { useEffect, useRef } from 'react';
import { ChevronDown, Expand, Monitor } from 'lucide-react';
import type { PresentationMode } from '../hooks/usePresentationMode';

export function PresenterControls({ mode }: { mode: PresentationMode }) {
  const settings = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (settings.current && !settings.current.contains(event.target as Node)) settings.current.open = false;
    };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, []);
  return (
    <section className="mc-presenter-controls" aria-label="Presenter controls" hidden={mode.fullscreen}>
      <div className="mc-presenter-caption">
        <strong><Monitor size={17} aria-hidden="true" /> Presenter controls</strong>
        <span>Hidden in fullscreen · Esc to return</span>
      </div>
      <div className="mc-presenter-actions">
        <details className="mc-display-settings" ref={settings} onKeyDown={(event) => {
          if (event.key === 'Escape' && settings.current?.open) {
            settings.current.open = false;
            settings.current.querySelector('summary')?.focus();
            event.stopPropagation();
          }
        }}>
          <summary>Display settings<ChevronDown size={15} aria-hidden="true" /></summary>
          <div className="mc-display-settings-panel">
            <label className="mc-projector-option">
              <input type="checkbox" checked={mode.projector} onChange={(event) => mode.setProjector(event.target.checked)} />
              Projector readability
            </label>
            <button type="button" aria-pressed={mode.preview} onClick={() => mode.setPreview(!mode.preview)}>
              Preview audience layout
            </button>
          </div>
        </details>
        <button className="mc-present-button" type="button" aria-label="Present fullscreen" onClick={() => {
          if (settings.current) settings.current.open = false;
          void mode.present();
        }}>
          <Expand size={17} aria-hidden="true" /> Present<span className="mc-fullscreen-label"> fullscreen</span>
        </button>
      </div>
      {mode.error && <p className="mc-presenter-error" role="alert">{mode.error}</p>}
    </section>
  );
}
