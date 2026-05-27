import { useState } from 'react';
import { ChatComposer } from './components/ChatComposer';
import { ChatTranscript } from './components/ChatTranscript';
import { MemoryDrawer } from './components/MemoryDrawer';
import { PhaseSelector } from './components/PhaseSelector';
import { RecommendationCards } from './components/RecommendationCards';
import { TracePanel } from './components/TracePanel';
import { TravelerContextPanel } from './components/TravelerContextPanel';
import { TripDetailDrawer } from './components/TripDetailDrawer';
import type { MeridianShowcaseState } from './hooks/useMeridianShowcase';

const navItems = ['Concierge', 'Trips', 'Discover', 'Profile', 'Preferences', 'Messages'];

function BrandMark() {
  return <span className="mds-brand-mark" aria-hidden="true" />;
}

export function DesktopMeridianApp({ state }: { state: MeridianShowcaseState }) {
  const [memoryOpen, setMemoryOpen] = useState(false);

  return (
    <div className="mds-desktop-app">
      <aside className="mds-desktop-sidebar">
        <div className="mds-brand">
          <BrandMark />
          Meridian
        </div>
        <nav className="mds-nav-items" aria-label="Desktop navigation">
          {navItems.map((item, index) => (
            <button
              key={item}
              type="button"
              className={`mds-nav-item${index === 0 ? ' is-active' : ''}`}
              onClick={() => {
                if (item === 'Preferences') setMemoryOpen(true);
                if (item === 'Trips' && state.recommendations[0]) state.selectTrip(state.recommendations[0]);
              }}
            >
              <span className="mds-nav-icon" aria-hidden="true" />
              {item}
              {item === 'Messages' && <b>{state.messages.length}</b>}
            </button>
          ))}
        </nav>
        <div className="mds-sidebar-spacer" />
        <button type="button" className="mds-nav-item" onClick={() => setMemoryOpen(true)}>
          <span className="mds-nav-icon" aria-hidden="true" />
          Settings
        </button>
        <div className="mds-account-mini">
          <span className="mds-avatar" aria-hidden="true" />
          <div className="mds-account-copy">
            <strong>Alex Morgan</strong>
            <span>Explorer</span>
          </div>
        </div>
      </aside>

      <main className="mds-desktop-main">
        <div className="mds-top-actions">
          <span>{state.backendStatus === 'online' ? 'Live backend' : 'Backend offline'}</span>
          {state.isFallbackMode && <span className="mds-fallback-badge">Demo fallback</span>}
          <span>USD</span>
        </div>
        <div className="mds-headline-row">
          <div>
            <h1>Good morning, Alex.</h1>
            <p>Where would you like to go next?</p>
          </div>
          <PhaseSelector state={state} />
        </div>

        {state.error && (
          <button type="button" className="mds-error-banner" onClick={state.clearError}>
            {state.error}
          </button>
        )}

        <ChatTranscript state={state} />

        <div className="mds-assistant-line">
          {state.recommendations.length
            ? 'Here are the strongest recommendations for this traveler.'
            : 'Recommendations will appear after your first request.'}
        </div>

        <RecommendationCards state={state} />

        <div className="mds-main-actions">
          <button type="button" onClick={() => state.replayLastPrompt()} disabled={!state.lastPrompt || state.isLoading}>
            Rerun across {state.phaseLabel}
          </button>
          <button type="button" onClick={() => setMemoryOpen(true)}>
            Inspect memory
          </button>
        </div>

        <ChatComposer state={state} />
      </main>

      <aside className="mds-desktop-right">
        <TravelerContextPanel state={state} onOpenMemory={() => setMemoryOpen(true)} />
        <TracePanel state={state} />
      </aside>

      <TripDetailDrawer state={state} />
      <MemoryDrawer state={state} open={memoryOpen} onClose={() => setMemoryOpen(false)} />
    </div>
  );
}
