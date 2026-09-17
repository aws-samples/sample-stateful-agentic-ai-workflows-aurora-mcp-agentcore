import { useEffect, useState } from 'react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';

export function RequestWaitNotice({ state, onReadRecovery }: {
  state: MeridianShowcaseState; onReadRecovery: () => void;
}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!state.isLoading) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [state.isLoading]);
  if (!state.isLoading && !(state.error && state.selectedPhase === 5 && state.conversationId)) return null;
  return <div className="mc-request-wait" role="status">
    <div>{state.isLoading
      ? <><strong>Waiting for Meridian · {Math.max(0, Math.floor((now - (state.requestStartedAt ?? now)) / 1000))}s</strong><p>Responses may take up to 55 seconds. Stopping the wait does not cancel a saved action.</p></>
      : <><strong>Check the saved recovery</strong><p>{state.error}</p></>}
    </div>
    {state.isLoading
      ? <button type="button" onClick={state.stopWaiting}>Stop waiting</button>
      : <button type="button" onClick={onReadRecovery}>Re-read this recovery</button>}
  </div>;
}
