import { parseDatabaseTime } from './evidence';
import type { JourneyDocument } from './types';
import { isObserved } from './types';

/**
 * Whether the stop control should be offered for this journey.
 *
 * Aurora shows a live session when the latest execution is running or paused.
 * A stop that landed on a paused run leaves it paused, so the control also
 * hides once the newest recorded stop is later than that execution started.
 * Both timestamps come from the same database. A missing or unreadable one
 * leaves the control shown, because the server refuses a repeat stop anyway.
 */
export function canStopSession(document: JourneyDocument | null): boolean {
  if (!document || !isObserved(document.executions)) return false;
  const latest = document.executions.items[document.executions.items.length - 1];
  if (latest?.status !== 'running' && latest?.status !== 'paused') return false;
  const stops = isObserved(document.session_stops) ? document.session_stops.items : [];
  const stoppedAt = parseDatabaseTime(stops[0]?.stopped_at);
  const startedAt = parseDatabaseTime(latest.started_at);
  return !(Number.isFinite(stoppedAt) && Number.isFinite(startedAt) && stoppedAt > startedAt);
}
