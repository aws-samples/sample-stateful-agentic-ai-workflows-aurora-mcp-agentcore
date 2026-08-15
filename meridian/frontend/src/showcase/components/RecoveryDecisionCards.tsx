import {
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  Circle,
  Clock3,
  Database,
  FileCheck2,
  GitCompareArrows,
  Headphones,
  LockKeyhole,
  Loader2,
  Plane,
  Route,
  Search,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import type { LongTermMemoryFact, Product, TravelerProfile } from '../../types';
import type {
  RecoveryEvidence,
  RecoveryStage,
} from '../lib/recoveryState';
import { TripVisual } from './TripVisual';

interface DecisionCardAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

interface RecommendedRecoveryPlanCardProps {
  product: Product | null;
  stage: RecoveryStage;
  evidence: RecoveryEvidence;
  memoryFacts: LongTermMemoryFact[];
  travelerProfile: TravelerProfile | null;
  primaryAction: DecisionCardAction;
  secondaryAction: DecisionCardAction;
}

interface FlightOptionCardProps {
  product: Product | null;
  rank: number;
  badge: string;
  availabilityObserved: boolean;
  disabled?: boolean;
  onView: () => void;
  onCompare: () => void;
}

interface ConciergeAssistanceCardProps {
  stage: RecoveryStage;
  evidence: RecoveryEvidence;
  product: Product | null;
  disabled?: boolean;
  onHotel: () => void;
  onProtection: () => void;
}

interface AgentProofCardProps {
  stage: RecoveryStage;
  evidence: RecoveryEvidence;
  recommendationCount: number;
  traceCount: number;
  onViewProof: () => void;
}

interface CheckpointedPlanCardProps {
  stage: RecoveryStage;
  evidence: RecoveryEvidence;
  threadId: string;
  resumedAfterRestart: boolean;
}

interface RecoveryLaunchCardProps {
  stage: RecoveryStage;
  errorDetail?: string | null;
  disabled?: boolean;
  compact?: boolean;
  resumeMode?: boolean;
  onStart: () => void;
}

function money(price: number): string {
  return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

function durationLabel(product: Product): string {
  return product.available_sizes?.[0] ?? 'Flexible duration';
}

function inventoryDetails(product: Product): {
  count: number;
  durations: number;
} {
  const entries = Object.entries(product.availability ?? {}).filter(
    ([, value]) => Number(value) > 0,
  );
  return {
    count: entries.reduce((sum, [, value]) => sum + Number(value), 0),
    durations: entries.length,
  };
}

function availabilityLabel(
  product: Product,
  availabilityObserved: boolean,
): string {
  if (!availabilityObserved) return 'Live duration inventory pending';
  const inventory = inventoryDetails(product);
  if (!inventory.durations) return 'Duration inventory checked';
  return `${inventory.count} places across ${inventory.durations} stay${
    inventory.durations === 1 ? '' : 's'
  }`;
}

function cleanPreference(value?: string | null): string | null {
  if (!value) return null;
  const trimmed = value
    .trim()
    .replace(/^boutique\s*>\s*chain$/i, 'Boutique hotels');
  if (!trimmed || trimmed.length > 28) return null;
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

function preferenceChips(
  evidence: RecoveryEvidence,
  facts: LongTermMemoryFact[],
  profile: TravelerProfile | null,
): string[] {
  const chips: string[] = [];
  if (evidence.memoryObserved) {
    const seat = cleanPreference(profile?.seat_preference);
    if (seat) chips.push(seat);

    const usefulFact = facts.find((fact) =>
      /seat|arrival|lodging|hotel|travel_style|trip_goal/i.test(fact.key),
    );
    const factValue = cleanPreference(usefulFact?.value);
    if (factValue && !chips.includes(factValue)) chips.push(factValue);

    chips.push('Memory match');
  }
  if (evidence.loyaltyObserved) chips.push('Airline Premier');
  return chips.length ? chips.slice(0, 3) : ['Preference match pending'];
}

function travelerContextReasons(
  evidence: RecoveryEvidence,
  facts: LongTermMemoryFact[],
  profile: TravelerProfile | null,
): { label: string; detail: string }[] {
  if (!evidence.memoryObserved) return [];
  const byKey = new Map(facts.map((fact) => [fact.key, fact.value]));
  const reasons: { label: string; detail: string }[] = [];
  const allergy =
    byKey.get('shellfish_allergy') ??
    profile?.dietary_notes;
  if (allergy) {
    reasons.push({
      label: 'Dietary safety',
      detail: `Shellfish allergy flagged for every hotel and dining handoff`,
    });
  }
  const lodging =
    byKey.get('lodging_preference') ??
    byKey.get('lodging_style') ??
    byKey.get('travel_style');
  if (lodging) {
    reasons.push({
      label: 'Stay preference',
      detail: `${cleanPreference(lodging) ?? lodging} carried into concierge search`,
    });
  }
  const seat = cleanPreference(profile?.seat_preference);
  if (seat) {
    reasons.push({
      label: 'Long-haul comfort',
      detail: `${seat} retained for replacement-flight review`,
    });
  }
  if (evidence.loyaltyObserved) {
    reasons.push({
      label: 'Loyalty context',
      detail: 'Airline Premier and Hotel Platinum benefits checked',
    });
  }
  return reasons.slice(0, 3);
}

function stageBadge(stage: RecoveryStage): string {
  if (stage === 'ready') return 'Ready for review';
  if (stage === 'checkpointed') return 'Shortlist checkpointed';
  if (stage === 'running') return 'Ranking live options';
  return 'Awaiting recovery';
}

function RouteTimeline({
  detail,
  compact = false,
}: {
  detail: string;
  compact?: boolean;
}) {
  return (
    <div className={`mds-decision-route${compact ? ' is-compact' : ''}`}>
      <span>
        <b>JFK</b>
        {!compact && <small>New York</small>}
      </span>
      <span className="mds-decision-route-line" aria-hidden="true">
        <i />
        <Plane size={compact ? 13 : 16} />
        <i />
      </span>
      <span>
        <b>HND</b>
        {!compact && <small>Tokyo</small>}
      </span>
      <em>{detail}</em>
    </div>
  );
}

export function RecoveryLaunchCard({
  stage,
  errorDetail = null,
  disabled = false,
  compact = false,
  resumeMode = false,
  onStart,
}: RecoveryLaunchCardProps) {
  const running = stage === 'running';
  const failed = Boolean(errorDetail);
  const [activeStep, setActiveStep] = useState(0);
  const launchSteps = [
    {
      icon: AlertTriangle,
      label: 'Understand disruption',
      detail: 'Classify the cancelled-flight recovery.',
    },
    {
      icon: Search,
      label: 'Search and rank',
      detail: 'Retrieve and rerank live Tokyo options.',
    },
    {
      icon: Database,
      label: 'Save an Aurora checkpoint',
      detail: 'Persist the shortlist before verification.',
    },
    {
      icon: CheckCircle2,
      label: 'Verify after resume',
      detail: 'Check the top three options after the pause.',
    },
  ];

  useEffect(() => {
    if (!running) {
      setActiveStep(0);
      return undefined;
    }

    if (resumeMode) {
      setActiveStep(3);
      return undefined;
    }

    setActiveStep(0);
    const timers = [
      window.setTimeout(() => setActiveStep(1), 300),
      window.setTimeout(() => setActiveStep(2), 1250),
    ];
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [resumeMode, running]);

  const connectionFailure =
    failed && /connection|checkpoint|postgres|aurora/i.test(errorDetail ?? '');
  const failedStep = connectionFailure ? 2 : Math.max(activeStep, 0);

  return (
    <article
      className={`mds-decision-card mds-recovery-launch-card is-${stage}${
        failed ? ' has-error' : ''
      }${compact ? ' is-compact' : ''}`}
      aria-label={compact ? 'Live recovery progress' : 'Start travel recovery'}
    >
      {!compact && (
        <section
          className="mds-mobile-disruption-card"
          aria-label="Cancelled ANA NH 109 mobile trip card"
        >
          <div className="mds-mobile-disruption-topline">
            <span>Meridian trips</span>
            <em>
              {failed ? (
                <AlertTriangle size={13} aria-hidden="true" />
              ) : running ? (
                <Loader2 size={13} aria-hidden="true" />
              ) : (
                <AlertTriangle size={13} aria-hidden="true" />
              )}
              {failed
                ? 'Recovery needs attention'
                : running
                  ? 'Recovery in progress'
                  : 'Action needed'}
            </em>
          </div>

          <div className="mds-mobile-disruption-hero">
            <div className="mds-mobile-disruption-message">
              <small>Trip update</small>
              <span aria-hidden="true">
                <AlertTriangle size={22} />
              </span>
              <div>
                <h2>Your flight has been cancelled.</h2>
                <p>
                  ANA NH 109 from New York to Tokyo is no longer operating.
                  Meridian can build a recovery plan now.
                </p>
              </div>
            </div>
            <figure className="mds-mobile-disruption-media">
              <img
                src="/travel/recovery-flight.jpg"
                alt="ANA aircraft on final approach"
                width="1920"
                height="1168"
                loading="eager"
                decoding="async"
              />
            </figure>

            <div className="mds-mobile-disruption-flight">
              <div className="mds-mobile-disruption-route">
                <span>
                  <small>From</small>
                  <strong>JFK</strong>
                  <em>New York</em>
                </span>
                <span className="mds-mobile-disruption-route-line">
                  <i />
                  <Circle size={8} fill="currentColor" aria-hidden="true" />
                  <i />
                  <b>ANA NH 109</b>
                </span>
                <span>
                  <small>To</small>
                  <strong>HND</strong>
                  <em>Tokyo</em>
                </span>
              </div>

              <div className="mds-mobile-disruption-status">
                <strong>Cancelled</strong>
                <span>
                  {failed
                    ? 'The workflow stopped safely before changing the trip.'
                    : running
                      ? 'Meridian is building a checkpointed recovery plan.'
                      : 'Live recovery options are ready to search.'}
                </span>
              </div>

              {failed && (
                <div className="mds-recovery-launch-error" role="alert">
                  <AlertTriangle size={17} aria-hidden="true" />
                  <span>
                    <strong>
                      {connectionFailure
                        ? 'Aurora checkpoint connection unavailable'
                        : 'Recovery workflow interrupted'}
                    </strong>
                    <small>{errorDetail}</small>
                  </span>
                </div>
              )}

              <button
                type="button"
                onClick={onStart}
                disabled={disabled || running}
              >
                {running ? (
                  <Loader2 size={18} aria-hidden="true" />
                ) : (
                  <Route size={18} aria-hidden="true" />
                )}
                {running
                  ? 'Building plan'
                  : failed
                    ? 'Retry recovery'
                    : 'Start recovery'}
              </button>
            </div>
          </div>
        </section>
      )}

      <div className="mds-recovery-timeline-heading">
        <span>{running ? 'Live workflow' : failed ? 'Workflow stopped' : 'Recovery workflow'}</span>
        <small>
          {running
            ? `Step ${activeStep + 1} of ${launchSteps.length}`
            : failed
              ? 'No trip change was made'
              : 'Runs after you confirm'}
        </small>
      </div>

      <ol
        className={`mds-recovery-launch-steps${
          running ? ' is-running' : failed ? ' is-failed' : ''
        }`}
        aria-label="Recovery workflow progress"
      >
        {launchSteps.map((step, index) => {
          const Icon = step.icon;
          const stepState = failed
            ? index < failedStep
              ? 'is-visited'
              : index === failedStep
                ? 'is-failed'
                : 'is-pending'
            : running
              ? index < activeStep
                ? 'is-visited'
                : index === activeStep
                  ? 'is-current'
                  : 'is-pending'
              : index === 0
                ? 'is-ready'
                : 'is-pending';
          return (
            <li
              key={step.label}
              className={stepState}
              aria-current={stepState === 'is-current' ? 'step' : undefined}
            >
              <span>
                {stepState === 'is-current' ? (
                  <Loader2 size={16} aria-hidden="true" />
                ) : stepState === 'is-visited' ? (
                  <Check size={15} strokeWidth={3} aria-hidden="true" />
                ) : stepState === 'is-failed' ? (
                  <AlertTriangle size={15} aria-hidden="true" />
                ) : (
                  <Icon size={16} aria-hidden="true" />
                )}
              </span>
              <div>
                <strong>{step.label}</strong>
                <small>{step.detail}</small>
              </div>
            </li>
          );
        })}
      </ol>

      {!compact && (
        <footer className="mds-recovery-launch-actions">
          <span>
            <LockKeyhole size={14} aria-hidden="true" />
            No booking or purchase without confirmation
          </span>
        </footer>
      )}
    </article>
  );
}

export function RecoveryGuardrailsCard() {
  return (
    <article
      className="mds-decision-card mds-recovery-guardrails-card"
      aria-label="Recovery guardrails"
    >
      <span>
        <ShieldCheck size={17} aria-hidden="true" />
      </span>
      <div>
        <strong>Built for a safe handoff</strong>
        <small>
          Live search, traveler context, and durable state become visible only
          after the workflow observes them.
        </small>
      </div>
    </article>
  );
}

export function RecommendedRecoveryPlanCard({
  product,
  stage,
  evidence,
  memoryFacts,
  travelerProfile,
  primaryAction,
  secondaryAction,
}: RecommendedRecoveryPlanCardProps) {
  const chips = preferenceChips(
    evidence,
    memoryFacts,
    travelerProfile,
  );
  const contextReasons = travelerContextReasons(
    evidence,
    memoryFacts,
    travelerProfile,
  );

  return (
    <article
      className={`mds-decision-card mds-recommended-plan-card is-${stage}`}
      aria-label="Recommended recovery plan"
    >
      <header className="mds-decision-card-head">
        <span className="mds-decision-card-kicker">
          <Sparkles size={17} aria-hidden="true" />
          Recommended recovery plan
        </span>
        <span className={`mds-decision-status is-${stage}`}>
          {stage === 'running' && <Loader2 size={13} aria-hidden="true" />}
          {stage === 'checkpointed' && <Database size={13} aria-hidden="true" />}
          {stage === 'ready' && <Check size={13} aria-hidden="true" />}
          {stageBadge(stage)}
        </span>
      </header>

      {product ? (
        <>
          <div className="mds-recommended-plan-media">
            <TripVisual product={product} />
            <span aria-hidden="true" />
            <em>Recovery pick</em>
          </div>
          <div className="mds-recommended-plan-title">
            <div>
              <small>
                {product.brand || 'Meridian partner'}
                {product.destination ? ` · ${product.destination}` : ''}
              </small>
              <strong>{product.name}</strong>
              {product.description && <p>{product.description}</p>}
            </div>
            <div className="mds-recommended-plan-price">
              <small>From</small>
              <b>{money(product.price)}</b>
              <span>per traveler</span>
            </div>
          </div>

          <RouteTimeline detail={durationLabel(product)} />

          <div className="mds-recommended-plan-signals">
            <span className={evidence.availabilityObserved ? 'is-verified' : ''}>
              <Clock3 size={14} aria-hidden="true" />
              {availabilityLabel(product, evidence.availabilityObserved)}
            </span>
            <span className={evidence.checkpointObserved ? 'is-verified' : ''}>
              <Database size={14} aria-hidden="true" />
              {evidence.checkpointObserved
                ? 'Plan state saved'
                : 'Checkpoint created during recovery'}
            </span>
            <span>
              <FileCheck2 size={14} aria-hidden="true" />
              Policy review remains a traveler decision
            </span>
          </div>
        </>
      ) : (
        <div className="mds-recommended-plan-empty">
          <Route size={24} aria-hidden="true" />
          <span>
            <strong>
              {stage === 'running'
                ? 'Ranking live Tokyo alternatives'
                : 'Your recommended plan will appear here'}
            </strong>
            <small>
              Meridian will compare the live catalog before presenting a
              traveler decision.
            </small>
          </span>
        </div>
      )}

      <div className="mds-decision-preference-row">
        <small>
          {evidence.memoryObserved
            ? 'Traveler context applied'
            : 'Traveler context pending'}
        </small>
        <div>
          {chips.map((chip) => (
            <span
              key={chip}
              className={
                chip === 'Preference match pending' ? 'is-pending' : ''
              }
            >
              {chip === 'Airline Premier' ? (
                <ShieldCheck size={13} aria-hidden="true" />
              ) : (
                <CheckCircle2 size={13} aria-hidden="true" />
              )}
              {chip}
            </span>
          ))}
        </div>
      </div>

      {contextReasons.length > 0 && (
        <section
          className="mds-recovery-context-reasons"
          aria-label="Why this plan fits Alex"
        >
          <header>
            <span>
              <Sparkles size={15} aria-hidden="true" />
              Why this fits Alex
            </span>
            <em>Aurora traveler context</em>
          </header>
          <ul>
            {contextReasons.map((reason) => (
              <li key={reason.label}>
                <CheckCircle2 size={15} aria-hidden="true" />
                <span>
                  <strong>{reason.label}</strong>
                  <small>{reason.detail}</small>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer className="mds-decision-card-actions">
        <button
          type="button"
          className="is-primary"
          onClick={primaryAction.onClick}
          disabled={primaryAction.disabled}
        >
          {stage === 'checkpointed' ? (
            <Database size={16} aria-hidden="true" />
          ) : stage === 'ready' ? (
            <CheckCircle2 size={16} aria-hidden="true" />
          ) : (
            <Plane size={16} aria-hidden="true" />
          )}
          {primaryAction.label}
        </button>
        <button
          type="button"
          className="is-secondary"
          onClick={secondaryAction.onClick}
          disabled={secondaryAction.disabled}
        >
          {secondaryAction.label}
          <ArrowRight size={15} aria-hidden="true" />
        </button>
      </footer>
    </article>
  );
}

export function FlightOptionCard({
  product,
  rank,
  badge,
  availabilityObserved,
  disabled = false,
  onView,
  onCompare,
}: FlightOptionCardProps) {
  return (
    <article
      className="mds-decision-card mds-flight-option-card"
      aria-label={`Recovery option ${rank}`}
    >
      <header>
        <span>{badge}</span>
        <small>Option {rank}</small>
      </header>
      {product ? (
        <>
          <div className="mds-flight-option-card-media">
            <TripVisual product={product} compact />
            <span aria-hidden="true" />
          </div>
          <div className="mds-flight-option-card-title">
            <strong>{product.name}</strong>
            <span>{product.brand || 'Meridian partner'}</span>
          </div>
          <RouteTimeline detail={durationLabel(product)} compact />
          <dl>
            <div>
              <dt>Price</dt>
              <dd>{money(product.price)}</dd>
            </div>
            <div>
              <dt>Availability</dt>
              <dd>{availabilityLabel(product, availabilityObserved)}</dd>
            </div>
          </dl>
        </>
      ) : (
        <div className="mds-flight-option-card-empty">
          <Plane size={21} aria-hidden="true" />
          <strong>Alternative pending</strong>
          <span>Appears after the live recovery search.</span>
        </div>
      )}
      <footer>
        <button
          type="button"
          className="is-primary"
          onClick={onView}
          disabled={disabled || !product}
        >
          View option
        </button>
        <button
          type="button"
          className="is-icon"
          onClick={onCompare}
          disabled={disabled || !product}
          aria-label={`Compare option ${rank}`}
          title="Compare option"
        >
          <GitCompareArrows size={16} aria-hidden="true" />
        </button>
      </footer>
    </article>
  );
}

export function ConciergeAssistanceCard({
  stage,
  evidence,
  product,
  disabled = false,
  onHotel,
  onProtection,
}: ConciergeAssistanceCardProps) {
  const ready = stage === 'ready';
  const contextLabel = evidence.memoryObserved
    ? 'Traveler context matched'
    : 'Traveler context available after recall';
  return (
    <article
      className="mds-decision-card mds-concierge-assistance-card"
      aria-label="Concierge assistance"
    >
      <header className="mds-decision-card-head">
        <span className="mds-decision-card-kicker">
          <Headphones size={17} aria-hidden="true" />
          Concierge assistance
        </span>
        <Sparkles size={16} aria-hidden="true" />
      </header>
      <div className="mds-concierge-hotel-media">
        <img
          src="/travel/haneda-hotel.jpg"
          alt="Airport hotel room overlooking Haneda runways"
        />
        <span className="mds-concierge-hotel-media-shade" />
        <em>{ready ? 'Search ready' : 'Queued'}</em>
        <div>
          <small>Haneda · hotel assistance</small>
          <strong>Airport-area stay shortlist</strong>
          <span>
            {product
              ? `Aligned to ${product.name}`
              : 'Ready after a replacement plan is selected'}
          </span>
        </div>
      </div>
      <div className="mds-concierge-context-chips">
        {evidence.memoryObserved && <span className="is-violet">Memory match</span>}
        {evidence.loyaltyObserved && <span>Hotel Platinum</span>}
        <span className={ready ? 'is-green' : ''}>
          {ready ? 'Lounge access' : 'Hotel options'}
        </span>
        <span className={ready ? 'is-yellow' : ''}>
          {ready ? 'Airport transfer' : 'Transfer support'}
        </span>
      </div>
      <p>{contextLabel}. Nothing is booked automatically.</p>
      <footer className="mds-concierge-assistance-actions">
        <button
          type="button"
          className="is-primary"
          onClick={onHotel}
          disabled={disabled || !ready}
        >
          Find hotel options
        </button>
        <button
          type="button"
          onClick={onProtection}
          disabled={disabled || !ready}
        >
          Review protection
        </button>
      </footer>
    </article>
  );
}

function ProofRow({
  label,
  detail,
  status,
}: {
  label: string;
  detail: string;
  status: 'done' | 'running' | 'pending';
}) {
  return (
    <li className={`is-${status}`}>
      <span aria-hidden="true">
        {status === 'done' ? (
          <Check size={12} strokeWidth={3} />
        ) : status === 'running' ? (
          <Loader2 size={13} />
        ) : (
          <Circle size={10} />
        )}
      </span>
      <div>
        <strong>{label}</strong>
        <small>{detail}</small>
      </div>
    </li>
  );
}

export function AgentProofCard({
  stage,
  evidence,
  recommendationCount,
  traceCount,
  onViewProof,
}: AgentProofCardProps) {
  const running = stage === 'running';
  const statusFor = (observed: boolean) =>
    observed ? 'done' : running ? 'running' : 'pending';

  return (
    <article
      className="mds-decision-card mds-agent-proof-card"
      aria-label="Agent proof"
    >
      <header className="mds-decision-card-head">
        <span className="mds-decision-card-kicker">
          <ShieldCheck size={17} aria-hidden="true" />
          Agent proof
        </span>
        <small>{traceCount ? `${traceCount} observed spans` : 'Awaiting run'}</small>
      </header>
      <ul>
        <ProofRow
          label="Live alternatives"
          detail={
            evidence.alternativesObserved
              ? `${recommendationCount} ranked options returned`
              : 'No live result observed yet'
          }
          status={statusFor(evidence.alternativesObserved)}
        />
        <ProofRow
          label="Traveler context"
          detail={
            evidence.memoryObserved
              ? 'Preference context recalled'
              : 'Recall not observed in this run'
          }
          status={statusFor(evidence.memoryObserved)}
        />
        <ProofRow
          label="Duration inventory"
          detail={
            evidence.availabilityObserved
              ? 'Top-option inventory verified'
              : 'Inventory check pending'
          }
          status={statusFor(evidence.availabilityObserved)}
        />
        <ProofRow
          label="Durable workflow"
          detail={
            evidence.checkpointObserved
              ? evidence.durableCheckpoint
                ? 'Checkpoint persisted in Aurora'
                : 'Checkpoint observed on current worker'
              : 'Checkpoint not observed yet'
          }
          status={statusFor(evidence.checkpointObserved)}
        />
      </ul>
      <button type="button" onClick={onViewProof}>
        View system proof
        <ArrowRight size={15} aria-hidden="true" />
      </button>
    </article>
  );
}

export function CheckpointedPlanCard({
  stage,
  evidence,
  threadId,
  resumedAfterRestart,
}: CheckpointedPlanCardProps) {
  const searchDone = evidence.searchObserved;
  const rankDone = evidence.alternativesObserved;
  const checkpointDone = evidence.checkpointObserved;
  const inventoryDone = evidence.availabilityObserved;
  const status =
    stage === 'ready'
      ? 'Plan ready'
      : stage === 'checkpointed'
        ? evidence.durableCheckpoint
          ? 'Checkpointed in Aurora'
          : 'Checkpointed'
        : stage === 'running'
          ? 'Workflow running'
          : 'Not started';

  return (
    <article
      className={`mds-decision-card mds-checkpointed-plan-card is-${stage}`}
      aria-label="Checkpointed plan progress"
    >
      <header className="mds-decision-card-head">
        <span className="mds-decision-card-kicker">
          <Database size={17} aria-hidden="true" />
          Checkpointed plan
        </span>
        <span className={`mds-checkpoint-badge is-${stage}`}>{status}</span>
      </header>
      <div className="mds-checkpoint-thread">
        <small>Thread</small>
        <strong>{threadId}</strong>
      </div>
      <ol className="mds-checkpoint-progress">
        <li className="is-done"><i />Disruption</li>
        <li className={searchDone ? 'is-done' : stage === 'running' ? 'is-current' : ''}>
          <i />Search
        </li>
        <li className={rankDone ? 'is-done' : ''}><i />Rank</li>
        <li className={checkpointDone ? 'is-done' : ''}><i />Save</li>
        <li className={inventoryDone ? 'is-done' : stage === 'checkpointed' ? 'is-current' : ''}>
          <i />Verify
        </li>
      </ol>
      <p>
        {stage === 'ready'
          ? resumedAfterRestart
            ? 'Resumed from Aurora after a worker restart.'
            : 'Resumed from the saved Aurora workflow state.'
          : stage === 'checkpointed'
            ? 'The ranked shortlist is durable and safe to resume.'
            : stage === 'running'
              ? 'Meridian is saving progress between workflow steps.'
              : 'Recovery state will be persisted before inventory verification.'}
      </p>
    </article>
  );
}
