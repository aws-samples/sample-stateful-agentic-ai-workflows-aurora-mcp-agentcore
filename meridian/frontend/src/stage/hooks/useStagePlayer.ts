/**
 * useStagePlayer - controls span playback on the Demo Stage.
 *
 * The "player" walks through `spans` in order, advancing every
 * `spanDurationMs` while playing. Kiosk mode auto-loops across scenarios.
 * `prefers-reduced-motion` collapses the animation: spans are revealed
 * instantly but the timeline does not pulse.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { StageSpan } from '../types';

interface UseStagePlayerOptions {
  spans: StageSpan[];
  autoPlay?: boolean;
  /** Called when the last span finishes. */
  onComplete?: () => void;
  /** Total animation length budget (ms); spans are sized proportionally. */
  totalDurationMs?: number;
  /** Minimum dwell per span (ms). */
  minSpanMs?: number;
}

export interface StagePlayerState {
  /** Index of the currently active span (or -1 if not started). */
  activeIndex: number;
  /** Whether the player is currently advancing. */
  isPlaying: boolean;
  /** Whether all spans have completed. */
  isComplete: boolean;
  /** prefers-reduced-motion is in effect. */
  reducedMotion: boolean;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  next: () => void;
  prev: () => void;
  replay: () => void;
}

function prefersReducedMotion() {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function useStagePlayer({
  spans,
  autoPlay = true,
  onComplete,
  totalDurationMs = 4200,
  minSpanMs = 380,
}: UseStagePlayerOptions): StagePlayerState {
  const [activeIndex, setActiveIndex] = useState(-1);
  const [isPlaying, setIsPlaying] = useState(autoPlay);
  const [reducedMotion, setReducedMotion] = useState(prefersReducedMotion());
  const timeoutRef = useRef<number | null>(null);
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const handler = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    mq.addEventListener?.('change', handler);
    return () => mq.removeEventListener?.('change', handler);
  }, []);

  // Reset whenever the span list itself changes.
  useEffect(() => {
    setActiveIndex(-1);
    setIsPlaying(autoPlay);
  }, [spans, autoPlay]);

  const totalLatency = spans.reduce((a, s) => a + (s.latencyMs ?? 0), 0) || 1;

  const clearPending = useCallback(() => {
    if (timeoutRef.current != null) {
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  // Drive the playhead while playing.
  useEffect(() => {
    clearPending();
    if (!isPlaying || spans.length === 0) return;

    if (reducedMotion) {
      // Reveal everything immediately for accessibility.
      setActiveIndex(spans.length - 1);
      setIsPlaying(false);
      onCompleteRef.current?.();
      return;
    }

    if (activeIndex >= spans.length - 1) {
      // Hold on the last span, then complete.
      timeoutRef.current = window.setTimeout(() => {
        setIsPlaying(false);
        onCompleteRef.current?.();
      }, 480);
      return;
    }

    const nextIdx = activeIndex + 1;
    const nextSpan = spans[nextIdx];
    const share = (nextSpan.latencyMs ?? 60) / totalLatency;
    const dwell = Math.max(minSpanMs, Math.round(totalDurationMs * share));

    timeoutRef.current = window.setTimeout(() => {
      setActiveIndex(nextIdx);
    }, activeIndex < 0 ? 220 : dwell);

    return () => clearPending();
  }, [isPlaying, activeIndex, spans, totalLatency, totalDurationMs, minSpanMs, reducedMotion, clearPending]);

  const play = useCallback(() => {
    if (activeIndex >= spans.length - 1) {
      setActiveIndex(-1);
    }
    setIsPlaying(true);
  }, [activeIndex, spans.length]);

  const pause = useCallback(() => setIsPlaying(false), []);
  const toggle = useCallback(() => {
    if (activeIndex >= spans.length - 1) {
      setActiveIndex(-1);
      setIsPlaying(true);
      return;
    }
    setIsPlaying((p) => !p);
  }, [activeIndex, spans.length]);
  const next = useCallback(() => {
    setIsPlaying(false);
    setActiveIndex((i) => Math.min(spans.length - 1, i + 1));
  }, [spans.length]);
  const prev = useCallback(() => {
    setIsPlaying(false);
    setActiveIndex((i) => Math.max(-1, i - 1));
  }, []);
  const replay = useCallback(() => {
    setActiveIndex(-1);
    setIsPlaying(true);
  }, []);

  return {
    activeIndex,
    isPlaying,
    isComplete: activeIndex >= spans.length - 1 && !isPlaying,
    reducedMotion,
    play,
    pause,
    toggle,
    next,
    prev,
    replay,
  };
}
