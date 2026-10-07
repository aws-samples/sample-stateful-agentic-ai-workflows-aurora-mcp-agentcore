import { hasLiveLease, hasVerifiedResume, parseDatabaseTime, useEvidenceClock } from '../journey/evidence';
import { HoldReceipt } from '../components/HoldReceipt';
import { AuroraIcon } from '../components/ServiceMark';
import { useState } from 'react';
import { ArrowRight, RefreshCw, ShieldCheck, Terminal } from 'lucide-react';

import type { JourneyDocument, JourneyExecution, SessionStop } from '../journey/types';
import { isObserved } from '../journey/types';
import { useSession } from '../../auth/SessionContext';

type TabId = 'checkpoint' | 'authorization' | 'business';

const TABS: { id: TabId; label: string }[] = [
  { id: 'checkpoint', label: 'Saved step' },
  { id: 'authorization', label: 'Authorization' },
  { id: 'business', label: 'Business result' },
];

const NOT_RECORDED = 'Not recorded';

function shortTime(value: string | null | undefined): string {
  if (!value) return NOT_RECORDED;
  const parsed = new Date(parseDatabaseTime(value));
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toISOString().slice(11, 19) + 'Z';
}

/** A fact that came from the database, with the source it came from. */
function Fact({
  label,
  value,
  mono,
  tone,
}: {
  label: string;
  value: string;
  mono?: boolean;
  tone?: 'good' | 'muted';
}) {
  return (
    <div className="mds-proof-fact">
      <span className="mds-proof-fact-label">{label}</span>
      <span
        className={`mds-proof-fact-value${mono ? ' is-mono' : ''}${
          tone ? ` is-${tone}` : ''
        }`}
      >
        {value}
      </span>
    </div>
  );
}

/** The microVM id, or the whole worker id for journeys saved before Runtime ids existed. */
function microvmOf(execution: JourneyExecution): string {
  return execution.microvm_id ?? execution.worker_id;
}

function sessionChanged(first: JourneyExecution | null, latest: JourneyExecution | null): boolean {
  if (!first || !latest) return false;
  if (first.microvm_id && latest.microvm_id) return first.microvm_id !== latest.microvm_id;
  return first.worker_id !== latest.worker_id;
}

const STOP_WORDING: Record<SessionStop['stopped_during'], string> = {
  waiting: 'Stopped while waiting',
  running: 'Stopped mid-run',
  finished: 'Stopped after the run finished',
};

function stopSummary(stop: SessionStop): string {
  const how = STOP_WORDING[stop.stopped_during] ?? 'Stopped';
  return stop.last_step ? `${how}, last step ${stop.last_step}` : how;
}

function leaseNote(execution: JourneyExecution | null, running: boolean): string {
  if (!execution) return 'No execution recorded';
  const { status } = execution;
  if (status === 'abandoned') return 'Lease expired; execution abandoned';
  if (status === 'failed') return 'Execution failed';
  if (running) return 'Holding the lease';
  if (status !== 'running') return status;
  return execution.lease_expires_at ? 'Lease expired' : 'Lease not verified';
}

function SessionCard({
  role,
  execution,
  fallback,
  now,
}: {
  role: string;
  execution: JourneyExecution | null;
  fallback: string;
  now: number;
}) {
  const status = execution?.status ?? 'none';
  const stopped = status === 'abandoned' || status === 'failed';
  const running = hasLiveLease(execution, now);
  return (
    <div
      className={`mds-proof-worker${stopped ? ' is-stopped' : ''}${
        running ? ' is-running' : ''
      }`}
    >
      <Terminal size={18} aria-hidden="true" className="mds-proof-worker-glyph" />
      <span className="mds-proof-worker-role">{role}</span>
      <strong className="mds-proof-worker-id">
        {execution ? execution.runtime_session_id ?? NOT_RECORDED : fallback}
      </strong>
      {execution && <span className="mds-proof-worker-vm">{microvmOf(execution)}</span>}
      <span className="mds-proof-worker-note">{leaseNote(execution, running)}</span>
      <span className="mds-proof-worker-state">
        {execution ? `attempt ${execution.attempt}, ${status}` : NOT_RECORDED}
      </span>
    </div>
  );
}

