import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
} from 'lucide-react';
import { useEffect, useRef, useState, type RefObject } from 'react';
import { ChatComposer } from './ChatComposer';
import type { AdoptableHold, MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { SHOWCASE_FINALE_PROMPT, type ShowcaseTraceSpan } from '../lib/showcaseAdapters';
import {
  deriveRecoveryEvidence,
  deriveRecoveryStage,
  deriveRecoverySteps,
  type RecoveryEvidence,
  type RecoveryStage,
  type RecoveryStepView,
} from '../lib/recoveryState';
import type { Product } from '../../types';
import { deriveWorkflowState } from '../lib/showcaseProof';
import { usePrefersReducedMotion } from '../lib/prefersReducedMotion';
import { useLiveCues } from '../hooks/useLiveCues';
import { RecoveryBriefing } from './RecoveryBriefing';
import { RecoveryChecks } from './RecoveryChecks';
import { RecoveryBoardingPass } from './RecoveryBoardingPass';
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

/** The spans the recovery steps read, for one recovery thread.
 *
 * A request clears the trace until its response arrives, so a resume shows the
 * steps the paused run confirmed. A resumed run returns the earlier spans again
 * without the checkpoint write time measured after that pause; the time is
 * carried over by span id, never re-derived.
 */
function useRecoveryStepSpans(
  spans: ShowcaseTraceSpan[], thread: string | null,
): ShowcaseTraceSpan[] {
  // What the last trace for this thread resolved to, and the trace it came
  // from. Kept in state so a render React discards cannot advance it.
  const [seen, setSeen] = useState<{
    thread: string | null; source: ShowcaseTraceSpan[]; spans: ShowcaseTraceSpan[];
  }>({ thread, source: [], spans: [] });
  const sameThread = seen.thread === thread;
  if (sameThread && (!spans.length || spans === seen.source)) return seen.spans;
  const measured = new Map((sameThread ? seen.spans : []).map(span => [span.id, span.latencyMs]));
  const merged = spans.map(span => (span.latencyMs == null && measured.get(span.id) != null
    ? { ...span, latencyMs: measured.get(span.id) ?? null } : span));
  setSeen({ thread, source: spans, spans: merged });
  return merged;
}

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

/** The recovery layout, kept in the URL while the layout study is open. */
function useRecoveryLayout() {
  const [recoveryLayout, setRecoveryLayout] = useState<RecoveryLayout>(
    initialRecoveryLayout,
  );
  const layoutReviewEnabled =
    typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('recoveryReview') === '1';
  const selectRecoveryLayout = (layout: RecoveryLayout) => {
    setRecoveryLayout(layout);
    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    url.searchParams.set('recoveryReview', '1');
    url.searchParams.set('recoveryLayout', layout);
    window.history.replaceState(null, '', url);
  };
  return { recoveryLayout, layoutReviewEnabled, selectRecoveryLayout };
}

/** Focus the title once for screen readers, and bring the console into view
 *  when a request starts. */
function useRecoveryDeskFocus(isLoading: boolean, prefersReducedMotion: boolean) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const consoleRef = useRef<HTMLDivElement>(null);
  const previousLoadingRef = useRef(false);

  useEffect(() => { headingRef.current?.focus({ preventScroll: true }); }, []);

  useEffect(() => {
    const wasLoading = previousLoadingRef.current;
    previousLoadingRef.current = isLoading;
    if (!isLoading || wasLoading) return;

    const frame = window.requestAnimationFrame(() => {
      if (typeof consoleRef.current?.scrollIntoView === 'function') {
        consoleRef.current.scrollIntoView({
          behavior: prefersReducedMotion ? 'auto' : 'smooth',
          block: 'start',
        });
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [isLoading, prefersReducedMotion]);

  return { headingRef, consoleRef };
}

/** The desk shows only Phase 5's recovery; any other phase leaves it empty. */
function recoveryDeskState(sourceState: MeridianShowcaseState): MeridianShowcaseState {
  return sourceState.selectedPhase === 5 ? sourceState : {
    ...sourceState, messages: [], recommendations: [], traceSpans: [],
    workflowStatus: null, workflowResumedAfterRestart: false, lastPrompt: null,
    conversationId: null, error: null,
  };
}

function workflowErrorDetailOf(spans: ShowcaseTraceSpan[]): string | null {
  const workflowErrorSpan = spans.find(
    (span) =>
      span.status === 'error' ||
      span.category === 'error' ||
      /workflow error|langgraph error/i.test(
        `${span.name} ${span.details ?? ''}`,
      ),
  );
  return workflowErrorSpan?.details ?? null;
}

function recoveryStatusLabel(stage: RecoveryStage, error: string | null): string {
  return stage === 'ready'
    ? 'Recovery plan ready'
    : stage === 'checkpointed'
      ? 'Shortlist saved · ready to verify'
      : stage === 'running'
        ? 'Recovery in progress'
        : error ? 'Recovery needs reconciliation' : 'Recovery ready to start';
}

function RecoveryOverview({ stage, error, showHeading, headingRef }: {
  stage: RecoveryStage;
  error: string | null;
  showHeading: boolean;
  headingRef: RefObject<HTMLHeadingElement>;
}) {
  return (
    <header className={`mds-recovery-overview is-${stage}`}>
      {showHeading && <div className="mds-recovery-overview-title">
        <h1 tabIndex={-1} ref={headingRef}>Alex&apos;s JFK to Tokyo recovery</h1>
        <span className="mds-recovery-cancelled-badge">
          <AlertTriangle size={14} aria-hidden="true" />
          Traveler-reported disruption
        </span>
      </div>}
      <div className="mds-recovery-overview-meta">
        {showHeading && <>
          <span>Request: rework a canceled JFK-to-Tokyo trip</span><i aria-hidden="true" />
        </>}
        <strong>{recoveryStatusLabel(stage, error)}</strong>
      </div>
    </header>
  );
}

function RecoveryContext({ stage, state }: {
  stage: RecoveryStage;
  state: MeridianShowcaseState;
}) {
  return (
    <>
      {(stage === 'checkpointed' || stage === 'action') && (
        <p className="mc-recovery-action-note">
          Recovery checks package availability and requests a 15-minute courtesy hold on the
          leading option. No payment is taken; booking confirmation happens later in Concierge.
        </p>
      )}
      <details className="mc-trip-context">
        <summary>
          <span className="mc-trip-context-route">
            <strong>JFK</strong><ArrowRight size={19} aria-hidden="true" /><strong>Tokyo</strong>
          </span>
          <span className="mc-trip-context-note">
            Original flight canceled<small>Traveler-reported · inspect itinerary</small>
          </span>
          <ChevronDown size={18} aria-hidden="true" />
        </summary>
        <RecoveryBoardingPass state={state} />
      </details>
    </>
  );
}

/** The hold this recovery recorded, and the step that takes it back to Alex. */
function RecoveryHoldHandoff({ state, journeyDocument, onOpenProof, onOpenConcierge }: {
  state: MeridianShowcaseState;
  journeyDocument?: JourneyDocument | null;
  onOpenProof: () => void;
  onOpenConcierge?: (hold: AdoptableHold) => void;
}) {
  const workflowProof = deriveWorkflowState(state.traceSpans);
  const savedHold = journeyDocument?.active_thread_id === state.conversationId
    && isObserved(journeyDocument?.hold) ? journeyDocument.hold : null;
  const handoffHold: AdoptableHold | null = savedHold?.status === 'held'
    ? savedHold
    : null;
  return (
    <>
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
      {!savedHold && workflowProof.holdId && <div className="mds-recovery-handoff">
        <div><strong>Read the receipt before continuing.</strong><span>Verify the held package, party, duration and total from Aurora.</span></div>
        <button type="button" className="mc-session-primary" onClick={onOpenProof}>Read booking receipt<ArrowRight size={16} aria-hidden="true" /></button>
      </div>}
    </>
  );
}

function RecoveryLayoutReview({ recoveryLayout, onSelect }: {
  recoveryLayout: RecoveryLayout;
  onSelect: (layout: RecoveryLayout) => void;
}) {
  const activeLayout =
    RECOVERY_LAYOUTS.find((option) => option.id === recoveryLayout) ??
    RECOVERY_LAYOUTS[0];
  return (
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
            onClick={() => onSelect(layout.id)}
          >
            {layout.label}
          </button>
        ))}
      </div>
    </section>
  );
}

function optionBadgeFor(state: MeridianShowcaseState) {
  const pricedRecommendations = (state.recommendations ?? []).filter(
    (product) => Number.isFinite(product.price),
  );
  const lowestPrice = pricedRecommendations.length
    ? Math.min(...pricedRecommendations.map((product) => product.price))
    : null;
  return (product: Product | null, index: number) => {
    if (!product) return `Option ${index + 2}`;
    if (lowestPrice !== null && product.price === lowestPrice) {
      return 'Lowest package price';
    }
    if ((product.available_sizes?.length ?? 0) > 1) {
      return 'Flexible duration';
    }
    return `Ranked option ${index + 2}`;
  };
}

function RecoveryAlternatives({ state, evidence }: {
  state: MeridianShowcaseState;
  evidence: RecoveryEvidence;
}) {
  const alternativeProducts = Array.from({ length: 3 }, (_, index) =>
    state.recommendations?.[index + 1] ?? null,
  );
  const optionBadge = optionBadgeFor(state);
  return (
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
              evidence.availabilityObserved && index < 2
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
  );
}

/** The decision, its alternatives and its support, once the workflow paused or finished. */
function RecoveryDecisionDashboard({
  state, stage, evidence, briefingRef, onStart, onOpenProof,
}: {
  state: MeridianShowcaseState;
  stage: RecoveryStage;
  evidence: RecoveryEvidence;
  briefingRef: RefObject<HTMLElement>;
  onStart: () => void;
  onOpenProof: () => void;
}) {
  const prefersReducedMotion = usePrefersReducedMotion();
  const topRecoveryOption = state.recommendations?.[0] ?? null;
  const hasConversation =
    state.messages.length > 0 || state.isLoading || Boolean(state.error);
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
    if (stage === 'checkpointed') {
      resumeRecovery();
    } else if (stage === 'ready') {
      if (topRecoveryOption) state.openTripDetails(topRecoveryOption);
    } else {
      onStart();
    }
  };
  const runWorkflowPrompt = (prompt: string) => {
    state.setSelectedPhase(5);
    void state.applyPhaseExample(prompt, true, 5);
  };
  const primaryActionLabel =
    stage === 'ready'
      ? 'Review this plan'
      : stage === 'checkpointed'
        ? 'Resume and request hold'
        : 'Start recovery';

  return (
    <section
      className="mds-recovery-decision-system"
      aria-label="Recovery decisions"
    >
      <div className="mds-recovery-decision-primary">
        <RecommendedRecoveryPlanCard
          product={topRecoveryOption}
          stage={stage}
          evidence={evidence}
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

        <RecoveryAlternatives state={state} evidence={evidence} />
      </div>

      <div className="mds-recovery-decision-support">
        <ConciergeAssistanceCard
          stage={stage}
          evidence={evidence}
          product={topRecoveryOption}
          disabled={state.isLoading}
          onHotel={() => runWorkflowPrompt(STAY_PROMPT)}
          onProtection={() => runWorkflowPrompt(PROTECTION_PROMPT)}
        />
        <AgentProofCard
          stage={stage}
          evidence={evidence}
          recommendationCount={state.recommendations?.length ?? 0}
          traceCount={state.traceSpans?.length ?? 0}
          onViewProof={onOpenProof}
        />
      </div>
    </section>
  );
}

