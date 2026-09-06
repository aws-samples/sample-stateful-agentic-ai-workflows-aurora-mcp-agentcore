import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchJourneyDocument, fetchJourneys } from '../../api/client';
import type { JourneyDocument } from './types';

/** The four surfaces from the journey shell spec.
 *
 * Named descriptively. A/B/C were design-review labels and do not belong in
 * the shipped interface.
 */
export type SurfaceId = 'concierge' | 'ladder' | 'recovery' | 'proof';

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
    label: 'Presenter proof',
    blurb: 'Evidence read back from Aurora',
  },
];

const VALID = new Set<string>(SURFACES.map((s) => s.id));

function readUrl(): { view: SurfaceId; journeyId: string | null } {
  if (typeof window === 'undefined') return { view: 'concierge', journeyId: null };
  const params = new URLSearchParams(window.location.search);
  const view = params.get('view');
  return {
    view: view && VALID.has(view) ? (view as SurfaceId) : 'concierge',
    journeyId: params.get('journey'),
  };
}

/** Keep the surface and journey in the URL so a refresh reloads backend state. */
export function useSurfaceUrlState() {
  const [{ view, journeyId }, setUrlState] = useState(readUrl);

  useEffect(() => {
    const onPop = () => setUrlState(readUrl());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const write = useCallback((next: { view?: SurfaceId; journeyId?: string | null }) => {
    setUrlState((current) => {
      const merged = {
        view: next.view ?? current.view,
        journeyId: next.journeyId === undefined ? current.journeyId : next.journeyId,
      };
      const params = new URLSearchParams(window.location.search);
      params.set('view', merged.view);
      if (merged.journeyId) params.set('journey', merged.journeyId);
      else params.delete('journey');
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

  return { view, journeyId, setView, setJourneyId };
}

export type JourneyState = {
  journeyId: string | null;
  document: JourneyDocument | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
};

/** Own the journey document: resolve an id, load it, and let a surface reload it.
 *
 * The whole continuity claim is that a refresh restores the conversation, the
 * plan, the pending decision, the hold and both executions from persisted
 * state, so the document is fetched rather than remembered.
 */
export function useJourney(
  journeyId: string | null,
  onResolveId: (id: string) => void,
  enabled: boolean,
): JourneyState {
  const [document, setDocument] = useState<JourneyDocument | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  // Held in a ref so resolving an id cannot itself retrigger the load.
  const resolve = useRef(onResolveId);
  resolve.current = onResolveId;
  const loadedNonce = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    // Publishing the resolved id changes this dependency. Without this the
    // surface immediately re-reads the document it just loaded, which costs a
    // few seconds of Data API round trips and leaves the control saying
    // "Reading" over data that is already on screen.
    if (journeyId && document?.journey_id === journeyId && nonce === loadedNonce.current) {
      return;
    }
    let cancelled = false;
    let resolvedFromList: string | null = null;

    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        let id = journeyId;
        if (!id) {
          const journeys = await fetchJourneys(1);
          if (!journeys.length) {
            if (!cancelled) {
              setDocument(null);
              setError(
                'No journey has been recorded yet. Run a Phase 5 recovery, or scripts/kill_and_resume_demo.py, to create one.',
              );
            }
            return;
          }
          id = journeys[0].journey_id;
          resolvedFromList = id;
        }
        const doc = await fetchJourneyDocument(id);
        if (cancelled) return;
        setDocument(doc);
        loadedNonce.current = nonce;
        // Publish the id only once the document is committed. Writing it into
        // the URL first changes this effect's dependency mid-flight, which
        // cancels the very run that was about to deliver the document.
        if (resolvedFromList) resolve.current(resolvedFromList);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [journeyId, enabled, nonce, document?.journey_id]);

  return {
    journeyId,
    document,
    loading,
    error,
    refresh: useCallback(() => setNonce((n) => n + 1), []),
  };
}
