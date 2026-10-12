import { useLayoutEffect, useRef, useState } from 'react';
import { usePrefersReducedMotion } from '../lib/prefersReducedMotion';

// Reveal complete words when possible, without splitting a UTF-16 character.
function revealEnd(text: string, end: number): number {
  const limit = Math.min(text.length, end + 16);
  while (end < limit && !/\s/.test(text[end - 1])) end++;
  if (end < text.length && /[\uDC00-\uDFFF]/.test(text[end])) end++;
  return end;
}

/** Smooth real network deltas. History and authoritative corrections appear
 * immediately; an append-only completion drains the existing buffer once. */
export function useStreamingText(text: string, streaming: boolean, interrupted = false): string {
  const reducedMotion = usePrefersReducedMotion();
  const [visible, setVisible] = useState(() => streaming && !reducedMotion
    ? text.slice(0, revealEnd(text, Math.min(8, text.length))) : text);
  const source = useRef(text);
  const revealed = useRef(visible.length);
  const frame = useRef<number | null>(null);
  const wasStreaming = useRef(streaming && Boolean(text));

  useLayoutEffect(() => {
    const replacement = !text.startsWith(source.current);
    source.current = text;
    wasStreaming.current ||= streaming && Boolean(text);
    if (reducedMotion || interrupted || replacement || !wasStreaming.current) {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
      revealed.current = text.length;
      setVisible(text);
      return;
    }
    if (frame.current !== null || revealed.current >= text.length) return;

    let previous = performance.now();
    const advance = (now: number) => {
      const elapsed = now - previous;
      // Markdown needs at most 30 updates/sec. Keep a burst a fraction of a
      // second behind the wire instead of replaying a finished answer slowly.
      if (elapsed >= 32) {
        const remaining = source.current.length - revealed.current;
        const step = Math.max(1, Math.floor(Math.min(elapsed, 64) * Math.max(0.085, remaining / 240)));
        revealed.current = revealEnd(source.current, Math.min(source.current.length, revealed.current + step));
        setVisible(source.current.slice(0, revealed.current));
        previous = now;
      }
      frame.current = revealed.current < source.current.length ? requestAnimationFrame(advance) : null;
    };
    frame.current = requestAnimationFrame(advance);
  }, [text, streaming, interrupted, reducedMotion]);

  useLayoutEffect(() => () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
  }, []);

  return reducedMotion || interrupted ? text : visible;
}
