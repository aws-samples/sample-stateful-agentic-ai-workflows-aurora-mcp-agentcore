import {
  AlertTriangle,
  ArrowRight,
  Sparkles,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { ChatComposer } from './ChatComposer';
import type { AdoptableHold, MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { SHOWCASE_FINALE_PROMPT } from '../lib/showcaseAdapters';
import {
  deriveRecoveryEvidence,
  deriveRecoveryStage,
} from '../lib/recoveryState';
import { deriveWorkflowState } from '../lib/showcaseProof';
import { prefersReducedMotion } from '../lib/prefersReducedMotion';
import { RecoveryBriefing } from './RecoveryBriefing';
import { RecoveryBoardingPass } from './RecoveryBoardingPass';
import { TripHoldReceipt } from './TripHoldReceipt';
import { HoldReceipt } from './HoldReceipt';
import { isObserved, type JourneyDocument } from '../journey/types';
import {
  AgentProofCard,
  ConciergeAssistanceCard,
  PackageOptionCard,
  RecoveryLaunchCard,
  RecommendedRecoveryPlanCard,
} from './RecoveryDecisionCards';

const STAY_PROMPT =
  'Find a well-rated hotel near Haneda for tonight with lounge access and an easy airport transfer.';
const PROTECTION_PROMPT =
  'Review my trip protection and change-fee options before rebooking the canceled Tokyo flight.';

type RecoveryLayout = 'command' | 'focus' | 'journey';

const RECOVERY_LAYOUTS: {
  id: RecoveryLayout;
  label: string;
  description: string;
}[] = [
  {
    id: 'command',
    label: 'Command',
    description: 'Dominant decision with a compact operational rail',
  },
  {
    id: 'focus',
    label: 'Focus',
    description: 'Full-width decision, comparison, then proof',
  },
  {
    id: 'journey',
    label: 'Journey',
    description: 'Plan and concierge first, alternatives and evidence next',
  },
];

function initialRecoveryLayout(): RecoveryLayout {
  if (typeof window === 'undefined') return 'command';
  const layout = new URLSearchParams(window.location.search).get(
    'recoveryLayout',
  );
  return RECOVERY_LAYOUTS.some((option) => option.id === layout)
    ? (layout as RecoveryLayout)
    : 'command';
}

export function RecoveryWorkspace({
  state,
  onOpenProof = () => {},
  onOpenConcierge,
  showComposer = true,
  showHeading = true,
  journeyDocument,
}: {
  state: MeridianShowcaseState;
  onOpenProof?: () => void;
  /** Take the held package back to the concierge, where the traveler confirms the trip. */
  onOpenConcierge?: (hold: AdoptableHold) => void;
  showComposer?: boolean;
  showHeading?: boolean;
  journeyDocument?: JourneyDocument | null;
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
  const headingRef = useRef<HTMLHeadingElement>(null);
  const consoleRef = useRef<HTMLDivElement>(null);
  const previousLoadingRef = useRef(false);
  const [recoveryLayout, setRecoveryLayout] = useState<RecoveryLayout>(
    initialRecoveryLayout,
  );
  const layoutReviewEnabled =
    typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('recoveryReview') === '1';
  const activeLayout =
    RECOVERY_LAYOUTS.find((option) => option.id === recoveryLayout) ??
    RECOVERY_LAYOUTS[0];
  const isResumingFromCheckpoint =
    recoveryStage === 'running' &&
    state.workflowStatus === 'paused' &&
    /resume|checkpoint/i.test(state.lastPrompt ?? '');

  useEffect(() => { headingRef.current?.focus({ preventScroll: true }); }, []);

  useEffect(() => {
    const wasLoading = previousLoadingRef.current;
    previousLoadingRef.current = state.isLoading;
    if (!state.isLoading || wasLoading) return;

    const frame = window.requestAnimationFrame(() => {
      if (typeof consoleRef.current?.scrollIntoView === 'function') {
        consoleRef.current.scrollIntoView({
          behavior: prefersReducedMotion ? 'auto' : 'smooth',
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
    briefing.scrollIntoView({
      behavior: prefersReducedMotion ? 'auto' : 'smooth',
      block: 'start',
    });
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
  const selectRecoveryLayout = (layout: RecoveryLayout) => {
    setRecoveryLayout(layout);
    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    url.searchParams.set('recoveryReview', '1');
    url.searchParams.set('recoveryLayout', layout);
    window.history.replaceState(null, '', url);
  };
  const hasConversation =
    state.messages.length > 0 || state.isLoading || Boolean(state.error);
  const workflowProof = deriveWorkflowState(state.traceSpans);
  const savedHold = journeyDocument?.active_thread_id === state.conversationId && isObserved(journeyDocument?.hold) ? journeyDocument.hold : null;
  const handoffHold: AdoptableHold | null = savedHold?.status === 'held'
    ? savedHold
    : !savedHold && workflowProof.holdId && workflowProof.holdStatus === 'held' && topRecoveryOption
      ? {
        booking_id: workflowProof.holdId,
        package_id: topRecoveryOption.product_id,
        duration: null,
        travelers_count: null,
        hold_expires_at: workflowProof.holdExpiresAt || null,
        hold_created_at: workflowProof.holdCreatedAt || null,
        status: 'held',
      }
      : null;
  const recoveryStatusLabel =
    recoveryStage === 'ready'
      ? 'Recovery plan ready'
      : recoveryStage === 'checkpointed'
        ? 'Shortlist saved · ready to verify'
        : recoveryStage === 'running'
          ? 'Recovery in progress'
          : 'Recovery ready to start';
  const primaryActionLabel =
    recoveryStage === 'ready'
      ? 'Review this plan'
      : recoveryStage === 'checkpointed'
        ? 'Resume and verify'
        : 'Start recovery';
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
      }${hasConversation ? ' has-conversation' : ''} is-layout-${recoveryLayout}`}
    >
      <header className={`mds-recovery-overview is-${recoveryStage}`}>
        {showHeading && <div className="mds-recovery-overview-title">
          <h1 tabIndex={-1} ref={headingRef}>Alex&apos;s JFK to Tokyo recovery</h1>
          <span className="mds-recovery-cancelled-badge">
            <AlertTriangle size={14} aria-hidden="true" />
            Traveler-reported disruption
          </span>
        </div>}
        <div className="mds-recovery-overview-meta">
          {showHeading && <><span>Request: rework a canceled JFK-to-Tokyo trip</span><i aria-hidden="true" /></>}
          <strong>{recoveryStatusLabel}</strong>
        </div>
      </header>

      <RecoveryBoardingPass state={state} />

      {(savedHold || workflowProof.holdId) && <HoldReceipt
        holdId={savedHold?.booking_id ?? workflowProof.holdId}
        createdAt={savedHold?.hold_created_at ?? workflowProof.holdCreatedAt}
        expiresAt={savedHold?.hold_expires_at ?? workflowProof.holdExpiresAt}
        observedAt={savedHold?.observed_at}
        receivedAt={savedHold ? journeyDocument?.received_at : undefined}
        status={savedHold?.status ?? workflowProof.holdStatus}
        compact
      />}
      {handoffHold && onOpenConcierge && (
        <div className="mds-recovery-handoff">
          <div>
            <strong>Bring it home.</strong>
            <span>The package is held. Alex confirms the trip with the concierge; the booking policy decides before Aurora confirms.</span>
          </div>
          <button type="button" className="mc-session-primary" onClick={() => onOpenConcierge(handoffHold)}>
            Take it back to Alex<ArrowRight size={16} aria-hidden="true" />
          </button>
        </div>
      )}
      {state.tripHolds?.slice(-1).map(hold => <TripHoldReceipt key={hold.order.order_id} hold={hold} compact />)}

      {layoutReviewEnabled && (
        <section
          className="mds-recovery-layout-review"
          aria-label="Recovery layout prototypes"
        >
          <span>
            <strong>Layout study</strong>
            <small>{activeLayout.description}</small>
          </span>
          <div role="group" aria-label="Choose a recovery layout">
            {RECOVERY_LAYOUTS.map((layout) => (
              <button
                key={layout.id}
                type="button"
                className={recoveryLayout === layout.id ? 'is-active' : ''}
                aria-pressed={recoveryLayout === layout.id}
                onClick={() => selectRecoveryLayout(layout.id)}
              >
                {layout.label}
              </button>
            ))}
          </div>
        </section>
      )}

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
                    <PackageOptionCard
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
