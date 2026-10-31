import { hasDurableSnapshot } from './showcaseProof';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import type { ShowcaseTraceSpan } from './showcaseAdapters';
import { isDurableField, isSnapshotTitle } from './spanTitles';
import { formatLatency, stepService, stepSourceLabel } from './stepSource';

export type RecoveryStage = 'action' | 'running' | 'saved' | 'ready';

export interface RecoveryEvidence {
  searchObserved: boolean;
  alternativesObserved: boolean;
  availabilityObserved: boolean;
  loyaltyObserved: boolean;
  memoryObserved: boolean;
  snapshotObserved: boolean;
  durableSnapshot: boolean;
}

function spanText(
  state: MeridianShowcaseState,
  index: number,
): string {
  const span = (state.traceSpans ?? [])[index];
  if (!span || span.status !== 'ok') return '';
  return [
    span.name,
    span.details,
    span.component,
    span.sql,
    ...span.fields.flatMap((field) => [field.label, field.value]),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

function hasRecoveryRequest(state: MeridianShowcaseState): boolean {
  const recoveryPattern = /flight.+cancell?ed|cancell?ed.+flight/i;
  return (
    recoveryPattern.test(state.lastPrompt ?? '') ||
    state.messages.some(
      (message) =>
        message.role === 'user' && recoveryPattern.test(message.text),
    )
  );
}

export function deriveRecoveryStage(state: MeridianShowcaseState): RecoveryStage {
  if (state.selectedPhase !== 5 || !hasRecoveryRequest(state) || state.error) {
    return 'action';
  }
  if (state.isLoading) return 'running';
  if (state.workflowStatus === 'paused') return 'saved';
  if (
    state.workflowStatus === 'resumed' ||
    state.workflowStatus === 'complete'
  ) {
    return 'ready';
  }
  return 'action';
}

export function deriveRecoveryEvidence(
  state: MeridianShowcaseState,
): RecoveryEvidence {
  if (state.selectedPhase !== 5) return {
    searchObserved: false, alternativesObserved: false, availabilityObserved: false,
    loyaltyObserved: false, memoryObserved: false, snapshotObserved: false, durableSnapshot: false,
  };
  const texts = (state.traceSpans ?? []).map((_, index) =>
    spanText(state, index),
  );
  const recommendationCount = state.recommendations?.length ?? 0;
  const snapshotSpans = texts.filter((text) =>
    /checkpoint|snapshot saved|saved step|postgres.?saver|workflow state/.test(text),
  );


  return {
    searchObserved:
      recommendationCount > 0 ||
      texts.some((text) =>
        /workflow node: search|semantic_trip_search|hybrid_trip_search|search_trips|hybrid retrieval|catalog search|searchagent/.test(
          text,
        ),
      ),
    alternativesObserved: recommendationCount > 0,
    availabilityObserved: texts.some((text) =>
      /availability fan-out|duration inventory|packageagent|availability_checks/.test(
        text,
      ),
    ),
    loyaltyObserved: texts.some((text) =>
      /loyalty|airline premier|tier applied/.test(text),
    ),
    memoryObserved: texts.some((text) =>
      /aurora recall|traveler memory|memoryagent|preference context|recall spans/.test(
        text,
      ),
    ),
    snapshotObserved: snapshotSpans.length > 0,
    durableSnapshot:
      hasDurableSnapshot(state.traceSpans ?? []),
  };
}

export type RecoveryStepState = 'is-ready' | 'is-pending' | 'is-current' | 'is-visited';
export type RecoveryStepId = 'understand' | 'search' | 'snapshot' | 'verify';

/** One of the four recovery workflow steps, as the backend has reported it. */
export interface RecoveryStepView {
  /** Which step this is, so a view never depends on its position in a list. */
  id: RecoveryStepId;
  state: RecoveryStepState;
  /** "Service, time" once the step is confirmed; the service alone when unmeasured. */
  source: string | null;
}

const CLASSIFY_NODE = /^Workflow node: classify/;
const SEARCH_NODE = /^Workflow node: search$/;
const VERIFY_NODE = /^Workflow node: availability/;
const isStepBoundary = (span: ShowcaseTraceSpan) =>
  /^Workflow node:/.test(span.name) || isSnapshotTitle(span.name);

/** The services a node's own spans called, in the order it called them. */
function nodeServices(spans: ShowcaseTraceSpan[], nodeIndex: number): string {
  const services: string[] = [];
  for (const span of spans.slice(nodeIndex + 1)) {
    if (isStepBoundary(span)) break;
    const service = stepService(span).replace(/ Data API$/, '');
    if (service !== 'Meridian app' && !services.includes(service)) services.push(service);
  }
  return services.join(' + ') || 'Strands Graph';
}

function nodeSource(spans: ShowcaseTraceSpan[], node: RegExp): string | null {
  const index = spans.findIndex(span => node.test(span.name) && span.status === 'ok');
  if (index < 0) return null;
  return [nodeServices(spans, index), formatLatency(spans[index].latencyMs)]
    .filter(Boolean).join(', ');
}

/** The snapshot written after the search node, and only a durable one. */
function snapshotSource(spans: ShowcaseTraceSpan[]): string | null {
  const search = spans.findIndex(span => SEARCH_NODE.test(span.name));
  const snapshot = search < 0
    ? undefined
    : spans.slice(search + 1).find(span => isSnapshotTitle(span.name));
  const durable = snapshot?.fields
    .some(field => isDurableField(field.label) && field.value === 'true');
  return snapshot && snapshot.status === 'ok' && durable ? stepSourceLabel(snapshot) : null;
}

const STEP_IDS: RecoveryStepId[] = ['understand', 'search', 'snapshot', 'verify'];

const pending = (id: RecoveryStepId): RecoveryStepView => (
  { id, state: 'is-pending', source: null });

/** Understand, search, snapshot and verify, each confirmed only by the spans
 *  the backend returned. A fresh run in flight claims no step: the trace arrives
 *  with the response. */
export function deriveRecoverySteps(
  spans: ShowcaseTraceSpan[],
  stage: RecoveryStage,
  { resumeMode, failed }: { resumeMode: boolean; failed: boolean },
): RecoveryStepView[] {
  if (failed || (stage === 'running' && !resumeMode)) return STEP_IDS.map(pending);
  if (stage === 'action') {
    return STEP_IDS.map(id => (id === 'understand'
      ? { id, state: 'is-ready', source: null } : pending(id)));
  }
  const sources: Record<RecoveryStepId, string | null> = {
    understand: nodeSource(spans, CLASSIFY_NODE),
    search: nodeSource(spans, SEARCH_NODE),
    snapshot: snapshotSource(spans),
    verify: nodeSource(spans, VERIFY_NODE),
  };
  return STEP_IDS.map((id): RecoveryStepView => {
    // Resuming changes nothing the paused run's spans confirmed; only the
    // verification the request is now running is marked in progress.
    if (stage === 'running' && id === 'verify') return { id, state: 'is-current', source: null };
    const source = sources[id];
    return source ? { id, state: 'is-visited', source } : pending(id);
  });
}
