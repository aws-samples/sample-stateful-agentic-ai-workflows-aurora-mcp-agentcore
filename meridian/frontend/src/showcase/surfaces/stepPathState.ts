import { useState, type CSSProperties } from 'react';

/** The index of a click-stepped path. Nothing advances on its own. */
export function useStepper(count: number) {
  const [index, setIndex] = useState(0);
  return {
    index,
    back: () => setIndex(current => Math.max(0, current - 1)),
    next: () => setIndex(current => Math.min(count - 1, current + 1)),
    reset: () => setIndex(0),
  };
}

/** Where the moving chip sits: the centre of hop `at` in a lane of `count` hops. */
export function laneStyle(count: number, at: number): CSSProperties {
  return { '--mds-step-count': count, '--mds-step-at': at } as CSSProperties;
}
