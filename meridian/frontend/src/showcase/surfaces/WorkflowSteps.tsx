import { ServiceMark } from '../components/ServiceMark';
import { WORKFLOW_NODES, WORKFLOW_STEPS } from './briefingSteps';
import { StepControls } from './StepPath';
import { useStepper } from './stepPathState';

const LABEL = 'Phase 5 durable workflow path';

/** Phase 5: the recovery route, the saved steps in Aurora, the lease and the Runtime sessions. */
export function WorkflowSteps() {
  const { index, back, next, reset } = useStepper(WORKFLOW_STEPS.length);
  const step = WORKFLOW_STEPS[index];
  const nodeState = (node: number) => {
    if (node < step.saved) return 'is-done';
    if (step.paused && node === step.saved) return 'is-paused';
    return node === step.next ? 'is-next' : '';
  };
  return <div className="mds-step-path mds-step-workflow"><div className="mds-step-workflow-grid">
    <div className="mds-step-workflow-main">
    <div className="mds-step-sessions">
      <span className={`mds-step-session ${step.session1 === 'live' ? 'is-live' : 'is-stopped'}`}>
        <ServiceMark name="agentcore-runtime" size={24} />
        {step.session1 === 'live' ? 'Runtime session 1' : 'Runtime session 1, stopped'}
      </span>
      {step.session2 && <span className="mds-step-session is-live">
        <ServiceMark name="agentcore-runtime" size={24} />Runtime session 2, new microVM
      </span>}
    </div>
      <ol className="mds-step-nodes" aria-label={LABEL}>
        {WORKFLOW_NODES.map(([title, detail], node) => (
          <li key={title} className={`mds-step-node ${nodeState(node)}`}>
            <span className="mds-step-node-number" aria-hidden="true">{node + 1}</span>
            <strong>{title}</strong>
            <p>{detail}</p>
            {node < step.saved && <span className="mds-step-node-flag">Saved</span>}
            {step.paused && node === step.saved && <span className="mds-step-node-flag">Paused for review</span>}
          </li>
        ))}
      </ol>
      <p className="mds-step-caption" aria-live="polite"><span className="mds-sr-only">Step {index + 1} of {WORKFLOW_STEPS.length}. </span>{step.caption}</p>
      <StepControls index={index} count={WORKFLOW_STEPS.length} label={LABEL} onBack={back} onNext={next} onReset={reset} />
    </div>
      <aside className="mds-step-store" aria-label="Saved steps in Amazon Aurora">
        <div className="mds-step-store-head">
          <ServiceMark name="aurora" size={32} />
          <div><strong>Amazon Aurora</strong><span>workflow_snapshots</span></div>
        </div>
        <ol className="mds-step-saved">
          {step.saved === 0
            ? <li className="is-empty">No saved steps yet</li>
            : WORKFLOW_NODES.slice(0, step.saved).map(([title], node) => (
              <li key={title} className={step.restored && node === step.saved - 1 ? 'is-restored' : undefined}>Saved step: {title}</li>
            ))}
        </ol>
        <p className="mds-step-lease">Lease: <strong>{step.lease}</strong></p>
      </aside>
  </div></div>;
}
