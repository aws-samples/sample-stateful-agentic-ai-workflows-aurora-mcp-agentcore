import { useEffect, useRef, useState } from 'react';
import { motion } from 'motion/react';
import { hasVerifiedResume } from '../journey/evidence';
import { canStopSession } from '../journey/sessionStop';
import { AuroraIcon } from '../components/ServiceMark';
import { STATE_CHANGE, useLiveCues } from '../hooks/useLiveCues';
import { Check, Circle, ShieldCheck } from 'lucide-react';

import type { JourneyDocument, SessionStop } from '../journey/types';
import { isObserved } from '../journey/types';

type Step = {
  id: string;
  label: string;
  /** What Aurora holds when this step has happened. */
  detail: string;
  done: boolean;
};

function stopStep(stop: SessionStop | undefined): { label: string; detail: string } {
  if (!stop) return { label: 'Session stopped', detail: '' };
  if (stop.stopped_during === 'finished') {
    return { label: 'Session stopped after the run finished', detail: 'the hold was already recorded' };
  }
  const running = stop.stopped_during === 'running';
  const label = running ? 'Session stopped mid-run' : 'Session stopped while waiting';
  if (stop.outcome === 'not_running') return { label, detail: 'the session had already ended' };
  if (!running) return { label, detail: 'for the review answer' };
  return { label, detail: stop.last_step ? `after ${stop.last_step}, lease released` : 'lease released' };
}

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
  const stops = isObserved(document.session_stops) ? document.session_stops.items : [];
  const stopped = stopStep(stops[0]);
  const auth = document.authorization;
  const checkpoint = document.checkpoint;
  const recommendations = document.recommendations;
  const steps: (Step & { recorded: boolean })[] = [
    {
      id: 'auth',
      label: 'Traveler authorized',
      detail: isObserved(auth) ? `${document.traveler_id}, ${auth.decision}` : '',
      done: isObserved(auth) && auth.decision === 'allow',
      recorded: isObserved(auth),
    },
    {
      id: 'retrieved',
      label: 'Alternatives retrieved',
      detail: isObserved(recommendations)
        ? `${recommendations.items.length} options in the saved step` : '',
      done: true,
      recorded: isObserved(recommendations),
    },
    {
      id: 'checkpoint',
      label: 'Saved step',
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
      id: 'stopped',
      label: stopped.label,
      detail: stopped.detail,
      done: true,
      recorded: stops.length > 0,
    },
    {
      id: 'resumed',
      label: 'Saved plan resumed',
      detail: 'Completed from the saved step',
      done: true,
      recorded: hasVerifiedResume(document),
    },
  ];
  return steps.filter((step) => step.recorded);
}

function StopSessionControl(
  { onStop, onStopped }: { onStop: () => Promise<void>; onStopped: () => void },
) {
  const [phase, setPhase] = useState<'idle' | 'confirm' | 'stopping'>('idle');
  const [failure, setFailure] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const opened = useRef(false);

  useEffect(() => {
    if (phase === 'confirm') {
      opened.current = true;
      keep.current?.focus();
    } else if (phase === 'idle' && opened.current) {
      opened.current = false;
      trigger.current?.focus();
    }
  }, [phase]);

  const confirm = async () => {
    setPhase('stopping');
    setFailure(null);
    try {
      await onStop();
    } catch (err) {
      setFailure(err instanceof Error ? err.message : 'Stopping the session failed.');
      setPhase('idle');
      return;
    }
    // The stop is recorded and this control is about to unmount; keep focus in the rail.
    opened.current = false;
    onStopped();
    setPhase('idle');
  };

  return (
    <div className="mds-continuity-stop">
      {phase === 'idle' ? (
        <button ref={trigger} type="button" className="is-secondary"
          onClick={() => { setFailure(null); setPhase('confirm'); }}>
          Stop runtime session
        </button>
      ) : (
        <div role="group" aria-label="Confirm stopping the runtime session">
          <p>
            Stop the AgentCore Runtime session for this journey? The saved steps stay in
            Aurora, and Resume starts a new microVM.
          </p>
          <div className="mds-continuity-stop-actions">
            <button ref={keep} type="button" className="is-secondary" disabled={phase === 'stopping'}
              onClick={() => setPhase('idle')}>
              Keep running
            </button>
            <button type="button" className="is-primary" disabled={phase === 'stopping'}
              onClick={() => void confirm()}>
              {phase === 'stopping' ? 'Stopping…' : 'Stop session'}
            </button>
          </div>
        </div>
      )}
      {failure ? <p role="alert" className="mds-continuity-stop-error">{failure}</p> : null}
    </div>
  );
}

export function JourneyContinuityRail({
  document,
  error,
  loading = false,
  thread = null,
  running = false,
  onStopSession,
}: {
  document: JourneyDocument | null;
  error: string | null;
  loading?: boolean;
  /** The recovery thread on the desk, whose run this rail may be watching. */
  thread?: string | null;
  /** Whether that recovery's request is in flight. */
  running?: boolean;
  /** Stops the journey's Runtime session; offered while it runs or waits. */
  onStopSession?: () => Promise<void>;
}) {
  const steps = stepsFor(document);
  const region = useRef<HTMLDivElement>(null);
  // Rows fade in, and their marks settle, only as a watched run records them.
  const live = useLiveCues(thread, running);

  return (
    <div ref={region} className="mds-continuity-rail" tabIndex={0} role="region" aria-label="Journey progress">
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
            thread: {error ? 'unavailable' : loading ? 'reading…' : '—'}
          </code>
        )}
        {onStopSession && canStopSession(document) ? (
          <StopSessionControl onStop={onStopSession} onStopped={() => region.current?.focus()} />
        ) : null}
        <span className="mds-continuity-source">
          <ShieldCheck size={13} aria-hidden="true" />
          {error ? 'Read failed; any displayed evidence is from the last load.' : document ? 'Source: Aurora journey records.' : loading ? 'Waiting for current Aurora journey evidence.' : 'Start a recovery or open a saved journey.'}
        </span>
      </footer>
    </div>
  );
}

export default JourneyContinuityRail;
