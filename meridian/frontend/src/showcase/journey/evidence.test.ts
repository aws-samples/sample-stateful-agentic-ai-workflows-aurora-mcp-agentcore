import { describe, expect, it } from 'vitest';
import { hasLiveLease, hasVerifiedResume, parseDatabaseTime } from './evidence';
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
