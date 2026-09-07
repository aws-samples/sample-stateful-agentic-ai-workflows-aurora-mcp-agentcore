import { ArrowRight, Check, Pause, Play } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { deriveRecoveryEvidence } from '../lib/recoveryState';
import { SHOWCASE_FINALE_PROMPT } from '../lib/showcaseAdapters';
import { deriveWorkflowState } from '../lib/showcaseProof';
import { AuroraIcon } from './ServiceMark';

/** Teach the checkpoint here; carry the same thread to the traveler desk. */
export function WorkflowWorkspace({ state, onOpenRecovery }: {
  state: MeridianShowcaseState;
  onOpenRecovery: () => void;
}) {
  const evidence = deriveRecoveryEvidence(state);
  const workflow = deriveWorkflowState(state.traceSpans);
  const paused = state.workflowStatus === 'paused';
  const finished = state.workflowStatus === 'resumed' || state.workflowStatus === 'complete';
  const canContinue = !state.isLoading && (paused || finished);
  const steps = [
    { title: 'Find alternatives', detail: 'Classify the request and rank the trip options.', done: evidence.searchObserved },
    { title: 'Save the next step', detail: 'Keep the shortlist and where to resume in a checkpoint.', done: evidence.checkpointObserved },
    { title: 'Continue at the desk', detail: 'Check availability and request a timed package hold.', done: finished && evidence.availabilityObserved },
  ];

  return <section className="mc-workflow-lab" aria-label="Workflow checkpoint demonstration">
    <p className="mc-workflow-request">“My JFK-to-Tokyo flight was canceled. Help me find another way.”</p>
    <ol className="mc-workflow-steps" aria-label="Recovery workflow path">
      {steps.map((step, index) => <li key={step.title} data-complete={step.done}>
        <span className="mc-workflow-step-mark" aria-hidden="true">{step.done ? <Check size={20} /> : index + 1}</span>
        <h2>{step.title}</h2><p>{step.detail}</p>
      </li>)}
    </ol>
    <div className="mc-workflow-handoff" aria-busy={state.isLoading}>
      <div className="mc-workflow-handoff-copy">
        <h2>{state.isLoading ? 'Waiting for the workflow result…' : state.error ? 'Check the saved progress.' : paused ? 'The plan can wait. The progress is saved.' : finished ? 'The workflow has continued.' : 'Pause here. Pick up at the recovery desk.'}</h2>
        <p>{state.isLoading ? 'Completed steps appear when the backend returns its evidence.' : state.error ? 'The request was interrupted. Open the desk to review the saved state before retrying.' : paused ? 'Carry this shortlist into the recovery desk. Availability is the next step; no inventory is held yet.' : finished ? 'Return to the recovery desk to review the plan and its hold receipt.' : 'Run the search to its checkpoint. Then use the saved plan to continue the traveler’s recovery.'}</p>
      </div>
      {canContinue || state.error ? <button type="button" className="mc-session-primary" onClick={onOpenRecovery} disabled={state.isLoading}>
        Continue at recovery desk <ArrowRight size={18} aria-hidden="true" />
      </button> : <button type="button" className="mc-session-primary" disabled={state.isLoading} onClick={() => void state.applyPhaseExample(SHOWCASE_FINALE_PROMPT, true, 5)}>
        {state.isLoading ? <Pause size={18} aria-hidden="true" /> : <Play size={18} aria-hidden="true" />}
        {state.isLoading ? 'Running to checkpoint…' : 'Run to checkpoint'}
      </button>}
    </div>
    {(state.conversationId || evidence.checkpointObserved) && <dl className="mc-workflow-receipt">
      <div><dt>Same conversation</dt><dd><code>{state.conversationId ?? 'Not recorded'}</code></dd></div>
      <div><dt><AuroraIcon size={18} aria-hidden="true" /> Checkpoint store</dt><dd>{workflow.checkpoint}{evidence.checkpointObserved && <small>{evidence.durableCheckpoint ? 'Saved outside the worker' : 'In-process only; restart recovery is not proven'}</small>}</dd></div>
    </dl>}
  </section>;
}
