/**
 * PresenterControls — discreet bottom strip for the speaker.
 *
 * Local UI state only. Backend is not involved. Hidden in kiosk mode.
 */
import type { StageScenario, StageView } from '../types';

interface PresenterControlsProps {
  isPlaying: boolean;
  isComplete: boolean;
  canStep: boolean;
  view: StageView;
  scenarios: StageScenario[];
  scenarioId: StageScenario['id'];
  onScenario: (id: StageScenario['id']) => void;
  onTogglePlay: () => void;
  onStep: () => void;
  onPrev: () => void;
  onReplay: () => void;
  onView: (view: StageView) => void;
}

function PlayIcon({ playing }: { playing: boolean }) {
  return playing ? (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="3" y="2.5" width="3.6" height="11" rx="1" fill="currentColor" />
      <rect x="9.4" y="2.5" width="3.6" height="11" rx="1" fill="currentColor" />
    </svg>
  ) : (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M4 2.5L13 8L4 13.5V2.5Z" fill="currentColor" />
    </svg>
  );
}

const SCENARIO_LABEL: Record<StageScenario['id'], string> = {
  tokyo: 'Tokyo culture',
  recall: 'Recall',
  plan: 'Plan trip',
};

export function PresenterControls({
  isPlaying,
  isComplete,
  canStep,
  view,
  scenarios,
  scenarioId,
  onScenario,
  onTogglePlay,
  onStep,
  onPrev,
  onReplay,
  onView,
}: PresenterControlsProps) {
  return (
    <div className="ds-controls" role="toolbar" aria-label="Presenter controls">
      <div className="ds-controls-group" aria-label="Playback">
        <button
          type="button"
          className="ds-ctrl-btn is-primary"
          onClick={onTogglePlay}
          aria-label={isPlaying ? 'Pause demo loop' : 'Start demo loop'}
        >
          <PlayIcon playing={isPlaying} />
          {isPlaying ? 'Pause' : isComplete ? 'Replay' : 'Play'}
          <span className="ds-kbd">Space</span>
        </button>
        <button
          type="button"
          className="ds-ctrl-btn"
          onClick={onPrev}
          aria-label="Previous span"
        >
          ◀ Prev
        </button>
        <button
          type="button"
          className="ds-ctrl-btn"
          onClick={onStep}
          disabled={!canStep}
          aria-label="Step to next span"
        >
          Next span ▶ <span className="ds-kbd">→</span>
        </button>
        <button
          type="button"
          className="ds-ctrl-btn"
          onClick={onReplay}
          aria-label="Replay trace from the beginning"
        >
          ↻ Replay <span className="ds-kbd">R</span>
        </button>
      </div>

      <div className="ds-controls-spacer" />

      <div className="ds-ctrl-scenario" role="tablist" aria-label="Scenario">
        {scenarios.map((s) => (
          <button
            key={s.id}
            type="button"
            role="tab"
            aria-selected={s.id === scenarioId}
            className={s.id === scenarioId ? 'is-on' : ''}
            onClick={() => onScenario(s.id)}
          >
            {SCENARIO_LABEL[s.id] ?? s.id}
          </button>
        ))}
      </div>

      <div className="ds-ctrl-toggle" role="tablist" aria-label="Audience or builder view">
        <button
          type="button"
          role="tab"
          aria-selected={view === 'audience'}
          className={view === 'audience' ? 'is-on' : ''}
          onClick={() => onView('audience')}
        >
          Audience
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'builder'}
          className={view === 'builder' ? 'is-on' : ''}
          onClick={() => onView('builder')}
        >
          Builder <span className="ds-kbd">B</span>
        </button>
      </div>
    </div>
  );
}
