/**
 * ConciergeApp — minimal `/` shell that renders only the dark ConciergeStudio.
 *
 * The marketing scroll (Hero, Journey, Trips, Memory, System, Vision) lives in
 * App.tsx and stays reachable at `/marketing`. The chalk-talk lives at
 * `/chalk-talk`. This route is intentionally bare so the concierge UI fills
 * the viewport — no top nav, no scroll, no light-mode chrome.
 */
import { AgentBridgeProvider } from './context/AgentBridge';
import { ConciergeStudio } from './sections/ConciergeStudio';

export default function ConciergeApp() {
  return (
    <AgentBridgeProvider>
      <div
        className="cs-stage-fullscreen"
        style={{
          minHeight: '100vh',
          background: '#04070b',
        }}
      >
        <ConciergeStudio />
      </div>
    </AgentBridgeProvider>
  );
}
