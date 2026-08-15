import {
  AlertTriangle,
  Database,
  Sparkles,
} from 'lucide-react';
import { useEffect, useRef } from 'react';
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
  RecoveryLaunchCard,
  RecommendedRecoveryPlanCard,
} from './RecoveryDecisionCards';

const STAY_PROMPT =
  'Find a well-rated hotel near Haneda for tonight with lounge access and an easy airport transfer.';
const PROTECTION_PROMPT =
  'Review my trip protection and change-fee options before rebooking the cancelled Tokyo flight.';

export function RecoveryWorkspace({
  state,
  onOpenProof = () => {},
  showComposer = true,
}: {
  state: MeridianShowcaseState;
  onOpenProof?: () => void;
  showComposer?: boolean;
}) {
  const recoveryStage = deriveRecoveryStage(state);
  const recoveryEvidence = deriveRecoveryEvidence(state);
  const topRecoveryOption = state.recommendations?.[0] ?? null;
  const workflowErrorSpan = state.traceSpans.find(
    (span) =>
      span.status === 'error' ||
      span.category === 'error' ||
      /workflow error|langgraph error/i.test(
        `${span.name} ${span.details ?? ''}`,
      ),
  );
  const workflowErrorDetail = workflowErrorSpan?.details ?? null;
  const briefingRef = useRef<HTMLElement>(null);
  const consoleRef = useRef<HTMLDivElement>(null);
  const previousLoadingRef = useRef(false);
  const isResumingFromCheckpoint =
    recoveryStage === 'running' &&
    state.workflowStatus === 'paused' &&
    /resume|checkpoint/i.test(state.lastPrompt ?? '');

  useEffect(() => {
    const wasLoading = previousLoadingRef.current;
    previousLoadingRef.current = state.isLoading;
    if (!state.isLoading || wasLoading) return;

    const frame = window.requestAnimationFrame(() => {
      if (typeof consoleRef.current?.scrollIntoView === 'function') {
        consoleRef.current.scrollIntoView({
          behavior: 'smooth',
          block: 'start',
        });
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [state.isLoading]);

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
      ? 'Recovery plan ready'
      : recoveryStage === 'checkpointed'
        ? durableCheckpoint
          ? 'Shortlist checkpointed in Aurora'
          : 'Shortlist checkpointed'
        : recoveryStage === 'running'
          ? 'Recovery in progress'
          : 'Recovery ready to start';
  const primaryActionLabel =
    recoveryStage === 'ready'
      ? 'Review this plan'
      : recoveryStage === 'checkpointed'
        ? 'Resume and verify'
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
  const showDecisionDashboard =
    recoveryStage === 'checkpointed' || recoveryStage === 'ready';

  return (
    <div
      className={`mds-recovery-workspace${
        recoveryStage === 'running' ? ' is-running' : ''
      }${hasConversation ? ' has-conversation' : ''}`}
    >
      <header className={`mds-recovery-overview is-${recoveryStage}`}>
        <div className="mds-recovery-overview-title">
          <h1>Alex&apos;s JFK → Tokyo recovery</h1>
          <span className="mds-recovery-cancelled-badge">
            <AlertTriangle size={14} aria-hidden="true" />
            Cancelled flight
          </span>
        </div>
        <div className="mds-recovery-overview-meta">
          <span>Original: ANA NH 109 cancelled</span>
          <i aria-hidden="true" />
          <strong>{recoveryStatusLabel}</strong>
        </div>
        {(recoveryStage === 'checkpointed' ||
          (recoveryStage === 'ready' && state.workflowStatus === 'resumed')) && (
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
      </header>

      {recoveryStage === 'running' ? (
        <div ref={consoleRef} className="mds-recovery-active-console">
          <RecoveryLaunchCard
            stage={recoveryStage}
            compact
            resumeMode={isResumingFromCheckpoint}
            disabled
            onStart={startRecovery}
          />
        </div>
      ) : showDecisionDashboard ? (
        <div ref={consoleRef} className="mds-recovery-active-console">
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
                      : 'Searching live options'}
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
              />
            </div>
          </section>
        </div>
      ) : (
        <section
          className="mds-recovery-launch-system"
          aria-label="Start recovery"
        >
          <RecoveryLaunchCard
            stage={recoveryStage}
            errorDetail={workflowErrorDetail}
            disabled={state.isLoading}
            onStart={startRecovery}
          />
        </section>
      )}

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

      {showComposer && recoveryStage === 'ready' && (
        <ChatComposer state={state} recoveryMode />
      )}
    </div>
  );
}