/** The recovery's working area: the live console once a run starts, a
 *  reconciliation prompt when a response was lost, or the launch card. */
function RecoveryConsole({
  state, stage, evidence, steps, live, resumeMode, errorDetail, consoleRef, briefingRef,
  onStart, onOpenProof,
}: {
  state: MeridianShowcaseState;
  stage: RecoveryStage;
  evidence: RecoveryEvidence;
  steps: RecoveryStepView[];
  live: boolean;
  resumeMode: boolean;
  errorDetail: string | null;
  consoleRef: RefObject<HTMLDivElement>;
  briefingRef: RefObject<HTMLElement>;
  onStart: () => void;
  onOpenProof: () => void;
}) {
  const showDecisionDashboard = stage === 'checkpointed' || stage === 'ready';
  // One console for running, checkpointed and ready, so the step card stays
  // mounted and each step's change reads as a change, not a new card.
  if (stage === 'running' || showDecisionDashboard) {
    return (
      <div ref={consoleRef} className="mds-recovery-active-console">
        <RecoveryLaunchCard
          stage={stage}
          steps={steps}
          live={live}
          compact
          resumeMode={resumeMode}
          disabled
          onStart={onStart}
        />
        {showDecisionDashboard && <RecoveryDecisionDashboard
          state={state} stage={stage} evidence={evidence}
          briefingRef={briefingRef} onStart={onStart} onOpenProof={onOpenProof}
        />}
      </div>
    );
  }
  if (state.error && state.conversationId) {
    return (
      <section className="mds-recovery-launch-system" role="status">
        <h2>Check this recovery before continuing.</h2>
        <p>
          The last response was not received. Its thread is preserved; open System evidence
          or re-read this recovery to inspect saved progress.
        </p>
        <button type="button" onClick={onOpenProof}>Open System evidence</button>
      </section>
    );
  }
  return (
    <section
      className="mds-recovery-launch-system"
      aria-label="Start recovery"
    >
      <RecoveryLaunchCard
        stage={stage}
        steps={steps}
        errorDetail={errorDetail}
        disabled={state.isLoading}
        onStart={onStart}
      />
    </section>
  );
}

