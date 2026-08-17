import { useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  BadgeDollarSign,
  CalendarDays,
  Check,
  ChevronDown,
  Circle,
  Clock3,
  Database,
  Heart,
  Loader2,
  MapPin,
  Sparkles,
  Star,
  UserRound,
  UsersRound,
} from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import {
  deriveRecoveryEvidence,
  deriveRecoveryStage,
} from '../lib/recoveryState';

interface ActivityRow {
  label: string;
  detail: string;
  status: 'pending' | 'running' | 'done' | 'unobserved';
}

function preferenceSummary(state: MeridianShowcaseState): string {
  const facts = (state.memoryFacts ?? [])
    .map((fact) => fact.value)
    .filter(Boolean)
    .slice(0, 2);
  return facts.length ? facts.join(', ') : 'Window seat, early arrival';
}

function activityRows(state: MeridianShowcaseState): ActivityRow[] {
  const stage = deriveRecoveryStage(state);
  const running = stage === 'running';
  const settled = stage === 'checkpointed' || stage === 'ready';
  const evidence = deriveRecoveryEvidence(state);
  const traceSpans = state.traceSpans ?? [];
  const searchSpans = traceSpans.filter((span) =>
    /search|retriev|semantic|hybrid|catalog/i.test(
      `${span.name} ${span.details ?? ''} ${span.component ?? ''}`,
    ),
  ).length;
  const memorySpans = traceSpans.filter((span) =>
    /aurora recall|traveler memory|memoryagent|preference context/i.test(
      `${span.name} ${span.details ?? ''} ${span.component ?? ''}`,
    ),
  ).length;
  const checkpointSpans = traceSpans.filter((span) =>
    /checkpoint|postgres.?saver|workflow state/i.test(
      `${span.name} ${span.details ?? ''}`,
    ),
  ).length;
  const recommendations = state.recommendations?.length ?? 0;

  return [
    {
      label: 'Search',
      detail: searchSpans ? `${searchSpans} governed search spans` : 'Awaiting live search',
      status: evidence.searchObserved
        ? 'done'
        : running
          ? 'running'
          : settled
            ? 'unobserved'
            : 'pending',
    },
    {
      label: 'Alternatives',
      detail: recommendations ? `${recommendations} options returned` : 'Awaiting live results',
      status: evidence.alternativesObserved
        ? 'done'
        : settled
          ? 'unobserved'
          : 'pending',
    },
    {
      label: 'Loyalty perks',
      detail: evidence.loyaltyObserved
        ? 'Airline Premier applied'
        : settled
          ? 'Not observed in this run'
          : 'Profile check pending',
      status: evidence.loyaltyObserved
        ? 'done'
        : settled
          ? 'unobserved'
          : 'pending',
    },
    {
      label: 'Traveler memory',
      detail: evidence.memoryObserved && memorySpans
        ? `${memorySpans} recall spans`
        : settled
          ? 'Not observed in this run'
          : 'Recall pending',
      status: evidence.memoryObserved
        ? 'done'
        : settled
          ? 'unobserved'
          : 'pending',
    },
    {
      label: 'Checkpoint',
      detail: checkpointSpans
        ? `${checkpointSpans} durable state span${checkpointSpans === 1 ? '' : 's'}`
        : 'Aurora checkpoint pending',
      status: evidence.checkpointObserved
        ? 'done'
        : running
          ? 'running'
          : settled
            ? 'unobserved'
            : 'pending',
    },
  ];
}

function ActivityIcon({ status }: { status: ActivityRow['status'] }) {
  if (status === 'done') return <Check size={12} strokeWidth={3} />;
  if (status === 'running') return <Loader2 size={13} strokeWidth={2.2} />;
  return <Circle size={10} strokeWidth={1.8} />;
}

