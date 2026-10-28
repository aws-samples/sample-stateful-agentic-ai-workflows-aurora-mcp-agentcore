import { useEffect, useState } from 'react';
import { isObserved, type JourneyDocument, type JourneyExecution } from './types';

export function parseDatabaseTime(value?: string | null): number {
  if (!value) return NaN;
  const normalized = value.replace(' ', 'T').replace(/([+-]\d{2})$/, '$1:00');
  // Older Data API responses omit the zone on UTC timestamp columns.
  return Date.parse(/[zZ]$|[+-]\d{2}:\d{2}$/.test(normalized) ? normalized : `${normalized}Z`);
}

/** The database clock at `now` on this browser's clock, when the document says when it was read. */
function databaseNow(document: JourneyDocument | null, now: number): number {
  const observed = parseDatabaseTime(document?.observed_at);
  return Number.isFinite(observed) && document?.received_at != null
    ? observed + Math.max(0, now - document.received_at) : now;
}

export function useEvidenceClock(document: JourneyDocument | null): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return databaseNow(document, now);
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

/** Whether the journey's latest execution still holds a live lease, so its worker is running. */
export function workflowStillRunning(document: JourneyDocument, now = Date.now()): boolean {
  if (!isObserved(document.executions)) return false;
  const items = document.executions.items;
  return hasLiveLease(items[items.length - 1], databaseNow(document, now));
}