function RecoveryBriefingSection({ state, briefingRef }: {
  state: MeridianShowcaseState;
  briefingRef: RefObject<HTMLElement>;
}) {
  return (
    <section
      ref={briefingRef}
      className="mds-recovery-briefing"
      aria-label="Recovery briefing"
    >
      <div className="mds-recovery-briefing-head">
        <span>Recovery briefing</span>
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
            {state.error}
          </span>
          <span className="mds-error-banner-actions">
            {state.lastPrompt && !state.conversationId && (
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
  );
}

export function RecoveryWorkspace({
  state: sourceState,
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
  const prefersReducedMotion = usePrefersReducedMotion();
  const state = recoveryDeskState(sourceState);
  const recoveryStage = deriveRecoveryStage(state);
  const recoveryEvidence = deriveRecoveryEvidence(state);
  const workflowErrorDetail = workflowErrorDetailOf(state.traceSpans);
  const briefingRef = useRef<HTMLElement>(null);
  const { headingRef, consoleRef } = useRecoveryDeskFocus(state.isLoading, prefersReducedMotion);
  const { recoveryLayout, layoutReviewEnabled, selectRecoveryLayout } = useRecoveryLayout();
  const isResumingFromCheckpoint =
    recoveryStage === 'running' &&
    state.workflowStatus === 'paused' &&
    /resume|checkpoint/i.test(state.lastPrompt ?? '');
  const stepSpans = useRecoveryStepSpans(state.traceSpans, state.conversationId);
  const recoverySteps = deriveRecoverySteps(stepSpans, recoveryStage, {
    resumeMode: isResumingFromCheckpoint,
    failed: Boolean(workflowErrorDetail),
  });
  const liveCues = useLiveCues(state.conversationId, state.isLoading);

  const startRecovery = () => {
    state.setSelectedPhase(5);
    void state.applyPhaseExample(SHOWCASE_FINALE_PROMPT, true, 5);
  };
  const hasConversation =
    state.messages.length > 0 || state.isLoading || Boolean(state.error);
  // Before the traveler starts, the hero and the workflow it will run come
  // first; the hold note and the itinerary follow them on the stage.
  const launchFirst = recoveryStage === 'action' && !(state.error && state.conversationId);
  const recoveryContext = <RecoveryContext stage={recoveryStage} state={state} />;

  return (
    <div
      className={`mds-recovery-workspace${
        recoveryStage === 'running' ? ' is-running' : ''
      }${hasConversation ? ' has-conversation' : ''} is-layout-${recoveryLayout}`}
    >
      <RecoveryOverview
        stage={recoveryStage} error={state.error} showHeading={showHeading} headingRef={headingRef}
      />

      {!launchFirst && recoveryContext}

      {hasConversation && (
        <RecoveryChecks state={state} journeyDocument={journeyDocument} onOpenProof={onOpenProof} />
      )}

      <RecoveryHoldHandoff
        state={state} journeyDocument={journeyDocument}
        onOpenProof={onOpenProof} onOpenConcierge={onOpenConcierge}
      />

      {layoutReviewEnabled && (
        <RecoveryLayoutReview recoveryLayout={recoveryLayout} onSelect={selectRecoveryLayout} />
      )}

      <RecoveryConsole
        state={state} stage={recoveryStage} evidence={recoveryEvidence}
        steps={recoverySteps} live={liveCues} resumeMode={isResumingFromCheckpoint}
        errorDetail={workflowErrorDetail} consoleRef={consoleRef} briefingRef={briefingRef}
        onStart={startRecovery} onOpenProof={onOpenProof}
      />

      {launchFirst && recoveryContext}

      {hasConversation && <RecoveryBriefingSection state={state} briefingRef={briefingRef} />}

      {showComposer && recoveryStage === 'ready' && (
        <ChatComposer state={state} recoveryMode />
      )}
    </div>
  );
}
