import { useEffect, useState } from 'react';

/** Keep browser fullscreen separate from the presenter's windowed preview. */
export function usePresentationMode() {
  const [fullscreen, setFullscreen] = useState(() =>
    Boolean(document.fullscreenElement || window.matchMedia?.('(display-mode: fullscreen)').matches),
  );
  const [preview, setPreview] = useState(false);
  const [projector, setProjector] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const displayMode = window.matchMedia?.('(display-mode: fullscreen)');
    const sync = () => {
      const active = Boolean(document.fullscreenElement || displayMode?.matches);
      setFullscreen(active);
      if (active) setError(null);
    };
    document.addEventListener('fullscreenchange', sync);
    displayMode?.addEventListener('change', sync);
    return () => {
      document.removeEventListener('fullscreenchange', sync);
      displayMode?.removeEventListener('change', sync);
    };
  }, []);

  const present = async () => {
    setError(null);
    if (!document.documentElement.requestFullscreen) {
      setError('Fullscreen is unavailable in this browser. Use a desktop browser to present.');
      return;
    }
    try {
      // Include document-level dialog portals so trip details remain operable.
      await document.documentElement.requestFullscreen();
    } catch {
      setError('Fullscreen could not start. Select Present fullscreen to try again.');
    }
  };

  return { fullscreen, preview, setPreview, projector, setProjector, error, present };
}

export type PresentationMode = ReturnType<typeof usePresentationMode>;
