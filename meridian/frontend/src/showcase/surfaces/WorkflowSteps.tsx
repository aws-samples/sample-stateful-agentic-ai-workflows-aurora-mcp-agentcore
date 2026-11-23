import { ServiceMark } from '../components/ServiceMark';
import { LEASE_NOTE, WORKFLOW_NODES, WORKFLOW_STEPS } from './briefingSteps';
import { StepControls } from './StepPath';
import { useStepper } from './stepPathState';

const LABEL = 'Phase 5 durable workflow path';

/** Phase 5: the recovery route, the saved steps in Aurora, the lease and the two workers. */
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
          {step.session1 === 'live' ? 'Worker 1, microVM 1' : 'Worker 1, stopped'}
        </span>
        {step.session2 && <span className="mds-step-session is-live">
          <ServiceMark name="agentcore-runtime" size={24} />Worker 2, new microVM
        </span>}
      </div>
      <ol className="mds-step-nodes" aria-label={LABEL}>
        {WORKFLOW_NODES.map((item, node) => (
          <li key={item.title} className={`mds-step-node ${nodeState(node)}`}>
            <strong>{item.title}</strong>
            <p><code>{item.code}</code>{item.note && `, ${item.note}`}</p>
            {node < step.saved && <>
              <span className="mds-step-mark" aria-hidden="true">✓</span>
              <span className="mds-step-node-pill">microVM {item.worker}<span className="mds-sr-only">, saved</span></span>
            </>}
            {step.paused && node === step.saved && <span className="mds-step-node-pill is-paused">Paused for review</span>}
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
          : WORKFLOW_NODES.slice(0, step.saved).map((item, node) => (
            <li key={item.title} className={step.restored && node === step.saved - 1 ? 'is-restored' : undefined}>Saved step: {item.title}</li>
          ))}
      </ol>
      <p className="mds-step-lease">Lease: <strong>{step.lease}</strong></p>
      <p className="mds-step-lease-note">{LEASE_NOTE}</p>
    </aside>
  </div></div>;
}
