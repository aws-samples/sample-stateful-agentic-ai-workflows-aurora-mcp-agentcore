import { describe, expect, it } from 'vitest';
import { hasLiveLease, hasVerifiedResume, parseDatabaseTime, workflowStillRunning } from './evidence';
import type { JourneyDocument, JourneyExecution } from './types';

const execution = { execution_id: 'exe-2', status: 'running', lease_expires_at: '2026-09-06 17:00:00+00' } as JourneyExecution;

describe('execution evidence', () => {
  it.each(['2026-09-20 00:07:17.625721', '2026-09-20 00:07:17.625721+00', '2026-09-20T00:07:17.625721Z'])(
    'reads the UTC database instant independently of presenter timezone: %s', value => {
      expect(parseDatabaseTime(value)).toBe(Date.parse('2026-09-20T00:07:17.625Z'));
    },
  );
  it('expires a lease at the exact database deadline and never trusts a missing deadline', () => {
    expect(hasLiveLease(execution, Date.parse('2026-09-06T16:59:59Z'))).toBe(true);
    expect(hasLiveLease(execution, Date.parse('2026-09-06T17:00:00Z'))).toBe(false);
    expect(hasLiveLease({ ...execution, lease_expires_at: null }, 0)).toBe(false);
  });
  it('requires a completed resume receipt belonging to the latest execution', () => {
    const document = {
      active_thread_id: 'thread', executions: { status: 'observed', source: 'journey_executions', items: [execution] },
      workflow: { status: 'observed', source: 'checkpoint', workflow_status: 'resumed', conversation_id: 'thread', resumed_from_checkpoint: 'cp-1', execution_id: 'exe-2' },
    } as JourneyDocument;
    expect(hasVerifiedResume(document)).toBe(false);
    document.executions = { status: 'observed', source: 'journey_executions', items: [{ ...execution, status: 'failed' }] };
    expect(hasVerifiedResume(document)).toBe(false);
    document.executions = { status: 'observed', source: 'journey_executions', items: [{ ...execution, status: 'succeeded' }] };
    expect(hasVerifiedResume(document)).toBe(true);
    document.executions.items.push({ ...execution, execution_id: 'exe-3', status: 'succeeded' });
    expect(hasVerifiedResume(document)).toBe(false);
  });
});

describe('workflowStillRunning', () => {
  const read = (items: JourneyExecution[]) => ({
    observed_at: '2026-09-06T16:59:00Z', received_at: Date.parse('2026-09-06T17:30:00Z'),
    executions: { status: 'observed', source: 'journey_executions', items },
  }) as unknown as JourneyDocument;

  it('is true while the latest execution holds a lease on the database clock', () => {
    const now = Date.parse('2026-09-06T17:30:30Z');
    expect(workflowStillRunning(read([execution]), now)).toBe(true);
    expect(workflowStillRunning(read([execution]), Date.parse('2026-09-06T17:31:00Z'))).toBe(false);
  });

  it('is false for a finished execution and when executions are unavailable', () => {
    const now = Date.parse('2026-09-06T17:30:10Z');
    expect(workflowStillRunning(read([{ ...execution, status: 'succeeded' }]), now)).toBe(false);
    expect(workflowStillRunning({ executions: { status: 'unavailable', reason: 'x' } } as JourneyDocument, now)).toBe(false);
  });
});
