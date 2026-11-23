import { useState } from 'react';
import { ServiceMark } from '../components/ServiceMark';
import { BOUNDARY_CHECKS, SCENARIOS, type ScenarioId } from './briefingSteps';
import './briefingSteps.css';

const LABEL = 'Checks a hold request passes';
const STATUS = { 'is-done': 'Passed', 'is-stopped': 'Stopped here', 'is-ahead': 'Not reached' } as const;

/** Verify the boundaries: the five checks, where each illustrative hold request stops, and what is recorded. */
export function BoundaryScenarios() {
  const [selected, setSelected] = useState<ScenarioId>('within');
  const scenario = SCENARIOS.find(item => item.id === selected) ?? SCENARIOS[0];
  const refused = scenario.stopAt !== null;
  const state = (check: number): keyof typeof STATUS => {
    if (scenario.stopAt === null || check < scenario.stopAt) return 'is-done';
    return check === scenario.stopAt ? 'is-stopped' : 'is-ahead';
  };
  return <div className="mds-step-path mds-step-boundaries">
    <div className="mds-step-scenarios" role="group" aria-label="Scenario">
      {SCENARIOS.map(item => (
        <button key={item.id} type="button" className="mds-step-scenario" aria-pressed={item.id === selected} onClick={() => setSelected(item.id)}>
          {item.label}
        </button>
      ))}
    </div>
    <ol className="mds-check-rows" aria-label={LABEL}>
      {BOUNDARY_CHECKS.map((check, position) => (
        <li key={check.verb} className={`mds-check-row ${state(position)}`}>
          <span className="mds-check-tab" aria-hidden="true">{String(position + 1).padStart(2, '0')}</span>
          <ServiceMark name={check.mark} size={32} />
          <span className="mds-check-who"><span className="mds-check-verb">{check.verb}</span><span>{check.service}</span></span>
          <span className="mds-check-what"><strong>{check.question}</strong><span>{check.line}</span></span>
          <span className="mds-check-pill">{check.pill}</span>
          <span className={`mds-check-mark ${state(position)}`} aria-hidden="true">
            {state(position) === 'is-done' ? '✓' : state(position) === 'is-stopped' ? '✕' : ''}
          </span>
          <span className="mds-sr-only">{STATUS[state(position)]}</span>
        </li>
      ))}
    </ol>
    <div className={`mds-step-outcome ${refused ? 'is-stopped' : 'is-done'}`} aria-live="polite">
      <strong>{scenario.title}</strong>
      <p>{scenario.text}</p>
    </div>
    <p className="mds-step-note">Signed-in release shown. Illustrative scenarios: this briefing makes no service calls, and System evidence shows the recorded decisions.</p>
  </div>;
}
