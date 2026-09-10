import { hasLiveLease, hasVerifiedResume, useEvidenceClock } from '../journey/evidence';
import { AuroraIcon } from '../components/ServiceMark';
import { Check, Circle, ShieldCheck } from 'lucide-react';

import type { JourneyDocument } from '../journey/types';
import { isObserved } from '../journey/types';

type Step = {
  id: string;
  label: string;
  /** What Aurora holds when this step has happened. */
  detail: string;
  done: boolean;
};

/** The continuity claim, one line per fact, each read from the database.
 *
 * The design prototype ticked these on a timer. Here each one is either true
 * of the journey in Aurora or it is not, so a presenter cannot get ahead of
 * the system and the system cannot claim a step it did not take.
 */
function stepsFor(document: JourneyDocument | null, now: number): Step[] {
  if (!document) {
    return [
      { id: 'auth', label: 'Traveler authorized', detail: 'No journey yet', done: false },
      { id: 'retrieved', label: 'Alternatives retrieved', detail: 'No journey yet', done: false },
      { id: 'checkpoint', label: 'Checkpoint persisted', detail: 'No journey yet', done: false },
      { id: 'interrupted', label: 'Worker interrupted', detail: 'No journey yet', done: false },
      { id: 'resumed', label: 'Saved plan resumed', detail: 'No journey yet', done: false },
    ];
  }

  const executions = isObserved(document.executions) ? document.executions.items : [];
  const abandoned = executions.filter((e) => e.status === 'abandoned');
  const running = executions.find((e) => hasLiveLease(e, now));
  const auth = document.authorization;
  const checkpoint = document.checkpoint;
  const resumed = hasVerifiedResume(document);

  return [
    {
      id: 'auth',
      label: 'Traveler authorized',
      detail: isObserved(auth)
        ? `${document.traveler_id} · ${auth.decision}`
        : 'No decision recorded',
      done: isObserved(auth) && auth.decision === 'allow',
    },
    {
      id: 'retrieved',
      label: 'Alternatives retrieved',
      detail: isObserved(document.recommendations)
        ? `${document.recommendations.items.length} options in the checkpoint`
        : 'Nothing checkpointed yet',
      done: isObserved(document.recommendations),
    },
    {
      id: 'checkpoint',
      label: 'Checkpoint persisted',
      detail: isObserved(checkpoint)
        ? checkpoint.checkpoint_id
        : 'Awaiting the first commit',
      done: isObserved(checkpoint),
    },
    {
      id: 'interrupted',
      label: 'Worker interrupted',
      detail: abandoned.length
        ? `${abandoned[0].worker_id} abandoned`
        : running
          ? `${running.worker_id} still running`
          : 'No execution yet',
      done: abandoned.length > 0,
    },
    {
      id: 'resumed',
      label: 'Saved plan resumed',
      detail: resumed ? 'Completed from the saved checkpoint' : 'Successful resume not verified',
      done: resumed,
    },
  ];
}

export function JourneyContinuityRail({
  document,
  error,
}: {
  document: JourneyDocument | null;
  error: string | null;
}) {
  const now = useEvidenceClock(document);
  const steps = stepsFor(document, now);

  return (
    <div className="mds-continuity-rail" tabIndex={0} role="region" aria-label="Journey progress">
      <header className="mds-continuity-head">
        <span>Journey continuity</span>
        <span
          className={`mds-continuity-dot${document && !error ? ' is-live' : ''}`}
          aria-hidden="true"
        />
      </header>

      <h2 className="mds-continuity-claim">
        Workers can fail.
        <span>The journey continues.</span>
      </h2>

      <ol className="mds-continuity-steps">
        {steps.map((step) => (
          <li key={step.id} className={step.done ? 'is-done' : ''}>
            <span className="mds-continuity-mark" aria-hidden="true">
              {step.done ? <Check size={13} strokeWidth={3} /> : <Circle size={13} />}
            </span>
            <span className="mds-continuity-copy">
              <strong>{step.label}</strong>
              <small>{step.detail}</small>
            </span>
          </li>
        ))}
      </ol>

      <footer className="mds-continuity-foot">
        <span className="mds-continuity-foot-head">
          <AuroraIcon size={15} aria-hidden="true" />
          Saved journey evidence.
        </span>
        <p>
          The workflow’s progress belongs to the journey, beyond a worker’s
          lifetime.
        </p>
        {document?.active_thread_id ? (
          <code>thread: {document.active_thread_id}</code>
        ) : (
          <code className="mds-continuity-absent">
            {error ? 'evidence unavailable' : 'reading…'}
          </code>
        )}
        <span className="mds-continuity-source">
          <ShieldCheck size={13} aria-hidden="true" />
          {error ? 'Read failed; any displayed evidence is from the last load.' : document ? 'Source: Aurora journey records.' : 'Aurora evidence has not loaded.'}
        </span>
      </footer>
    </div>
  );
}

export default JourneyContinuityRail;
