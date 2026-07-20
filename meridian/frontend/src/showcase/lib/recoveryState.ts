import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';

export type RecoveryStage = 'action' | 'running' | 'ready';

export function deriveRecoveryStage(state: MeridianShowcaseState): RecoveryStage {
  const isRecoveryTurn =
    state.selectedPhase === 5 &&
    /flight.+cancelled|cancelled.+flight/i.test(state.lastPrompt ?? '');
  if (!isRecoveryTurn || state.error) return 'action';
  if (state.isLoading) return 'running';
  return state.messages.some((message) => message.role === 'bot')
    ? 'ready'
    : 'action';
}
