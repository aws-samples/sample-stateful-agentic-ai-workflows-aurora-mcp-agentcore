/**
 * PresenterControls - discreet bottom strip for the speaker.
 *
 * Local UI state only. Backend is not involved. Hidden in kiosk mode.
 */
import type { StageScenario, StageView } from '../types';
import {
  ChevronLeft,
  ChevronRight,
  Pause,
  Play,
  RotateCcw,
} from 'lucide-react';

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
          {isPlaying ? (
            <Pause size={13} aria-hidden="true" />
          ) : (
            <Play size={13} aria-hidden="true" />
          )}
          {isPlaying ? 'Pause' : isComplete ? 'Replay' : 'Play'}
          <span className="ds-kbd">Space</span>
        </button>
        <button
          type="button"
          className="ds-ctrl-btn"
          onClick={onPrev}
          aria-label="Previous span"
        >
          <ChevronLeft size={14} aria-hidden="true" />
          Prev
        </button>
        <button
          type="button"
          className="ds-ctrl-btn"
          onClick={onStep}
          disabled={!canStep}
          aria-label="Step to next span"
        >
          Next span
          <ChevronRight size={14} aria-hidden="true" />
          <span className="ds-kbd">Right</span>
        </button>
        <button
          type="button"
          className="ds-ctrl-btn"
          onClick={onReplay}
          aria-label="Replay trace from the beginning"
        >
          <RotateCcw size={13} aria-hidden="true" />
          Replay <span className="ds-kbd">R</span>
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
