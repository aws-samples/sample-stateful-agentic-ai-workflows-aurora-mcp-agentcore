import type { JourneyDocument } from '../../src/showcase/journey/types';
import { PAUSED_ACTIVITIES } from './pausedRecovery';

const unavailable = { status: 'unavailable' as const, reason: 'e2e fixture' };

type FixtureOptions = {
  /** Whether Aurora has recorded a Runtime session stop for the journey. */
  stopped: boolean;
  /** The latest execution's status. Defaults to `paused`. */
  status?: 'paused' | 'running' | 'abandoned';
  /** What the stopped session was doing. Defaults to `waiting`. */
  stoppedDuring?: 'waiting' | 'running';
  /** The last step the run saved before a mid-run stop. */
  lastStep?: string | null;
  /** The ranked options the saved checkpoint holds. */
  products?: object[];
};

/** A journey document for a recovery with saved progress, as the backend serves it.
 *
 * Every section the desk does not need to restore the run is unavailable.
 *
 * @param threadId The recovery thread the journey is active on.
 * @param options What the latest execution and the session stop record.
 */
export function pausedJourneyDocument(
  threadId: string,
  { stopped, status = 'paused', stoppedDuring = 'waiting', lastStep = null, products = [] }: FixtureOptions,
): JourneyDocument {
  return {
    journey_id: 'jrn_e2e',
    traveler_id: 'trv_meridian_demo',
    status: 'active',
    checkpoint_backend: { kind: 'Aurora workflow_snapshots', durable: true },
    active_thread_id: threadId,
    workflow: {
      status: 'observed',
      source: 'checkpoint',
      conversation_id: threadId,
      query: 'My flight to Tokyo was cancelled. Find me alternatives.',
      message: 'Workflow paused after a saved step.',
      workflow_status: 'paused',
      next_nodes: ['hold'],
      activities: PAUSED_ACTIVITIES as never,
      travelers_count: 2,
      execution_id: 'exe_e2e',
      resumed_after_restart: false,
    },
    executions: {
      status: 'observed',
      source: 'journey_executions',
      items: [{
        execution_id: 'exe_e2e', attempt: 1, worker_id: 'rt-wf-e2e/vm-0123456789ab', status,
        started_at: '2026-10-07 09:59:00+00', ended_at: null, lease_expires_at: null,
        runtime_session_id: 'rt-wf-e2e', microvm_id: 'vm-0123456789ab',
      }],
    },
    session_stops: stopped
      ? {
        status: 'observed',
        source: 'workflow_session_stops',
        items: [{
          runtime_session_id: 'rt-wf-e2e', outcome: 'stopped', stopped_at: '2026-10-07 10:00:00+00',
          stopped_during: stoppedDuring, last_step: lastStep,
        }],
      }
      : { status: 'unavailable', reason: 'No Runtime session was stopped for this journey.' },
    checkpoint: unavailable,
    selected_plan: unavailable,
    recommendations: products.length
      ? { status: 'observed', source: 'checkpoint', items: products }
      : unavailable,
    pending_decision: unavailable,
    conversation: unavailable,
    hold: unavailable,
    authorization: unavailable,
    rls: unavailable,
  } as JourneyDocument;
}