export function JourneyPanel({
  state,
  onOpenProof = () => {},
}: {
  state: MeridianShowcaseState;
  onOpenProof?: () => void;
}) {
  const recoveryStage = deriveRecoveryStage(state);
  const partySize = state.travelerProfile?.party_size ?? 2;
  const rows = activityRows(state);
  const [travelContextExpanded, setTravelContextExpanded] = useState(false);

  return (
    <aside className="mds-journey-panel" aria-label="Current trip and travel context">
      <section className="mds-journey-section mds-current-trip-section">
        <header className="mds-journey-section-head">
          <h2>Current trip</h2>
          <span className={`mds-current-trip-state is-${recoveryStage}`}>
            {recoveryStage === 'ready'
              ? <Check size={12} />
              : recoveryStage === 'checkpointed'
                ? <Database size={12} />
              : recoveryStage === 'running'
                ? <Loader2 size={12} />
                : <AlertTriangle size={12} />}
            {recoveryStage === 'ready'
              ? 'Plan ready'
              : recoveryStage === 'checkpointed'
                ? 'Checkpoint saved'
              : recoveryStage === 'running'
                ? 'Recovering'
                : 'Action needed'}
          </span>
        </header>

        <div className={`mds-current-trip-card is-${recoveryStage}`}>
          <div className="mds-current-trip-topline">
            <span>Traveler report</span>
            <b>
              {recoveryStage === 'ready' ? (
                <Check size={14} aria-hidden="true" />
              ) : recoveryStage === 'checkpointed' ? (
                <Database size={14} aria-hidden="true" />
              ) : recoveryStage === 'running' ? (
                <Loader2 size={14} aria-hidden="true" />
              ) : (
                <AlertTriangle size={14} aria-hidden="true" />
              )}
              {recoveryStage === 'ready'
                ? 'Recovery plan ready'
                : recoveryStage === 'checkpointed'
                  ? 'Shortlist saved'
                : recoveryStage === 'running'
                  ? 'Checking alternatives'
                  : 'Canceled'}
            </b>
          </div>
          <div className="mds-current-trip-route" aria-label="New York JFK to Tokyo">
            <div>
              <strong>JFK</strong>
              <span>New York</span>
            </div>
            <span className="mds-current-trip-line" aria-hidden="true">
              <i />
              <ArrowRight size={18} />
              <i />
            </span>
            <div>
              <strong>TYO</strong>
              <span>Tokyo</span>
            </div>
          </div>
          <div className="mds-current-trip-meta">
            <span><CalendarDays size={14} />Today / 10:40 AM</span>
            <span><UsersRound size={14} />{partySize} travelers</span>
          </div>
          <div className="mds-current-trip-loyalty">
            <span>Airline Premier</span>
            <b>Elite status recognized</b>
          </div>
          <ol className={`mds-recovery-steps is-${recoveryStage}`} aria-label="Recovery workflow progress">
            <li className="is-complete"><i />Disruption</li>
            <li className={recoveryStage === 'action' ? '' : 'is-complete'}><i />Alternatives</li>
            <li className={['checkpointed', 'ready'].includes(recoveryStage) ? 'is-complete' : ''}><i />Checkpoint</li>
          </ol>
        </div>
      </section>

      <section
        className={`mds-journey-section mds-travel-context-section${travelContextExpanded ? ' is-expanded' : ' is-collapsed'}`}
      >
        <header className="mds-journey-section-head mds-travel-context-head">
          <button
            type="button"
            className="mds-travel-context-toggle"
            aria-expanded={travelContextExpanded}
            aria-controls="mds-travel-context-details"
            onClick={() => setTravelContextExpanded((expanded) => !expanded)}
          >
            <span>
              <h2>Travel context</h2>
              <small>
                {state.travelerProfile?.home_airport ?? 'JFK'} · {partySize} travelers · Airline Premier
              </small>
            </span>
            <ChevronDown size={18} aria-hidden="true" />
          </button>
        </header>
        {travelContextExpanded && (
          <dl className="mds-travel-context-list" id="mds-travel-context-details">
            <div>
              <dt><MapPin size={16} />From</dt>
              <dd><strong>{state.travelerProfile?.home_airport ?? 'JFK'}</strong><span>New York</span></dd>
            </div>
            <div>
              <dt><UserRound size={16} />Travelers</dt>
              <dd><strong>{partySize}</strong></dd>
            </div>
            <div>
              <dt><BadgeDollarSign size={16} />Budget</dt>
              <dd><strong>{state.travelerProfile?.budget_max ? `$${state.travelerProfile.budget_max.toLocaleString()}` : 'Flexible'}</strong></dd>
            </div>
            <div>
              <dt><Star size={16} />Loyalty</dt>
              <dd><strong>Airline Premier</strong></dd>
            </div>
            <div>
              <dt><Heart size={16} />Preferences</dt>
              <dd><span>{preferenceSummary(state)}</span></dd>
            </div>
          </dl>
        )}
      </section>

      <section className="mds-journey-section mds-agent-activity-section">
        <header className="mds-journey-section-head">
          <h2>Agent activity</h2>
          <span className={`mds-activity-live${state.backendStatus === 'online' ? ' is-live' : ''}`}>
            <i />
            {state.backendStatus === 'online' ? 'Live' : 'Waiting'}
          </span>
        </header>
        <div className="mds-agent-activity-list">
          {rows.map((row) => (
            <div className={`mds-agent-activity-row is-${row.status}`} key={row.label}>
              <span className="mds-agent-activity-icon" aria-hidden="true">
                <ActivityIcon status={row.status} />
              </span>
              <strong>{row.label}</strong>
              <span>{row.detail}</span>
              <small>
                {row.status === 'done'
                  ? 'verified'
                  : row.status === 'running'
                    ? 'live'
                    : row.status === 'unobserved'
                      ? 'not observed'
                      : 'pending'}
              </small>
            </div>
          ))}
        </div>
        <button
          type="button"
          className="mds-activity-log-button"
          onClick={onOpenProof}
        >
          <Sparkles size={15} />
          View full activity log
          <ArrowRight size={16} />
        </button>
      </section>

      <div className="mds-journey-footnote" aria-hidden="true">
        <Clock3 size={13} />
        State updates as the live workflow runs
      </div>
    </aside>
  );
}
