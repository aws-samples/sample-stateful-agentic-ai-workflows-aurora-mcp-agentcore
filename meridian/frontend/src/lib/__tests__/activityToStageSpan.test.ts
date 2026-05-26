import { describe, expect, it } from 'vitest';
import {
  activitiesToStageSpans,
  activityToStageSpan,
  buildReasoningChain,
} from '../activityToStageSpan';
import type { ActivityEntry } from '../../types';

describe('activityToStageSpan', () => {
  it('maps telemetry category to span kind', () => {
    const entry: ActivityEntry = {
      id: 'a1',
      timestamp: '2026-01-01T00:00:00Z',
      activity_type: 'reasoning',
      title: 'Recall traveler preferences',
      agent_name: 'MemoryAgent',
      execution_time_ms: 42,
      telemetry: { category: 'memory_long', component: 'Aurora PostgreSQL', status: 'ok' },
    };
    const span = activityToStageSpan(entry, 0);
    expect(span.kind).toBe('memory');
    expect(span.system).toBe('memory');
    expect(span.latencyMs).toBe(42);
  });

  it('builds reasoning chain from span names', () => {
    const spans = activitiesToStageSpans([
      {
        id: '1',
        timestamp: '',
        activity_type: 'delegation',
        title: 'Supervisor processing',
      },
      {
        id: '2',
        timestamp: '',
        activity_type: 'search',
        title: 'Hybrid search',
      },
    ] as ActivityEntry[]);
    expect(buildReasoningChain(spans)).toContain('→');
  });
});
