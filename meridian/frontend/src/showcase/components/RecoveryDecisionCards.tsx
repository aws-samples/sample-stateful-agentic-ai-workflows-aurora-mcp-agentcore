import { motion, PresenceContext } from 'motion/react';
import { AuroraIcon } from './ServiceMark';
import { CHECKPOINT_GLOW, STATE_CHANGE, useConfirmedLive } from '../hooks/useLiveCues';
import {
AlertTriangle,
  ArrowRight,
  CalendarDays,
  Check,
  CheckCircle2,
  Circle,
  Clock3,
  FileCheck2,
  GitCompareArrows,
  Headphones,
  Loader2,
  LockKeyhole,
  Plane,
  Route,
  Search,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import type { LongTermMemoryFact, Product, TravelerProfile } from '../../types';
import type {
  RecoveryEvidence,
  RecoveryStage,
  RecoveryStepId,
  RecoveryStepView,
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

interface PackageOptionCardProps {
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

interface RecoveryLaunchCardProps {
  stage: RecoveryStage;
  /** Each step's state and source, as the backend has confirmed it. */
  steps: RecoveryStepView[];
  /** Animate step changes: only after watching this recovery's run. */
  live?: boolean;
  errorDetail?: string | null;
  disabled?: boolean;
  compact?: boolean;
  resumeMode?: boolean;
  onStart: () => void;
}

/** The four workflow steps, each named by the id its backend view carries. */
const LAUNCH_STEPS: {
  id: RecoveryStepId;
  icon: typeof Search | typeof AuroraIcon;
  label: string;
  detail: string;
}[] = [
  {
    id: 'understand',
    icon: AlertTriangle,
    label: 'Understand disruption',
    detail: 'Classify the canceled-flight recovery.',
  },
  {
    id: 'search',
    icon: Search,
    label: 'Search and rank',
    detail: 'Retrieve and rerank live Tokyo options.',
  },
  {
    id: 'checkpoint',
    icon: AuroraIcon,
    label: 'Save an Aurora checkpoint',
    detail: 'Persist the shortlist before verification.',
  },
  {
    id: 'verify',
    icon: CheckCircle2,
    label: 'Verify after resume',
    detail: 'Check the top three options after the pause.',
  },
];

function stepView(steps: RecoveryStepView[], id: RecoveryStepId): RecoveryStepView {
  return steps.find(step => step.id === id) ?? { id, state: 'is-pending', source: null };
}

function timelineNote(
  stage: RecoveryStage, failed: boolean, resumeMode: boolean, total: number,
): string {
  if (failed) return 'Check saved progress before retrying';
  if (stage === 'running') return resumeMode ? `Step 4 of ${total}` : 'Waiting for saved results';
  if (stage === 'checkpointed') return 'Paused at a saved checkpoint';
  if (stage === 'ready') return 'Workflow complete';
  return 'Runs after you confirm';
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

    chips.push('Context recalled');
  }
  if (evidence.loyaltyObserved) chips.push('Loyalty context');
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
  const dietaryNotes = profile?.dietary_notes ?? byKey.get('dietary_notes');
  const shellfishNote = byKey.get('shellfish_allergy');
  if (dietaryNotes || shellfishNote) {
    reasons.push({
      label: 'Saved dietary note',
      detail: dietaryNotes || `Shellfish allergy: ${shellfishNote}`,
    });
  }
  const lodging =
    byKey.get('lodging_preference') ??
    byKey.get('lodging_style') ??
    byKey.get('travel_style');
  if (lodging) {
    reasons.push({
      label: 'Stay preference',
      detail: cleanPreference(lodging) ?? lodging,
    });
  }
  const seat = cleanPreference(profile?.seat_preference);
  if (seat) {
    reasons.push({
      label: 'Long-haul comfort',
      detail: `${seat} saved in the traveler profile`,
    });
  }
  if (evidence.loyaltyObserved) {
    reasons.push({
      label: 'Loyalty context',
      detail: 'Loyalty context was read. Partner benefits still need confirmation.',
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

function PackageSummary({
  product,
  detail,
  compact = false,
}: {
  product: Product;
  detail: string;
  compact?: boolean;
}) {
  const destination = product.destination ?? product.region ?? 'Trip package';
  return (
    <div className={`mds-decision-route${compact ? ' is-compact' : ''}`}>
      <span>
        <b>{destination}</b>
        {!compact && <small>Destination</small>}
      </span>
      <span className="mds-decision-route-line" aria-hidden="true">
        <i />
        <CalendarDays size={compact ? 13 : 16} />
        <i />
      </span>
      <span>
        <b>{detail}</b>
        {!compact && <small>Trip length</small>}
      </span>
      <em>Package inventory only. Flights not checked.</em>
    </div>
  );
}

/** The canceled flight: route, status, and why the workflow stopped if it did. */
function DisruptionFlight({ running, failed, errorDetail }: {
  running: boolean;
  failed: boolean;
  errorDetail: string | null;
}) {
  return (
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
          <b>Traveler report</b>
        </span>
        <span>
          <small>To</small>
          <strong>TYO</strong>
          <em>Tokyo</em>
        </span>
      </div>

      <div className="mds-mobile-disruption-status">
        <strong>Canceled</strong>
        <span>
          {failed
            ? 'The workflow was interrupted. Check System evidence for saved progress.'
            : running
              ? 'Meridian is building a checkpointed recovery plan.'
              : 'Live trip-package options are ready to search.'}
        </span>
      </div>

      {failed && (
        <div className="mds-recovery-launch-error" role="alert">
          <AlertTriangle size={17} aria-hidden="true" />
          <span>
            <strong>
              Recovery interrupted
            </strong>
            <small>{errorDetail}</small>
          </span>
        </div>
      )}
    </div>
  );
}

/** The traveler's view of the disruption, with the one action that starts recovery. */
function DisruptionHero({ running, failed, errorDetail, disabled, onStart }: {
  running: boolean;
  failed: boolean;
  errorDetail: string | null;
  disabled: boolean;
  onStart: () => void;
}) {
  return (
    <section
      className="mds-mobile-disruption-card"
      aria-label="Traveler-reported canceled flight"
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
            <h2>Let’s get your trip moving again.</h2>
            <p>
              Search live alternatives, save the shortlist in Aurora, and
              resume to verify availability. You decide what happens next.
            </p>
          </div>
          <button
            type="button"
            className="mds-mobile-disruption-primary"
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
        <figure className="mds-mobile-disruption-media">
          <img
            src="/travel/recovery-flight.jpg"
            alt="Aircraft on final approach"
            width="1920"
            height="1168"
            loading="eager"
            decoding="async"
          />
        </figure>

        <DisruptionFlight running={running} failed={failed} errorDetail={errorDetail} />
      </div>
    </section>
  );
}

/** The four workflow steps, each moving only when a watched response confirms it. */
function RecoveryStepList({ steps, live, running, failed }: {
  steps: RecoveryStepView[];
  live: boolean;
  running: boolean;
  failed: boolean;
}) {
  // The one emphasis, only when a watched run's response confirms the save.
  const checkpointGlow = useConfirmedLive(
    stepView(steps, 'checkpoint').state === 'is-visited', live,
  );
  // A step settles into place only when a response confirms it. While a
  // request is in flight nothing has been confirmed yet, so nothing moves.
  const settles = live && !running;

  // The app's view swap starts with AnimatePresence initial={false}, which
  // would freeze every cue that mounts later inside the first view it
  // paints. The steps decide for themselves when to move.
  return (
    <PresenceContext.Provider value={null}>
      <ol
        className={`mds-recovery-launch-steps${
          running ? ' is-running' : failed ? ' is-failed' : ''
        }`}
        aria-label="Recovery workflow progress"
      >
        {LAUNCH_STEPS.map((step) => {
          const Icon = step.icon;
          const { state: stepState, source } = stepView(steps, step.id);
          // The checkpoint step keeps its Aurora mark once Aurora confirms it.
          const aurora = step.id === 'checkpoint';
          const done = stepState === 'is-visited' && !aurora;
          return (
            <li
              key={step.label}
              className={stepState}
              aria-current={stepState === 'is-current' ? 'step' : undefined}
            >
              <span>
                <motion.span
                  key={stepState}
                  className="mds-recovery-step-icon"
                  initial={settles && stepState === 'is-visited'
                    ? { opacity: 0, scale: 0.6 } : false}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={STATE_CHANGE}
                >
                  {done
                    ? <Check size={15} strokeWidth={3} aria-hidden="true" />
                    : <Icon size={16} aria-hidden="true" />}
                </motion.span>
                {checkpointGlow && aurora && (
                  <motion.span
                    className="mds-aurora-glow"
                    aria-hidden="true"
                    initial={{ opacity: 0.9, scale: 1 }}
                    animate={{ opacity: 0, scale: 1.9 }}
                    transition={CHECKPOINT_GLOW}
                  />
                )}
              </span>
              <div>
                <strong>{step.label}</strong>
                <small>{step.detail}</small>
                {source && (
                  <motion.em
                    key={source}
                    className="mds-step-source"
                    initial={settles ? { opacity: 0 } : false}
                    animate={{ opacity: 1 }}
                    transition={STATE_CHANGE}
                  >
                    {source}
                  </motion.em>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </PresenceContext.Provider>
  );
}

export function RecoveryLaunchCard({
  stage,
  steps,
  live = false,
  errorDetail = null,
  disabled = false,
  compact = false,
  resumeMode = false,
  onStart,
}: RecoveryLaunchCardProps) {
  const running = stage === 'running';
  const failed = Boolean(errorDetail);

  return (
    <article
      className={`mds-decision-card mds-recovery-launch-card is-${stage}${
        failed ? ' has-error' : ''
      }${compact ? ' is-compact' : ''}`}
      aria-label={compact ? 'Live recovery progress' : 'Start travel recovery'}
    >
      {!compact && (
        <DisruptionHero
          running={running}
          failed={failed}
          errorDetail={errorDetail}
          disabled={disabled}
          onStart={onStart}
        />
      )}

      <div className="mds-recovery-timeline-heading">
        <span>{running ? 'Live workflow' : failed ? 'Workflow stopped' : 'Recovery workflow'}</span>
        <small>{timelineNote(stage, failed, resumeMode, LAUNCH_STEPS.length)}</small>
      </div>

      <RecoveryStepList steps={steps} live={live} running={running} failed={failed} />

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
          Recommended trip plan
        </span>
        <span className={`mds-decision-status is-${stage}`}>
          {stage === 'running' && <Loader2 size={13} aria-hidden="true" />}
          {stage === 'checkpointed' && <AuroraIcon size={13} aria-hidden="true" />}
          {stage === 'ready' && <Check size={13} aria-hidden="true" />}
          {stageBadge(stage)}
        </span>
      </header>

      {product ? (
        <>
          <div className="mds-recommended-plan-media">
            <TripVisual product={product} />
            <span aria-hidden="true" />
            <em>Top catalog match</em>
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

          <PackageSummary
            product={product}
            detail={durationLabel(product)}
          />

          <div className="mds-recommended-plan-signals">
            <span className={evidence.availabilityObserved ? 'is-verified' : ''}>
              <Clock3 size={14} aria-hidden="true" />
              {availabilityLabel(product, evidence.availabilityObserved)}
            </span>
            <span className={evidence.checkpointObserved ? 'is-verified' : ''}>
              <AuroraIcon size={14} aria-hidden="true" />
              {evidence.checkpointObserved
                ? 'Plan state saved'
                : 'Checkpoint created during recovery'}
            </span>
            <span>
              <FileCheck2 size={14} aria-hidden="true" />
              Flight and policy review remain traveler decisions
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
            ? 'Traveler context recalled'
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
          aria-label="Saved preferences to review"
        >
          <header>
            <span>
              <Sparkles size={15} aria-hidden="true" />
              Saved preferences to review
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
            <AuroraIcon size={16} aria-hidden="true" />
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

export function PackageOptionCard({
  product,
  rank,
  badge,
  availabilityObserved,
  disabled = false,
  onView,
  onCompare,
}: PackageOptionCardProps) {
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
          <PackageSummary
            product={product}
            detail={durationLabel(product)}
            compact
          />
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
    ? 'Traveler context recalled'
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
      <div className="mds-concierge-hotel-media" data-theme="dark">
        <img
          src="/travel/haneda-hotel.jpg"
          alt="Airport hotel room overlooking Haneda runways"
          width="1600"
          height="900"
          loading="lazy"
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
        {evidence.memoryObserved && <span>Context recalled</span>}
        {evidence.loyaltyObserved && <span>Loyalty context</span>}
        <span>{ready ? 'Check lounge options' : 'Hotel options'}</span>
        <span>{ready ? 'Check transfer options' : 'Transfer support'}</span>
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
        View system evidence
        <ArrowRight size={15} aria-hidden="true" />
      </button>
    </article>
  );
}
