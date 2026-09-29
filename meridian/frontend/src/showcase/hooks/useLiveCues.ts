import { useRef } from 'react';
import { usePrefersReducedMotion } from '../lib/prefersReducedMotion';

/** A state change: a short fade with a small scale on the state icon. */
export const STATE_CHANGE = { duration: 0.24, ease: [0.22, 0.61, 0.36, 1] } as const;
/** A new trace step arriving: a short rise with a fade. */
export const STEP_ENTER = { duration: 0.26, ease: [0.22, 0.61, 0.36, 1] } as const;
/** The one emphasis: Aurora confirming the checkpoint is saved. */
export const CHECKPOINT_GLOW = { duration: 0.9, ease: 'easeOut' } as const;

/** Whether a surface may animate what changes next.
 *
 * Only after it has watched a run in flight for this same recovery while
 * mounted, so every cue follows a state change the backend reported. A surface
 * that opens onto state already recorded paints it still, and nothing moves
 * when the viewer prefers reduced motion.
 *
 * @param identity The recovery the surface shows, such as its thread id.
 * @param running Whether a request for it is in flight now.
 */
export function useLiveCues(identity: string | null | undefined, running: boolean): boolean {
  const reduced = usePrefersReducedMotion();
  const observed = useRef<string | null>(null);
  if (running && identity) observed.current = identity;
  return !reduced && Boolean(identity) && observed.current === identity;
}
