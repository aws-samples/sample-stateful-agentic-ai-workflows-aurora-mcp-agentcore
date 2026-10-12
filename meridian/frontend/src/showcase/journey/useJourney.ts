import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchJourneyDocument, fetchJourneys } from '../../api/client';
import type { JourneyDocument } from './types';

/** The five surfaces from the journey shell spec.
 *
 * Named descriptively. A/B/C were design-review labels and do not belong in
 * the shipped interface.
 */
export type SurfaceId = 'concierge' | 'ladder' | 'recovery' | 'proof' | 'briefing';

export const SURFACES: { id: SurfaceId; label: string; blurb: string }[] = [
  {
    id: 'concierge',
    label: 'Concierge',
    blurb: 'What the traveler sees',
  },
  {
    id: 'ladder',
    label: 'Capability ladder',
    blurb: 'Five phases, each one checkable',
  },
  {
    id: 'recovery',
    label: 'Recovery desk',
    blurb: 'One decision, one action',
  },
  {
    id: 'proof',
    label: 'System evidence',
    blurb: 'Evidence read back from Aurora',
  },
  {
    id: 'briefing',
    label: 'Solution briefing',
    blurb: 'How it is built, where the boundaries sit',
  },
];

const VALID = new Set<string>(SURFACES.map((s) => s.id));

function readUrl(): { view: SurfaceId; journeyId: string | null; threadId: string | null } {
  if (typeof window === 'undefined') return { view: 'concierge', journeyId: null, threadId: null };
  const params = new URLSearchParams(window.location.search);
  const view = params.get('view');
  return {
    view: view && VALID.has(view) ? (view as SurfaceId) : 'concierge',
    journeyId: params.get('journey'),
    threadId: params.get('thread'),
  };
}

/** Keep the surface and journey in the URL so a refresh reloads backend state. */
export function useSurfaceUrlState() {
  const [{ view, journeyId, threadId }, setUrlState] = useState(readUrl);

  useEffect(() => {
    const onPop = () => setUrlState(readUrl());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const write = useCallback((next: { view?: SurfaceId; journeyId?: string | null; threadId?: string | null }) => {
    setUrlState((current) => {
      const merged = {
        view: next.view ?? current.view,
        journeyId: next.journeyId === undefined ? current.journeyId : next.journeyId,
        threadId: next.threadId === undefined ? current.threadId : next.threadId,
      };
      const params = new URLSearchParams(window.location.search);
      params.set('view', merged.view);
      if (merged.journeyId) params.set('journey', merged.journeyId);
      else params.delete('journey');
      if (merged.threadId) params.set('thread', merged.threadId);
      else params.delete('thread');
      window.history.replaceState(
        null,
        '',
        `${window.location.pathname}?${params.toString()}`,
      );
      return merged;
    });
  }, []);

  // Stable identities. These are effect dependencies downstream, and a fresh
  // closure each render restarts the fetch, which cancels the one before it and
  // leaves the surface loading forever.
  const setView = useCallback((next: SurfaceId) => write({ view: next }), [write]);
  const setJourneyId = useCallback(
    (next: string | null) => write({ journeyId: next }),
    [write],
  );

  const selectJourney = useCallback((id: string) => write({ journeyId: id, threadId: null }), [write]);
  return { view, journeyId, threadId, setView, setJourneyId, selectJourney };
}

/** How long after one in-flight read finishes the next one starts. */
export const JOURNEY_POLL_MS = 1000;

export type JourneyState = {
  journeyId: string | null;
  document: JourneyDocument | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
};

type JourneyRead = {
  key: string | null;
  document: JourneyDocument | null;
  loading: boolean;
  error: string | null;
};

const emptyRead: JourneyRead = { key: null, document: null, loading: false, error: null };

/** Resolve only the selected recovery; never adopt another thread's latest journey. */
async function readJourney(
  id: string | null,
  threadId: string | null | undefined,
  signal: AbortSignal,
): Promise<JourneyDocument | null> {
  if (!id && threadId) {
    const journeys = await fetchJourneys(1, signal, threadId);
    id = journeys.find(item => item.active_thread_id === threadId)?.journey_id ?? null;
  }
  if (!id) return null;
  const doc = await fetchJourneyDocument(id, signal);
  if (threadId && doc.active_thread_id !== threadId) {
    throw new Error('The journey does not match this recovery thread. '
      + 'Re-read after the workflow saves its progress.');
  }
  return doc;
}

function missingJourney(threadId: string | null | undefined): string {
  return threadId
    ? 'No journey has been recorded for this recovery yet. '
      + 'Re-read after the workflow saves its progress.'
    : 'No journey has been recorded yet. Start a recovery to create one.';
}

/** Read committed journey evidence, serially polling only during an active run.
 *
 * Aurora commits the lease, authorization and checkpoints during the request.
 * Each next read starts one second after the previous read ends. The transition
 * out of polling aborts that read and performs one final read of the outcome.
 * A refresh of saved evidence never animates or invents intermediate progress.
 */
export function useJourney(
  journeyId: string | null,
  onResolveId: (id: string) => void,
  enabled: boolean,
  threadId?: string | null,
  poll = false,
): JourneyState {
  const [read, setRead] = useState<JourneyRead>(emptyRead);
  const [nonce, setNonce] = useState(0);
  const resolve = useRef(onResolveId);
  useEffect(() => { resolve.current = onResolveId; }, [onResolveId]);
  // A resolved journey id in the URL must not cancel its own thread read.
  const selectedId = threadId ? null : journeyId;
  const key = enabled ? threadId || selectedId : null;

  useEffect(() => {
    if (!key) {
      setRead(emptyRead);
      return;
    }
    let cancelled = false;
    let id = selectedId;
    let controller: AbortController;
    let timeout: number;
    let next: number;
    const load = async () => {
      controller = new AbortController();
      timeout = window.setTimeout(() => controller.abort(), 60000);
      setRead(current => ({
        ...(current.key === key ? current : emptyRead), key, loading: true,
        error: poll && current.key === key ? current.error : null,
      }));
      try {
        const doc = await readJourney(id, threadId, controller.signal);
        if (cancelled) return;
        setRead({ key, document: doc, loading: false,
          error: doc || poll ? null : missingJourney(threadId) });
        if (doc) {
          if (id !== doc.journey_id) resolve.current(doc.journey_id);
          id = doc.journey_id;
        }
      } catch (err) {
        if (!cancelled) setRead(current => ({ ...current, error: controller.signal.aborted
          ? 'Reading the saved journey took too long. Check the connection and try again.'
          : err instanceof Error ? err.message : String(err) }));
      } finally {
        window.clearTimeout(timeout);
        if (!cancelled) {
          setRead(current => ({ ...current, loading: false }));
          if (poll) next = window.setTimeout(() => { void load(); }, JOURNEY_POLL_MS);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
      window.clearTimeout(next);
      controller?.abort();
    };
  }, [key, selectedId, threadId, nonce, poll]);

  const current = key && read.key === key ? read : emptyRead;
  return {
    journeyId, document: current.document, loading: current.loading, error: current.error,
    refresh: useCallback(() => setNonce(n => n + 1), []),
  };
}
