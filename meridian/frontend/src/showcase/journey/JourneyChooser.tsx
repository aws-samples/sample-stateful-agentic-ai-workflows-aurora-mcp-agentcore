import { useEffect, useState } from 'react';
import { fetchJourneys } from '../../api/client';
import { runWithDeadline } from '../../api/request';
import type { JourneySummary } from './types';

export function JourneyChooser({ disabled, onSelect }: { disabled: boolean; onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [journeys, setJourneys] = useState<JourneySummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void runWithDeadline(signal => fetchJourneys(50, signal), controller.signal, 15_000)
      .then(rows => {
        if (!controller.signal.aborted) setJourneys(rows.filter(row => !row.active_thread_id?.startsWith('concierge:')));
      }).catch(() => {
        if (!controller.signal.aborted) setError('Saved journeys could not be read. Close and reopen to try again.');
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [open]);
  return <details className="mc-saved-journeys" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Open a saved recovery</summary>
    {loading ? <p role="status">Reading saved journeys…</p> : error ? <p role="alert">{error}</p>
      : journeys.length ? <ul>{journeys.map(journey => <li key={journey.journey_id}>
        <button type="button" disabled={disabled} onClick={() => { onSelect(journey.journey_id); setOpen(false); }}>
          {journey.journey_id} · {journey.status} · {new Date(journey.updated_at).toLocaleString()}
        </button>
      </li>)}</ul> : <p>No saved recoveries were returned.</p>}
  </details>;
}
