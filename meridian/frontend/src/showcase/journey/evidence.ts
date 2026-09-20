import { useEffect, useState } from 'react';
import { isObserved, type JourneyDocument, type JourneyExecution } from './types';

export function parseDatabaseTime(value?: string | null): number {
  if (!value) return NaN;
  const normalized = value.replace(' ', 'T').replace(/([+-]\d{2})$/, '$1:00');
  // Older Data API responses omit the zone on UTC timestamp columns.
  return Date.parse(/[zZ]$|[+-]\d{2}:\d{2}$/.test(normalized) ? normalized : `${normalized}Z`);
}

export function useEvidenceClock(document: JourneyDocument | null): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const observed = parseDatabaseTime(document?.observed_at);
  return Number.isFinite(observed) && document?.received_at != null
    ? observed + Math.max(0, now - document.received_at) : now;
}

export function hasLiveLease(execution: JourneyExecution | null | undefined, now: number): boolean {
  return execution?.status === 'running' && parseDatabaseTime(execution.lease_expires_at) > now;
}

/** A second attempt is not proof of a successful resume. Require its receipt. */
export function hasVerifiedResume(document: JourneyDocument): boolean {
  const workflow = document.workflow;
  const executions = isObserved(document.executions) ? document.executions.items : [];
  const latest = executions[executions.length - 1];
  return isObserved(workflow) && workflow.workflow_status === 'resumed'
    && Boolean(workflow.resumed_from_checkpoint)
    && workflow.conversation_id === document.active_thread_id
    && Boolean(latest) && latest.status === 'succeeded'
    && workflow.execution_id === latest.execution_id;
}
