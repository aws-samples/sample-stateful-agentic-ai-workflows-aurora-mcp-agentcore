import { useState } from 'react';
import { BOUNDARY_HOPS, BOUNDARY_LABELS, SCENARIOS, type ScenarioId } from './briefingSteps';
import { HopBody } from './StepPath';
import { laneStyle } from './stepPathState';
import './briefingSteps.css';

const LABEL = 'Checks a hold request passes';

/** Verify the boundaries: where each illustrative hold request stops, and what is recorded. */
export function BoundaryScenarios() {
  const [selected, setSelected] = useState<ScenarioId>('within');
  const scenario = SCENARIOS.find(item => item.id === selected) ?? SCENARIOS[0];
  const refused = scenario.stopAt !== null;
  const end = scenario.stopAt ?? BOUNDARY_HOPS.length - 1;
  const state = (hop: number) => {
    if (hop < end || (!refused && hop === end)) return 'is-done';
    return hop === end ? 'is-stopped' : 'is-ahead';
  };
  return <div className="mds-step-path mds-step-boundaries">
    <div className="mds-step-scenarios" role="group" aria-label="Scenario">
      {SCENARIOS.map(item => (
        <button key={item.id} type="button" className="mds-step-scenario" aria-pressed={item.id === selected} onClick={() => setSelected(item.id)}>
          {item.label}
        </button>
      ))}
    </div>
    <div className="mds-step-track" style={laneStyle(BOUNDARY_HOPS.length, end)}>
      <span className={`mds-step-chip${refused ? ' is-stopped' : ''}`} aria-hidden="true">{scenario.chip}</span>
      <ol className="mds-step-lane" aria-label={LABEL}>
        {BOUNDARY_HOPS.map((hop, position) => (
          <li key={hop.title} className={`mds-step-hop ${state(position)}`}>
            <HopBody hop={hop} label={BOUNDARY_LABELS[position]} />
          </li>
        ))}
      </ol>
    </div>
    <div className={`mds-step-outcome ${refused ? 'is-stopped' : 'is-done'}`} aria-live="polite">
      <strong>{scenario.title}</strong>
      <p>{scenario.text}</p>
    </div>
    <p className="mds-step-note">Signed-in release shown. Illustrative scenarios: this briefing makes no service calls, and System evidence shows the recorded decisions.</p>
  </div>;
}
