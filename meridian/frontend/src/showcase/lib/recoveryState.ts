import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';

export type RecoveryStage = 'action' | 'running' | 'checkpointed' | 'ready';

export interface RecoveryEvidence {
  searchObserved: boolean;
  alternativesObserved: boolean;
  availabilityObserved: boolean;
  loyaltyObserved: boolean;
  memoryObserved: boolean;
  checkpointObserved: boolean;
  durableCheckpoint: boolean;
}

function spanText(
  state: MeridianShowcaseState,
  index: number,
): string {
  const span = (state.traceSpans ?? [])[index];
  if (!span) return '';
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
  if (state.workflowStatus === 'paused') return 'checkpointed';
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
  const texts = (state.traceSpans ?? []).map((_, index) =>
    spanText(state, index),
  );
  const recommendationCount = state.recommendations?.length ?? 0;
  const checkpointSpans = texts.filter((text) =>
    /checkpoint|postgres.?saver|workflow state/.test(text),
  );
  const durableFromTrace = checkpointSpans.some((text) =>
    /postgressaver|durability aurora|checkpoint_store checkpoints/.test(text),
  );

  return {
    searchObserved:
      recommendationCount > 0 ||
      texts.some((text) =>
        /workflow node: search|semantic_trip_search|hybrid retrieval|catalog search|searchagent/.test(
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
      /loyalty|traveler profile|airline premier|tier applied/.test(text),
    ),
    memoryObserved: texts.some((text) =>
      /aurora recall|traveler memory|memoryagent|preference context|recall spans/.test(
        text,
      ),
    ),
    checkpointObserved: checkpointSpans.length > 0,
    durableCheckpoint:
      state.backendHealth?.checkpoint_durable === true || durableFromTrace,
  };
}