/** Presenter proof: the durable state, read back from Aurora.
 *
 * Every value here is served by `GET /api/journeys/{id}`, which assembles it
 * from `checkpoints`, `journey_executions`, `hold_requests`, `bookings` and
 * `traveler_access_audit`. Nothing on this surface is simulated, so where the
 * database has no evidence the surface says so instead of showing a number.
 */
export function PresenterProof({
  document,
  loading,
  error,
  onRefresh,
  onOpenRecovery,
}: {
  document: JourneyDocument | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onOpenRecovery?: () => void;
}) {
  const [tab, setTab] = useState<TabId>('checkpoint');
  const now = useEvidenceClock(document);
  const { traveler: signedIn } = useSession();
  const signedInAs = signedIn
    ? (signedIn.displayName ? `${signedIn.displayName} (${signedIn.travelerId})` : signedIn.travelerId)
    : 'Not confirmed';

  if (error && !document) {
    return (
      <section className="mds-proof-surface" aria-label="System evidence">
        <header className="mc-evidence-intro"><h1>System evidence</h1><p>See which worker ran and what Aurora saved.</p></header>
        <div className="mds-proof-empty" role="status">
          <AuroraIcon size={56} aria-hidden="true" />
          <h2>Journey evidence is unavailable.</h2>
          <p>{error}</p>
          {onOpenRecovery && <button type="button" className="mds-proof-refresh" onClick={onOpenRecovery}>Open recovery desk</button>}
          <button type="button" className="mds-proof-refresh" onClick={onRefresh}>
            <RefreshCw size={15} aria-hidden="true" /> Check again
          </button>
        </div>
      </section>
    );
  }

  if (!document) {
    return (
      <section className="mds-proof-surface" aria-label="System evidence">
        <header className="mc-evidence-intro"><h1>System evidence</h1><p>Read the saved steps, access checks, and package hold from Aurora.</p></header>
        <div className="mds-proof-empty" role="status" aria-busy={loading}>
          <AuroraIcon size={56} aria-hidden="true" />
          <h2>{loading ? 'Reading the journey from Aurora…' : 'No recovery selected'}</h2>
          {!loading && <p>Open a saved recovery or start one to inspect its recorded evidence.</p>}
          {!loading && onOpenRecovery && (
            <button type="button" className="mds-proof-refresh" onClick={onOpenRecovery}>
              Open recovery desk
            </button>
          )}
        </div>
      </section>
    );
  }

  const executions = isObserved(document.executions) ? document.executions.items : [];
  const first = executions[0] ?? null;
  const latest = executions.length > 1 ? executions[executions.length - 1] : null;
  const checkpoint = document.checkpoint;
  const hold = document.hold;
  const auth = document.authorization;
  const restarted = sessionChanged(first, latest);
  const stops = document.session_stops && isObserved(document.session_stops)
    ? document.session_stops.items : [];
  const newestStop = stops[0] ?? null;
  const nextNode = isObserved(document.workflow) ? document.workflow.next_nodes?.[0] : undefined;
  const resumed = hasVerifiedResume(document);
  const waitingToResume = !resumed && isObserved(document.workflow)
    && document.workflow.workflow_status === 'paused';
  const holdGone = !isObserved(hold) && Boolean(hold.checkpoint_hold_id);
  const holdSummary = isObserved(hold)
    ? `${hold.hold_records} hold record${hold.hold_records === 1 ? '' : 's'}.`
    : holdGone ? 'Hold no longer in Aurora.' : 'No hold recorded.';

  return (
    <section className="mds-proof-surface" aria-label="System evidence">
      <header className="mds-proof-head">
        <div>
          <h1>
            {restarted ? 'The session changed.' : isObserved(checkpoint) ? 'The plan is saved.' : 'The journey has started.'}
            <span>{resumed ? 'The saved plan resumed.' : isObserved(checkpoint) ? 'The saved step remains.' : 'The evidence follows.'}</span>
          </h1>
        </div>
        <div className="mds-proof-head-meta">
          <p>A Tokyo recovery plan.</p>
          <p>
            {isObserved(checkpoint) ? 'Saved step recorded.' : 'Awaiting saved step.'}
            {' '}{holdSummary}
          </p>
          <button
            type="button"
            className="mds-proof-refresh"
            onClick={onRefresh}
            disabled={loading}
            aria-label="Re-read this journey from Aurora"
          >
            <RefreshCw size={15} aria-hidden="true" />
            {loading ? 'Reading…' : 'Re-read from Aurora'}
          </button>
        </div>
      </header>

      {error && <p className="mds-proof-read-error" role="alert">
        Re-read failed. Showing the last successful observation
        {document.observed_at ? ` (${shortTime(document.observed_at)})` : ''}. {error}
      </p>}

      <div className="mds-proof-flow">
        <SessionCard now={now} role="Original session" execution={first} fallback="none yet" />
        <span className="mds-proof-arrow" aria-hidden="true">
          <ArrowRight size={22} />
        </span>
        <div className="mds-proof-store">
          <div className="mds-proof-store-head">
            <AuroraIcon size={19} aria-hidden="true" />
            <strong>{document.checkpoint_backend.kind}</strong>
          </div>
          <p className="mds-proof-store-table">workflow_snapshots</p>
          <dl className="mds-proof-store-rows">
            <div>
              <dt>Journey</dt>
              <dd>{document.journey_id}</dd>
            </div>
            <div>
              <dt>Thread</dt>
              <dd>{document.active_thread_id ?? NOT_RECORDED}</dd>
            </div>
            {isObserved(checkpoint) && (
              <>
                <div>
                  <dt>Snapshots</dt>
                  <dd>{checkpoint.snapshot_count ?? NOT_RECORDED}</dd>
                </div>
                <div>
                  <dt>Latest snapshot</dt>
                  <dd>
                    {checkpoint.status ?? NOT_RECORDED}
                    {nextNode ? `, next ${nextNode}` : ''}
                  </dd>
                </div>
              </>
            )}
            <div>
              <dt>Hold</dt>
              <dd>
                {isObserved(hold) ? hold.hold_request_id
                  : holdGone ? 'No longer in Aurora' : 'Not created yet'}
              </dd>
            </div>
          </dl>
          <p
            className={`mds-proof-store-foot${
              isObserved(checkpoint) ? ' is-good' : ''
            }`}
          >
            <ShieldCheck size={15} aria-hidden="true" />
            {isObserved(checkpoint) ? 'Saved step kept' : 'Awaiting saved step'}
          </p>
        </div>
        <span className="mds-proof-arrow" aria-hidden="true">
          <ArrowRight size={22} />
        </span>
        <SessionCard now={now} role={restarted ? "Replacement session" : "Latest execution"} execution={latest} fallback="waiting" />
      </div>

      {newestStop && (
        <p className="mds-proof-stop">
          {stopSummary(newestStop)}
        </p>
      )}

      <div className="mds-proof-tabs" role="tablist" aria-label="Evidence">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`proof-tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`proof-panel-${id}`}
            className={`mds-proof-tab${tab === id ? ' is-active' : ''}`}
            tabIndex={tab === id ? 0 : -1}
            onKeyDown={event => {
              const current = TABS.findIndex(item => item.id === tab);
              const next = event.key === 'ArrowRight' ? (current + 1) % TABS.length
                : event.key === 'ArrowLeft' ? (current + TABS.length - 1) % TABS.length
                : event.key === 'Home' ? 0 : event.key === 'End' ? TABS.length - 1 : -1;
              if (next < 0) return;
              event.preventDefault();
              setTab(TABS[next].id);
              window.document.getElementById(`proof-tab-${TABS[next].id}`)?.focus();
            }}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div
        className="mds-proof-panel"
        role="tabpanel"
        id={`proof-panel-${tab}`}
        aria-labelledby={`proof-tab-${tab}`}
      >
        {tab === 'checkpoint' && (
          <div className="mds-proof-facts">
            <Fact
              label="Snapshot ID"
              mono
              value={isObserved(checkpoint) ? checkpoint.checkpoint_id : NOT_RECORDED}
            />
            <Fact label="Owner" value={document.traveler_id} />
            <Fact label="Signed in as" value={signedInAs} />
            <Fact
              label="Selected package"
              value={
                isObserved(document.selected_plan)
                  ? document.selected_plan.package_id
                  : isObserved(document.pending_decision)
                    ? document.pending_decision.package_id
                    : NOT_RECORDED
              }
            />
            <Fact
              label="Resume result"
              tone={resumed ? 'good' : 'muted'}
              value={resumed ? 'Completed from the saved step'
                : waitingToResume ? 'Not resumed yet' : 'Successful resume not verified'}
            />
          </div>
        )}

        {tab === 'authorization' && (
          <div className="mds-proof-auth">
            <ShieldCheck size={20} aria-hidden="true" />
            {isObserved(auth) ? (
              <div>
                <strong>
                  {document.traveler_id} owns {document.active_thread_id ?? document.journey_id}.
                </strong>
                <p>
                  Recorded {shortTime(auth.observed_at)} as{' '}
                  <b>{auth.decision.toUpperCase()}</b>: {auth.reason ?? 'no reason recorded'}.
                </p>
                <p className="mds-proof-subject">{auth.subject}</p>
              </div>
            ) : (
              <div>
                <strong>No authorization decision recorded.</strong>
                <p>{'reason' in auth ? auth.reason : ''}</p>
              </div>
            )}
          </div>
        )}

        {tab === 'business' && (
          <div className="mc-hold-evidence">
            {isObserved(hold) ? (
              <>
                <HoldReceipt holdId={hold.booking_id} createdAt={hold.hold_created_at} expiresAt={hold.hold_expires_at} observedAt={hold.observed_at} receivedAt={document.received_at} confirmedAt={hold.confirmed_at} status={hold.status} />
                <div className="mds-proof-facts">
                  <Fact label="Request identity" mono value={hold.hold_request_id} />
                  <Fact label="Travel party" value={hold.travelers_count ? `${hold.travelers_count} travelers` : NOT_RECORDED} />
                  <Fact label="Created by" value={hold.created_by_execution_id === first?.execution_id ? 'Original execution' : hold.created_by_execution_id === latest?.execution_id ? (restarted ? 'Replacement execution' : 'Resumed execution') : hold.created_by_execution_id || 'Not recorded'} />
                  <Fact label="Hold records in journey" value={String(hold.hold_records)} />
                  <Fact label="Confirmed" value={hold.confirmed_at ? `${shortTime(hold.confirmed_at)}, catalog inventory, no payment` : 'Not yet confirmed by the traveler'} />
                </div>
                <p className="mc-hold-proof-note">
                  {restarted && hold.created_by_execution_id === first?.execution_id
                    ? 'This booking was created by the original execution and is still readable after the replacement started.'
                    : restarted && hold.created_by_execution_id === latest?.execution_id
                      ? 'The replacement execution created this hold. This run shows recovery from a saved step; it does not yet prove an existing hold survived a restart.'
                      : 'To prove hold durability, stop the worker after the hold is committed, resume the same thread, then re-read the booking ID and original expiry.'}
                </p>
              </>
            ) : holdGone ? (
              <div className="mc-hold-pending">
                <strong>Hold no longer in Aurora.</strong>
                <p>
                  The saved step names hold {hold.checkpoint_hold_id}, but Aurora has no booking
                  for it. Releasing a sample booking removes its rows.
                </p>
              </div>
            ) : (
              <div className="mc-hold-pending">
                <strong>No package hold recorded.</strong>
                <p>A saved shortlist is only a saved step in the workflow. The hold begins after availability verification, when Aurora commits the booking.</p>
              </div>
            )}
          </div>
        )}
      </div>

      <footer className="mds-proof-foot">
        <span>Aurora, MCP, AgentCore, Strands Graph</span>
        <span>
          Read from {document.checkpoint_backend.kind}
          {document.checkpoint_backend.durable ? ', survives restart' : document.checkpoint_backend.kind === 'MemorySaver (in-process)' ? ', worker memory only' : ', durability not verified'}
        </span>
      </footer>
    </section>
  );
}

export default PresenterProof;
