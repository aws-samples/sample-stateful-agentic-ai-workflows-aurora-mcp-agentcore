import { useState } from 'react';
import { ChatComposer } from './components/ChatComposer';
import { ChatTranscript } from './components/ChatTranscript';
import { MemoryDrawer } from './components/MemoryDrawer';
import { NavPanelDrawer } from './components/NavPanelDrawer';
import type { NavPanelId } from './components/NavPanelDrawer';
import { PhaseSelector } from './components/PhaseSelector';
import { AuroraEvidenceStrip } from './components/AuroraEvidenceStrip';
import { TracePanel } from './components/TracePanel';
import { TravelerContextPanel } from './components/TravelerContextPanel';
import { TripDetailDrawer } from './components/TripDetailDrawer';
import { ComparisonDialog } from './components/ComparisonDialog';
import { JourneyPanel } from './components/JourneyPanel';
import type { MeridianShowcaseState } from './hooks/useMeridianShowcase';
import { SHOWCASE_PHASES } from './lib/showcaseAdapters';
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

function NavIcon({ id }: { id: NavItemId }) {
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
  if (id === 'preferences') {
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
  const [surfaceMode, setSurfaceMode] = useState<'experience' | 'proof'>('experience');
  const [memoryOpen, setMemoryOpen] = useState(false);
  // Let presenters free right-rail space without losing traveler context.
  const [forYouCollapsed, setForYouCollapsed] = useState(false);
  // Same affordance for traces, useful when the traveler panel is the focus.
  const [activityCollapsed, setActivityCollapsed] = useState(false);
  // Keep the trust proof available without crowding the chat surface.
  const [auroraEvidenceCollapsed, setAuroraEvidenceCollapsed] = useState(true);
  // Null means the default Concierge surface is active.
  const [navPanel, setNavPanel] = useState<NavPanelId | null>(null);

  // Time-of-day greeting keeps the demo personal without storing state.
  //   05:00–11:59 → morning
  //   12:00–16:59 → afternoon
  //   17:00–04:59 → evening
  const greetingHour = new Date().getHours();
  const greetingPart =
    greetingHour >= 5 && greetingHour < 12
      ? 'morning'
      : greetingHour >= 12 && greetingHour < 17
        ? 'afternoon'
        : 'evening';

  return (
    <div className={`mds-desktop-app is-${surfaceMode}`}>
      <aside className="mds-desktop-sidebar">
        <div className="mds-brand">
          <BrandMark />
          Meridian
        </div>
        <nav className="mds-nav-items" aria-label="Desktop navigation">
          {navItems.map((item) => {
            // Keep sidebar selection aligned with drawers as well as pages.
            const isActive =
              (item.id === 'concierge' && navPanel === null && !memoryOpen) ||
              (item.id === 'preferences' && memoryOpen) ||
              navPanel === (item.id as NavPanelId);
            return (
              <button
                key={item.id}
                type="button"
                className={`mds-nav-item${isActive ? ' is-active' : ''}`}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => {
                  if (item.id === 'concierge') {
                    setNavPanel(null);
                    setMemoryOpen(false);
                  } else if (item.id === 'preferences') {
                    setNavPanel(null);
                    setMemoryOpen(true);
                  } else {
                    setMemoryOpen(false);
                    setNavPanel(item.id as NavPanelId);
                  }
                }}
              >
                <span className="mds-nav-icon" aria-hidden="true">
                  <NavIcon id={item.id} />
                </span>
                {item.label}
                {item.id === 'messages' && state.messages.length > 0 && (
                  <b>{state.messages.length}</b>
                )}
              </button>
            );
          })}
        </nav>
        <div className="mds-sidebar-spacer" />
        <div className="mds-account-mini">
          <span className="mds-avatar is-photo" aria-hidden="true">
            <img src={ALEX_IMAGE_URL} alt={ALEX_NAME} loading="lazy" />
          </span>
          <div className="mds-account-copy">
            <strong>Alex Morgan</strong>
            <span>Bonvoy Platinum Elite</span>
          </div>
        </div>
      </aside>

      <main className="mds-desktop-main">
        {/* One scroll surface keeps history and inline results moving together. */}
        <div className="mds-desktop-scroll">
          <div className="mds-top-actions">
            {/* Breadcrumb orients the current surface. */}
            <nav className="mds-breadcrumb" aria-label="Breadcrumb">
              <span>Concierge</span>
              <span className="mds-breadcrumb-sep" aria-hidden="true">/</span>
              <span className="mds-breadcrumb-current">Recommendations</span>
            </nav>
            {/* Live status: backend reachability plus currency context. */}
            <span
              className={`mds-status-pill${state.backendStatus === 'online' ? ' is-live' : ' is-off'}`}
            >
              <span className="mds-status-dot" aria-hidden="true" />
              {state.backendStatus === 'online' ? 'Reasoning live' : 'Backend offline'}
              <span className="mds-status-sep" aria-hidden="true">·</span>
              <span className="mds-status-unit">USD</span>
            </span>
          </div>
          <div className="mds-surface-switch" role="tablist" aria-label="Showcase view">
            <button type="button" role="tab" aria-selected={surfaceMode === 'experience'} className={surfaceMode === 'experience' ? 'is-active' : ''} onClick={() => setSurfaceMode('experience')}>Experience</button>
            <button type="button" role="tab" aria-selected={surfaceMode === 'proof'} className={surfaceMode === 'proof' ? 'is-active' : ''} onClick={() => { setSurfaceMode('proof'); setAuroraEvidenceCollapsed(false); }}>System proof</button>
          </div>
          <div className="mds-headline-row">
            <div>
              <h1>{`Good ${greetingPart}, Alex.`}</h1>
              <p>Where would you like to go next?</p>
            </div>
            {surfaceMode === 'proof' && <PhaseSelector state={state} />}
          </div>

          {surfaceMode === 'proof' && <div className="mds-capability-ladder" aria-label="Five phase capability ladder">
            {SHOWCASE_PHASES.map((phase) => (
              <div
                key={phase.label}
                className={`mds-capability-step${state.selectedPhase === phase.phase ? ' is-active' : ''}`}
              >
                <span>{phase.label}</span>
                <b>{phase.capability}</b>
                <small>{phase.proofPoint}</small>
              </div>
            ))}
          </div>}

          {surfaceMode === 'proof' && <AuroraEvidenceStrip
            state={state}
            collapsed={auroraEvidenceCollapsed}
            onToggleCollapsed={() => setAuroraEvidenceCollapsed((prev) => !prev)}
          />}

          {/* Phase callout names the new capability added at this rung. */}
          {state.phaseHint && (
            <div className="mds-phase-hint" role="status" aria-live="polite">
              <span className="mds-phase-hint-badge">{state.phaseHint.label}</span>
              <span className="mds-phase-hint-copy">{state.phaseHint.adds}</span>
              {state.phaseHint.tech && (
                <span className="mds-phase-hint-tech">{state.phaseHint.tech}</span>
              )}
              <button
                type="button"
                className="mds-phase-hint-dismiss"
                onClick={state.dismissPhaseHint}
                aria-label="Dismiss"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>
          )}

          {state.error && (
            <div className="mds-error-banner" role="alert">
              <span className="mds-error-banner-copy">
                Couldn't reach the concierge. Aurora + FastAPI may be reconnecting.
              </span>
              <span className="mds-error-banner-actions">
                {state.lastPrompt && (
                  <button
                    type="button"
                    className="mds-error-retry"
                    onClick={() => {
                      state.clearError();
                      void state.replayLastPrompt();
                    }}
                    disabled={state.isLoading}
                  >
                    Retry
                  </button>
                )}
                <button type="button" className="mds-error-dismiss" onClick={state.clearError}>
                  Dismiss
                </button>
              </span>
            </div>
          )}

          <ChatTranscript state={state} />

          {/* Product cards stay attached to the bot turn that produced them. */}

          <div className="mds-main-actions">
            {surfaceMode === 'proof' && (
            <button type="button" onClick={() => state.replayLastPrompt()} disabled={!state.lastPrompt || state.isLoading}>
              Rerun across {state.phaseLabel}
            </button>
            )}
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

        {/* Sticky composer stays reachable while history scrolls. */}
        <div className="mds-desktop-dock">
          <ChatComposer state={state} />
        </div>
      </main>

      <aside className="mds-desktop-right">
        {surfaceMode === 'experience' ? <JourneyPanel state={state} /> : <>
        <TravelerContextPanel
          state={state}
          onOpenMemory={() => setMemoryOpen(true)}
          collapsed={forYouCollapsed}
          onToggleCollapsed={() => setForYouCollapsed((prev) => !prev)}
        />
        <TracePanel
          state={state}
          collapsed={activityCollapsed}
          onToggleCollapsed={() => setActivityCollapsed((prev) => !prev)}
        />
        </>}
      </aside>

      <TripDetailDrawer state={state} />
      <ComparisonDialog state={state} />
      <MemoryDrawer state={state} open={memoryOpen} onClose={() => setMemoryOpen(false)} />
      <NavPanelDrawer state={state} panel={navPanel} onClose={() => setNavPanel(null)} />
      {state.workspaceNotice && <div className="mds-toast" role="status">{state.workspaceNotice}</div>}
    </div>
  );
}
