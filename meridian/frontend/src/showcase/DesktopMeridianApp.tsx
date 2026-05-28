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
import { ALEX_IMAGE_URL, ALEX_NAME } from './lib/personas';

type NavItemId = 'concierge' | 'trips' | 'discover' | 'profile' | 'preferences' | 'messages';

const navItems: { id: NavItemId; label: string }[] = [
  { id: 'concierge', label: 'Concierge' },
  { id: 'trips', label: 'Trips' },
  { id: 'discover', label: 'Discover' },
  { id: 'profile', label: 'Profile' },
  { id: 'preferences', label: 'Preferences' },
  { id: 'messages', label: 'Messages' },
];

function BrandMark() {
  return <span className="mds-brand-mark" aria-hidden="true" />;
}

function NavIcon({ id }: { id: NavItemId | 'settings' }) {
  if (id === 'concierge') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 9V6a4 4 0 1 1 8 0v3" />
        <path d="M4 11h16l-1 9H5l-1-9Z" />
        <path d="M9.5 13.5h5" />
      </svg>
    );
  }
  if (id === 'trips') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="7" width="18" height="13" rx="2" />
        <path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
        <path d="M3 12h18" />
      </svg>
    );
  }
  if (id === 'discover') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 21s7-4.4 7-10a7 7 0 1 0-14 0c0 5.6 7 10 7 10Z" />
        <circle cx="12" cy="11" r="2.2" />
      </svg>
    );
  }
  if (id === 'profile') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="8" r="3.3" />
        <path d="M5 20a7 7 0 0 1 14 0" />
      </svg>
    );
  }
  if (id === 'preferences' || id === 'settings') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="3.1" />
        <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.7l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.7-.3 1.6 1.6 0 0 0-1 1.5V22a2 2 0 1 1-4 0v-.2a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.7.3l-.1.1a2 2 0 0 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.7 1.6 1.6 0 0 0-1.5-1H2a2 2 0 1 1 0-4h.2a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.7l-.1-.1a2 2 0 0 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.7.3h.1a1.6 1.6 0 0 0 1-1.5V2a2 2 0 1 1 4 0v.2a1.6 1.6 0 0 0 1 1.5h.1a1.6 1.6 0 0 0 1.7-.3l.1-.1a2 2 0 0 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.7v.1a1.6 1.6 0 0 0 1.5 1H22a2 2 0 1 1 0 4h-.2a1.6 1.6 0 0 0-1.5 1Z" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 6h16v12H4z" />
      <path d="m4 7 8 6 8-6" />
    </svg>
  );
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
              key={item.id}
              type="button"
              className={`mds-nav-item${index === 0 ? ' is-active' : ''}`}
              onClick={() => {
                if (item.id === 'preferences') setMemoryOpen(true);
                if (item.id === 'trips' && state.recommendations[0]) state.selectTrip(state.recommendations[0]);
              }}
            >
              <span className="mds-nav-icon" aria-hidden="true">
                <NavIcon id={item.id} />
              </span>
              {item.label}
              {item.id === 'messages' && <b>{state.messages.length}</b>}
            </button>
          ))}
        </nav>
        <div className="mds-sidebar-spacer" />
        <button type="button" className="mds-nav-item" onClick={() => setMemoryOpen(true)}>
          <span className="mds-nav-icon" aria-hidden="true">
            <NavIcon id="settings" />
          </span>
          Settings
        </button>
        <div className="mds-account-mini">
          <span className="mds-avatar is-photo" aria-hidden="true">
            <img src={ALEX_IMAGE_URL} alt={ALEX_NAME} loading="lazy" />
          </span>
          <div className="mds-account-copy">
            <strong>Alex Morgan</strong>
            <span>Explorer</span>
          </div>
        </div>
      </aside>

      <main className="mds-desktop-main">
        {/* Single scroll surface - everything before the sticky composer
            (header, chat, grid) scrolls together so the conversation flows
            naturally into the result cards below it. */}
        <div className="mds-desktop-scroll">
          <div className="mds-top-actions">
            <span>{state.backendStatus === 'online' ? 'Live backend' : 'Backend offline'}</span>
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

          {/* Only show the empty-state hint before any chat happens. */}
          {!state.recommendations.length && state.messages.length === 0 && (
            <div className="mds-assistant-line">
              Recommendations will appear after your first request.
            </div>
          )}

          <RecommendationCards state={state} />

          <div className="mds-main-actions">
            <button type="button" onClick={() => state.replayLastPrompt()} disabled={!state.lastPrompt || state.isLoading}>
              Rerun across {state.phaseLabel}
            </button>
            <button type="button" onClick={() => setMemoryOpen(true)}>
              Inspect memory
            </button>
            <button
              type="button"
              onClick={() => state.clearChat()}
              disabled={state.isLoading || (state.messages.length === 0 && state.traceSpans.length === 0)}
            >
              Clear chat
            </button>
          </div>
        </div>

        {/* Sticky composer dock - always reachable while history scrolls. */}
        <div className="mds-desktop-dock">
          <ChatComposer state={state} />
        </div>
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
