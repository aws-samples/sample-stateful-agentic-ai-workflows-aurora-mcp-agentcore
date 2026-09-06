import { useState } from 'react';
import { Database, RefreshCw, ShieldCheck, Terminal } from 'lucide-react';

import type { JourneyDocument, JourneyExecution } from '../journey/types';
import { isObserved } from '../journey/types';

type TabId = 'checkpoint' | 'authorization' | 'business';

const TABS: { id: TabId; label: string }[] = [
  { id: 'checkpoint', label: 'Checkpoint' },
  { id: 'authorization', label: 'Authorization' },
  { id: 'business', label: 'Business result' },
];

const DASH = '—';

function shortTime(value: string | null | undefined): string {
  if (!value) return DASH;
  const parsed = new Date(value.replace(' ', 'T'));
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

function WorkerCard({
  role,
  execution,
  fallback,
}: {
  role: string;
  execution: JourneyExecution | null;
  fallback: string;
}) {
  const status = execution?.status ?? 'none';
  const stopped = status === 'abandoned' || status === 'failed';
  const running = status === 'running';
  return (
    <div
      className={`mds-proof-worker${stopped ? ' is-stopped' : ''}${
        running ? ' is-running' : ''
      }`}
    >
      <Terminal size={18} aria-hidden="true" className="mds-proof-worker-glyph" />
      <span className="mds-proof-worker-role">{role}</span>
      <strong className="mds-proof-worker-id">{execution?.worker_id ?? fallback}</strong>
      <span className="mds-proof-worker-note">
        {execution
          ? stopped
            ? 'Stopped after the save'
            : running
              ? 'Holding the lease'
              : status
          : 'No execution recorded'}
      </span>
      <span className="mds-proof-worker-state">
        {execution ? `attempt ${execution.attempt} · ${status}` : DASH}
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
}: {
  document: JourneyDocument | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  const [tab, setTab] = useState<TabId>('checkpoint');

  if (error && !document) {
    return (
      <section className="mds-proof-surface" aria-label="Presenter proof">
        <div className="mds-proof-empty" role="status">
          <Database size={22} aria-hidden="true" />
          <h2>No journey to prove yet.</h2>
          <p>{error}</p>
          <button type="button" className="mds-proof-refresh" onClick={onRefresh}>
            <RefreshCw size={15} aria-hidden="true" /> Check again
          </button>
        </div>
      </section>
    );
  }

  if (!document) {
    return (
      <section className="mds-proof-surface" aria-label="Presenter proof">
        <div className="mds-proof-empty" role="status" aria-busy="true">
          <Database size={22} aria-hidden="true" />
          <h2>Reading the journey from Aurora…</h2>
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
  const restarted = executions.length > 1;

  return (
    <section className="mds-proof-surface" aria-label="Presenter proof">
      <header className="mds-proof-head">
        <div>
          <h1>
            {restarted ? 'The worker changed.' : 'The plan is saved.'}
            <span>{restarted ? 'The plan didn’t.' : 'Now stop the worker.'}</span>
          </h1>
        </div>
        <div className="mds-proof-head-meta">
          <p>A Tokyo recovery plan.</p>
          <p>One checkpoint. One durable hold.</p>
          <button
            type="button"
            className="mds-proof-refresh"
            onClick={onRefresh}
            aria-label="Re-read this journey from Aurora"
          >
            <RefreshCw size={15} aria-hidden="true" />
            {loading ? 'Reading…' : 'Re-read from Aurora'}
          </button>
        </div>
      </header>

      <div className="mds-proof-flow">
        <WorkerCard role="Original worker" execution={first} fallback="none yet" />
        <span className="mds-proof-arrow" aria-hidden="true">
          →
        </span>
        <div className="mds-proof-store">
          <div className="mds-proof-store-head">
            <Database size={19} aria-hidden="true" />
            <strong>{document.checkpoint_backend.kind}</strong>
          </div>
          <dl className="mds-proof-store-rows">
            <div>
              <dt>Journey</dt>
              <dd>{document.journey_id}</dd>
            </div>
            <div>
              <dt>Thread</dt>
              <dd>{document.active_thread_id ?? DASH}</dd>
            </div>
            <div>
              <dt>Hold</dt>
              <dd>{isObserved(hold) ? hold.hold_request_id : 'Not created yet'}</dd>
            </div>
          </dl>
          <p
            className={`mds-proof-store-foot${
              isObserved(checkpoint) ? ' is-good' : ''
            }`}
          >
            <ShieldCheck size={15} aria-hidden="true" />
            {isObserved(checkpoint) ? 'Checkpoint persisted' : 'Awaiting checkpoint'}
          </p>
        </div>
        <span className="mds-proof-arrow" aria-hidden="true">
          →
        </span>
        <WorkerCard role="Replacement worker" execution={latest} fallback="waiting" />
      </div>

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
              label="Checkpoint ID"
              mono
              value={isObserved(checkpoint) ? checkpoint.checkpoint_id : DASH}
            />
            <Fact label="Owner" value={document.traveler_id} />
            <Fact
              label="Selected package"
              value={
                isObserved(document.selected_plan)
                  ? document.selected_plan.package_id
                  : isObserved(document.pending_decision)
                    ? document.pending_decision.package_id
                    : DASH
              }
            />
            <Fact
              label="Resume result"
              tone={restarted ? 'good' : 'muted'}
              value={restarted ? 'Same thread, same hold' : 'Not resumed yet'}
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
                  <b>{auth.decision.toUpperCase()}</b> — {auth.reason ?? 'no reason recorded'}.
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
          <div className="mds-proof-facts">
            <Fact
              label="Hold ID"
              mono
              value={isObserved(hold) ? hold.booking_id : DASH}
            />
            <Fact
              label="Hold records"
              tone={isObserved(hold) && hold.hold_records === 1 ? 'good' : undefined}
              value={isObserved(hold) ? String(hold.hold_records) : DASH}
            />
            <Fact
              label="Travel party"
              value={
                isObserved(hold) && hold.travelers_count
                  ? `${hold.travelers_count} travelers`
                  : DASH
              }
            />
            <Fact
              label="After restart"
              tone={isObserved(hold) && hold.hold_records === 1 ? 'good' : undefined}
              value={
                !isObserved(hold)
                  ? DASH
                  : hold.hold_records === 1
                    ? 'No duplicate hold'
                    : `${hold.hold_records} holds — investigate`
              }
            />
          </div>
        )}
      </div>

      <footer className="mds-proof-foot">
        <span>Aurora · MCP · AgentCore · Strands · LangGraph</span>
        <span>
          Read from {document.checkpoint_backend.kind}
          {document.checkpoint_backend.durable ? ' · durable' : ' · in-process'}
        </span>
      </footer>
    </section>
  );
}

export default PresenterProof;
