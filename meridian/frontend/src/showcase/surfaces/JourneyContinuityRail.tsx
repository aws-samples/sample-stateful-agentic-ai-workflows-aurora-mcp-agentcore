import { motion } from 'motion/react';
import { hasVerifiedResume } from '../journey/evidence';
import { AuroraIcon } from '../components/ServiceMark';
import { STATE_CHANGE, useLiveCues } from '../hooks/useLiveCues';
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
 * The design prototype ticked these on a timer. Here a row appears only once
 * Aurora records its fact, so a presenter cannot get ahead of the system and
 * the system cannot claim a step it did not take. Before that there is no row.
 */
function stepsFor(document: JourneyDocument | null): Step[] {
  if (!document) return [];

  const executions = isObserved(document.executions) ? document.executions.items : [];
  const abandoned = executions.filter((e) => e.status === 'abandoned');
  const auth = document.authorization;
  const checkpoint = document.checkpoint;
  const recommendations = document.recommendations;
  const steps: (Step & { recorded: boolean })[] = [
    {
      id: 'auth',
      label: 'Traveler authorized',
      detail: isObserved(auth) ? `${document.traveler_id} · ${auth.decision}` : '',
      done: isObserved(auth) && auth.decision === 'allow',
      recorded: isObserved(auth),
    },
    {
      id: 'retrieved',
      label: 'Alternatives retrieved',
      detail: isObserved(recommendations)
        ? `${recommendations.items.length} options in the checkpoint` : '',
      done: true,
      recorded: isObserved(recommendations),
    },
    {
      id: 'checkpoint',
      label: 'Checkpoint persisted',
      detail: isObserved(checkpoint) ? checkpoint.checkpoint_id : '',
      done: true,
      recorded: isObserved(checkpoint),
    },
    {
      // Only an abandoned execution is an interruption. A worker that holds
      // a live lease is running, and the rail says nothing about it.
      id: 'interrupted',
      label: 'Worker interrupted',
      detail: abandoned.length ? `${abandoned[0].worker_id} abandoned` : '',
      done: true,
      recorded: abandoned.length > 0,
    },
    {
      id: 'resumed',
      label: 'Saved plan resumed',
      detail: 'Completed from the saved checkpoint',
      done: true,
      recorded: hasVerifiedResume(document),
    },
  ];
  return steps.filter((step) => step.recorded);
}

export function JourneyContinuityRail({
  document,
  error,
  loading = false,
  thread = null,
  running = false,
}: {
  document: JourneyDocument | null;
  error: string | null;
  loading?: boolean;
  /** The recovery thread on the desk, whose run this rail may be watching. */
  thread?: string | null;
  /** Whether that recovery's request is in flight. */
  running?: boolean;
}) {
  const steps = stepsFor(document);
  // Rows fade in, and their marks settle, only as a watched run records them.
  const live = useLiveCues(thread, running);

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

      {steps.length ? (
        <ol className="mds-continuity-steps">
          {steps.map((step) => (
            <motion.li
              key={step.id}
              className={step.done ? 'is-done' : ''}
              initial={live ? { opacity: 0 } : false}
              animate={{ opacity: 1 }}
              transition={STATE_CHANGE}
            >
              <span className="mds-continuity-mark" aria-hidden="true">
                <motion.span
                  key={step.done ? 'done' : 'open'}
                  initial={live ? { opacity: 0, scale: 0.6 } : false}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={STATE_CHANGE}
                >
                  {step.done ? <Check size={13} strokeWidth={3} /> : <Circle size={13} />}
                </motion.span>
              </span>
              <span className="mds-continuity-copy">
                <strong>{step.label}</strong>
                <small>{step.detail}</small>
              </span>
            </motion.li>
          ))}
        </ol>
      ) : (
        <p className="mds-continuity-empty">
          {loading
            ? 'Waiting for Aurora to record the first step.'
            : 'Start a recovery to see each step persist.'}
        </p>
      )}

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
            {error ? 'evidence unavailable' : loading ? 'reading…' : 'no recovery selected'}
          </code>
        )}
        <span className="mds-continuity-source">
          <ShieldCheck size={13} aria-hidden="true" />
          {error ? 'Read failed; any displayed evidence is from the last load.' : document ? 'Source: Aurora journey records.' : loading ? 'Waiting for current Aurora journey evidence.' : 'Start a recovery or open a saved journey.'}
        </span>
      </footer>
    </div>
  );
}

export default JourneyContinuityRail;
