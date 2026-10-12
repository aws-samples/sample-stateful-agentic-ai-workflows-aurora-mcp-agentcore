import { describe, expect, it } from 'vitest';

import { canStopSession } from './sessionStop';
import type { JourneyDocument, JourneyExecution, SessionStop } from './types';

const execution = (overrides: Partial<JourneyExecution> = {}): JourneyExecution => ({
  execution_id: 'exe_01', attempt: 1, worker_id: 'w', status: 'paused',
  started_at: '2026-10-07 10:00:00+00', ended_at: null, lease_expires_at: null, ...overrides,
});

const stop = (stopped_at: string): SessionStop => ({
  runtime_session_id: 'rt-wf-x', outcome: 'stopped', stopped_at,
  stopped_during: 'waiting', last_step: null,
});

const documentWith = (
  executions: JourneyExecution[] | null,
  stops: SessionStop[] | null = null,
): JourneyDocument => ({
  executions: executions
    ? { status: 'observed', source: 'journey_executions', items: executions }
    : { status: 'unavailable', reason: 'none' },
  session_stops: stops
    ? { status: 'observed', source: 'workflow_session_stops', items: stops }
    : { status: 'unavailable', reason: 'none' },
}) as unknown as JourneyDocument;

describe('canStopSession', () => {
  it('offers the stop while the latest execution is running or paused', () => {
    expect(canStopSession(documentWith([execution({ status: 'paused' })]))).toBe(true);
    expect(canStopSession(documentWith([execution({ status: 'running' })]))).toBe(true);
  });

  it.each(['succeeded', 'failed', 'abandoned'])('hides it when the latest execution is %s', status => {
    expect(canStopSession(documentWith([execution({ status })]))).toBe(false);
  });

  it('hides it without a document or without execution evidence', () => {
    expect(canStopSession(null)).toBe(false);
    expect(canStopSession(documentWith(null))).toBe(false);
    expect(canStopSession(documentWith([]))).toBe(false);
  });

  it('hides it once the newest stop is later than the latest execution started', () => {
    const document = documentWith(
      [execution()], [stop('2026-10-07 10:05:00.123+00'), stop('2026-10-07 09:00:00+00')]);
    expect(canStopSession(document)).toBe(false);
  });

  it('offers it again after a resume starts a newer execution than the stop', () => {
    const document = documentWith(
      [execution({ status: 'abandoned' }),
        execution({ execution_id: 'exe_02', attempt: 2, status: 'running',
          started_at: '2026-10-07 10:06:00+00' })],
      [stop('2026-10-07 10:05:00+00')]);
    expect(canStopSession(document)).toBe(true);
  });

  it('shows the control when either timestamp is missing or unreadable', () => {
    expect(canStopSession(documentWith([execution({ started_at: null })],
      [stop('2026-10-07 10:05:00+00')]))).toBe(true);
    expect(canStopSession(documentWith([execution()], [stop('not a time')]))).toBe(true);
  });
});
