import {
  AlertTriangle,
  CheckCircle2,
  Database,
  Loader2,
  Sparkles,
} from 'lucide-react';
import { useRef } from 'react';
import { ChatComposer } from './ChatComposer';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { SHOWCASE_FINALE_PROMPT } from '../lib/showcaseAdapters';
import {
  deriveRecoveryEvidence,
  deriveRecoveryStage,
} from '../lib/recoveryState';
import { RecoveryBriefing } from './RecoveryBriefing';
import {
  AgentProofCard,
  CheckpointedPlanCard,
  ConciergeAssistanceCard,
  FlightOptionCard,
  RecommendedRecoveryPlanCard,
} from './RecoveryDecisionCards';
import { RecoveryRouteMap } from './RecoveryRouteMap';

const STAY_PROMPT =
  'Find a well-rated hotel near Haneda for tonight with lounge access and an easy airport transfer.';
const PROTECTION_PROMPT =
  'Review my trip protection and change-fee options before rebooking the cancelled Tokyo flight.';

export function RecoveryWorkspace({
  state,
  greetingPart,
  onOpenProof = () => {},
}: {
  state: MeridianShowcaseState;
  greetingPart: string;
  onOpenProof?: () => void;
}) {
  const recoveryStage = deriveRecoveryStage(state);
  const recoveryEvidence = deriveRecoveryEvidence(state);
  const topRecoveryOption = state.recommendations?.[0] ?? null;
  const briefingRef = useRef<HTMLElement>(null);
  const startRecovery = () => {
    state.setSelectedPhase(5);
    void state.applyPhaseExample(SHOWCASE_FINALE_PROMPT, true, 5);
  };
  const resumeRecovery = () => {
    state.setSelectedPhase(5);
    void state.submitPrompt('Resume workflow from checkpoint', 5);
  };
  const reviewRecoveryPlan = () => {
    const briefing = briefingRef.current;
    if (!briefing) return;
    const details = briefing.querySelector('details');
    if (details) details.open = true;
    briefing.scrollIntoView({ behavior: 'smooth', block: 'start' });
    briefing.querySelector<HTMLElement>('summary')?.focus();
  };
  const runPrimaryAction = () => {
    if (recoveryStage === 'checkpointed') {
      resumeRecovery();
    } else if (recoveryStage === 'ready') {
      if (topRecoveryOption) state.openTripDetails(topRecoveryOption);
    } else {
      startRecovery();
    }
  };
  const runWorkflowPrompt = (prompt: string) => {
    state.setSelectedPhase(5);
    void state.applyPhaseExample(prompt, true, 5);
  };
  const hasConversation =
    state.messages.length > 0 || state.isLoading || Boolean(state.error);
  const durableCheckpoint =
    recoveryEvidence.checkpointObserved &&
    recoveryEvidence.durableCheckpoint;
  const threadId = state.conversationId ?? 'phase5-pending';
  const recoveryStatusLabel =
    recoveryStage === 'ready'
      ? 'Plan ready'
      : recoveryStage === 'checkpointed'
        ? durableCheckpoint
          ? 'Checkpoint saved in Aurora'
          : 'Checkpoint saved'
        : recoveryStage === 'running'
          ? 'Recovery running'
          : 'Action needed';
  const primaryActionLabel =
    recoveryStage === 'ready'
      ? 'Review this plan'
      : recoveryStage === 'checkpointed'
        ? 'Resume and verify'
        : recoveryStage === 'running'
          ? 'Recovery running'
          : 'Recover my trip';
  const alternativeProducts = Array.from({ length: 3 }, (_, index) =>
    state.recommendations?.[index + 1] ?? null,
  );
  const pricedRecommendations = (state.recommendations ?? []).filter(
    (product) => Number.isFinite(product.price),
  );
  const lowestPrice = pricedRecommendations.length
    ? Math.min(...pricedRecommendations.map((product) => product.price))
    : null;
  const optionBadge = (
    product: (typeof alternativeProducts)[number],
    index: number,
  ) => {
    if (!product) return `Option ${index + 2}`;
    if (lowestPrice !== null && product.price === lowestPrice) {
      return 'Lowest package price';
    }
    if ((product.available_sizes?.length ?? 0) > 1) {
      return 'Flexible duration';
    }
    return `Ranked option ${index + 2}`;
  };

  return (
    <div className="mds-recovery-workspace">
      <header className="mds-recovery-greeting">
        <h1>{`Good ${greetingPart}, Alex.`}</h1>
        <p>Let&apos;s get your Tokyo trip back on track.</p>
      </header>

      <section
        className={`mds-recovery-command is-${recoveryStage}`}
        aria-label="Active flight disruption"
      >
        <div className="mds-recovery-command-main is-compact">
          <span className="mds-recovery-warning" aria-hidden="true">
            {recoveryStage === 'ready' ? (
              <CheckCircle2 size={25} strokeWidth={2.1} />
            ) : recoveryStage === 'checkpointed' ? (
              <Database size={24} strokeWidth={2} />
            ) : recoveryStage === 'running' ? (
              <Loader2 size={24} strokeWidth={2} />
            ) : (
              <AlertTriangle size={25} strokeWidth={2.1} />
            )}
          </span>
          <div className="mds-recovery-command-copy">
            <b className="mds-recovery-status">{recoveryStatusLabel}</b>
            <h2>JFK to Tokyo flight cancelled</h2>
            <div className="mds-recovery-flight-meta">
              <strong>ANA</strong>
              <span>NH 109</span>
              <span>Today · 10:40 AM</span>
              <span>JFK → HND</span>
              <em>Airline Premier</em>
            </div>
            <p>
              {recoveryStage === 'ready'
                ? 'Your live alternatives and duration inventory are ready to review.'
                : recoveryStage === 'checkpointed'
                  ? 'The ranked shortlist is durable. Resume when you are ready to verify live duration inventory.'
                  : recoveryStage === 'running'
                    ? 'Meridian is ranking alternatives and saving workflow progress.'
                    : 'Meridian can rebuild the itinerary, checkpoint the shortlist, and resume before rebooking.'}
            </p>
            {(recoveryStage === 'checkpointed' ||
              (recoveryStage === 'ready' &&
                state.workflowStatus === 'resumed')) && (
              <div
                className={`mds-recovery-receipt is-${recoveryStage}`}
                role="status"
              >
                <Database size={14} aria-hidden="true" />
                <span>
                  {recoveryStage === 'checkpointed'
                    ? durableCheckpoint
                      ? `Checkpoint saved · thread ${threadId} · safe to restart`
                      : `Checkpoint saved · thread ${threadId} · current worker`
                    : state.workflowResumedAfterRestart
                      ? `Resumed from Aurora after worker restart · thread ${threadId}`
                      : `Resumed from Aurora checkpoint · thread ${threadId}`}
                </span>
              </div>
            )}
          </div>
          <RecoveryRouteMap />
        </div>
      </section>

      <section
        className="mds-recovery-decision-system"
        aria-label="Recovery decisions"
      >
        <div className="mds-recovery-decision-primary">
          <RecommendedRecoveryPlanCard
            product={topRecoveryOption}
            stage={recoveryStage}
            evidence={recoveryEvidence}
            memoryFacts={state.memoryFacts}
            travelerProfile={state.travelerProfile}
            primaryAction={{
              label: primaryActionLabel,
              onClick: runPrimaryAction,
              disabled: state.isLoading,
            }}
            secondaryAction={{
              label: topRecoveryOption ? 'Why this option?' : 'How recovery works',
              onClick: reviewRecoveryPlan,
              disabled: !hasConversation,
            }}
          />

          <div className="mds-recovery-alternatives">
            <div className="mds-recovery-alternatives-head">
              <span>
                <b>Alternative options</b>
                <small>Ranked from the same live recovery search</small>
              </span>
              <em>
                {state.recommendations?.length
                  ? `${state.recommendations.length} total`
                  : 'Awaiting search'}
              </em>
            </div>
            <div className="mds-recovery-alternative-grid">
              {alternativeProducts.map((product, index) => (
                <FlightOptionCard
                  key={product?.product_id ?? `pending-${index}`}
                  product={product}
                  rank={index + 2}
                  badge={optionBadge(product, index)}
                  availabilityObserved={
                    recoveryEvidence.availabilityObserved && index < 2
                  }
                  disabled={state.isLoading}
                  onView={() => {
                    if (product) state.openTripDetails(product);
                  }}
                  onCompare={() => {
                    if (product) state.compareTrip(product);
                  }}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="mds-recovery-decision-support">
          <ConciergeAssistanceCard
            stage={recoveryStage}
            evidence={recoveryEvidence}
            product={topRecoveryOption}
            disabled={state.isLoading}
            onHotel={() => runWorkflowPrompt(STAY_PROMPT)}
            onProtection={() => runWorkflowPrompt(PROTECTION_PROMPT)}
          />
          <AgentProofCard
            stage={recoveryStage}
            evidence={recoveryEvidence}
            recommendationCount={state.recommendations?.length ?? 0}
            traceCount={state.traceSpans?.length ?? 0}
            onViewProof={onOpenProof}
          />
          <CheckpointedPlanCard
            stage={recoveryStage}
            evidence={recoveryEvidence}
            threadId={threadId}
            resumedAfterRestart={state.workflowResumedAfterRestart}
            disabled={state.isLoading}
            onResume={resumeRecovery}
          />
        </div>
      </section>

      {hasConversation && (
        <section
          ref={briefingRef}
          className="mds-recovery-briefing"
          aria-label="Recovery briefing"
        >
          <div className="mds-recovery-briefing-head">
            <span><Sparkles size={16} />Recovery briefing</span>
            <button
              type="button"
              onClick={state.clearChat}
              disabled={state.isLoading}
            >
              Clear
            </button>
          </div>
          {state.error && (
            <div className="mds-error-banner" role="alert">
              <span className="mds-error-banner-copy">
                Meridian could not reach the live concierge.
              </span>
              <span className="mds-error-banner-actions">
                {state.lastPrompt && (
                  <button
                    type="button"
                    className="mds-error-retry"
                    onClick={() => void state.replayLastPrompt()}
                    disabled={state.isLoading}
                  >
                    Retry
                  </button>
                )}
                <button
                  type="button"
                  className="mds-error-dismiss"
                  onClick={state.clearError}
                >
                  Dismiss
                </button>
              </span>
            </div>
          )}
          <RecoveryBriefing state={state} />
        </section>
      )}

      <ChatComposer state={state} recoveryMode />
    </div>
  );
}
