import { hasLiveLease, hasVerifiedResume, useEvidenceClock } from '../journey/evidence';
import { HoldReceipt } from '../components/HoldReceipt';
import { AuroraIcon } from '../components/ServiceMark';
import { useState } from 'react';
import { ArrowRight, RefreshCw, ShieldCheck, Terminal } from 'lucide-react';

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
      <strong className="mds-proof-worker-id">{execution?.worker_id ?? fallback}</strong>
      <span className="mds-proof-worker-note">
        {execution
          ? stopped
            ? status === 'abandoned' ? 'Lease expired; execution abandoned' : 'Execution failed'
            : running
              ? 'Holding the lease'
              : status === 'running' ? execution?.lease_expires_at ? 'Lease expired' : 'Lease not verified' : status
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

  if (error && !document) {
    return (
      <section className="mds-proof-surface" aria-label="System evidence">
        <header className="mc-evidence-intro"><h1>System evidence</h1><p>See which worker ran and what Aurora saved.</p></header>
        <div className="mds-proof-empty" role="status">
          <AuroraIcon size={22} aria-hidden="true" />
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
          <AuroraIcon size={22} aria-hidden="true" />
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
  const restarted = Boolean(first && latest && first.worker_id !== latest.worker_id);
  const resumed = hasVerifiedResume(document);

  return (
    <section className="mds-proof-surface" aria-label="System evidence">
      <header className="mds-proof-head">
        <div>
          <h1>
            {restarted ? 'The worker changed.' : isObserved(checkpoint) ? 'The plan is saved.' : 'The journey has started.'}
            <span>{resumed ? 'The saved plan resumed.' : isObserved(checkpoint) ? 'The checkpoint remains.' : 'The evidence follows.'}</span>
          </h1>
        </div>
        <div className="mds-proof-head-meta">
          <p>A Tokyo recovery plan.</p>
          <p>{isObserved(checkpoint) ? 'Checkpoint recorded.' : 'Awaiting checkpoint.'} {isObserved(hold) ? `${hold.hold_records} hold record${hold.hold_records === 1 ? '' : 's'}.` : 'No hold recorded.'}</p>
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

      <div className="mds-proof-flow">
        <WorkerCard now={now} role="Original worker" execution={first} fallback="none yet" />
        <span className="mds-proof-arrow" aria-hidden="true">
          <ArrowRight size={22} />
        </span>
        <div className="mds-proof-store">
          <div className="mds-proof-store-head">
            <AuroraIcon size={19} aria-hidden="true" />
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
          <ArrowRight size={22} />
        </span>
        <WorkerCard now={now} role={restarted ? "Replacement worker" : "Latest execution"} execution={latest} fallback="waiting" />
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
              tone={resumed ? 'good' : 'muted'}
              value={resumed ? 'Completed from saved checkpoint' : 'Successful resume not verified'}
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
          <div className="mc-hold-evidence">
            {isObserved(hold) ? (
              <>
                <HoldReceipt holdId={hold.booking_id} createdAt={hold.hold_created_at} expiresAt={hold.hold_expires_at} observedAt={hold.observed_at} receivedAt={document.received_at} status={hold.status} />
                <div className="mds-proof-facts">
                  <Fact label="Request identity" mono value={hold.hold_request_id} />
                  <Fact label="Travel party" value={hold.travelers_count ? `${hold.travelers_count} travelers` : DASH} />
                  <Fact label="Created by" value={hold.created_by_execution_id === first?.execution_id ? 'Original execution' : hold.created_by_execution_id === latest?.execution_id ? 'Replacement execution' : hold.created_by_execution_id || 'Not recorded'} />
                  <Fact label="Hold records in journey" value={String(hold.hold_records)} />
                </div>
                <p className="mc-hold-proof-note">
                  {restarted && hold.created_by_execution_id === first?.execution_id
                    ? 'This booking was created by the original execution and is still readable after the replacement started.'
                    : restarted && hold.created_by_execution_id === latest?.execution_id
                      ? 'The replacement execution created this hold. This run proves checkpoint recovery; it does not yet prove an existing hold survived a restart.'
                      : 'To prove hold durability, stop the worker after the hold is committed, resume the same thread, then re-read the booking ID and original expiry.'}
                </p>
              </>
            ) : (
              <div className="mc-hold-pending">
                <strong>No package hold recorded.</strong>
                <p>A saved shortlist is a workflow checkpoint. The hold begins after availability verification, when Aurora commits the booking.</p>
              </div>
            )}
          </div>
        )}
      </div>

      <footer className="mds-proof-foot">
        <span>Aurora · MCP · AgentCore · Strands · LangGraph</span>
        <span>
          Read from {document.checkpoint_backend.kind}
          {document.checkpoint_backend.durable ? ' · survives restart' : document.checkpoint_backend.kind === 'MemorySaver (in-process)' ? ' · worker memory only' : ' · durability not verified'}
        </span>
      </footer>
    </section>
  );
}

export default PresenterProof;
