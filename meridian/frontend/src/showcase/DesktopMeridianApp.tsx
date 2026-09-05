import { useEffect, useState } from 'react';
import {
  Briefcase,
  Compass,
  Mail,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Settings2,
  Sparkles,
  Sun,
  UserRound,
  X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { AuroraEvidenceStrip } from './components/AuroraEvidenceStrip';
import { ChatComposer } from './components/ChatComposer';
import { ChatTranscript } from './components/ChatTranscript';
import { ComparisonDialog } from './components/ComparisonDialog';
import { DiscoveryWorkspace } from './components/DiscoveryWorkspace';
import { MemoryDrawer } from './components/MemoryDrawer';
import { NavPanelDrawer } from './components/NavPanelDrawer';
import type { NavPanelId } from './components/NavPanelDrawer';
import { RecoveryWorkspace } from './components/RecoveryWorkspace';
import { TracePanel } from './components/TracePanel';
import { TravelerContextPanel } from './components/TravelerContextPanel';
import { TripDetailDrawer } from './components/TripDetailDrawer';
import { IconTooltip } from './components/ShowcaseTooltip';
import type { MeridianShowcaseState } from './hooks/useMeridianShowcase';
import type { Phase } from '../types';
import { MERIDIAN_MARK_SRC } from '../lib/meridianBrand';
import { ALEX_IMAGE_URL, ALEX_NAME } from './lib/personas';
import { deriveRecoveryStage } from './lib/recoveryState';
import { SHOWCASE_PHASES } from './lib/showcaseAdapters';

type NavItemId = 'concierge' | 'trips' | 'discover' | 'profile' | 'preferences' | 'messages';
type ShowcaseTheme = 'dark' | 'light';
/**
 * Two views, not three steps.
 *
 * The five-phase ladder is the argument, so it is the top level. `product` is
 * the un-numbered cold open (and the close): the Meridian experience the
 * ladder builds toward. Numbering the product view as "step 1 of 3" made the
 * ladder look like a third of the story and forced the room to hold two
 * mental models at once.
 */
type ShowcaseView = 'product' | 'ladder';

const navItems: { id: NavItemId; label: string; icon: LucideIcon }[] = [
  { id: 'concierge', label: 'Concierge', icon: Sparkles },
  { id: 'trips', label: 'Trips', icon: Briefcase },
  { id: 'discover', label: 'Discover', icon: Compass },
  { id: 'profile', label: 'Profile', icon: UserRound },
  { id: 'preferences', label: 'Preferences', icon: Settings2 },
  { id: 'messages', label: 'Messages', icon: Mail },
];

function BrandMark() {
  return (
    <img
      className="mds-brand-mark"
      src={MERIDIAN_MARK_SRC}
      alt=""
      width="36"
      height="36"
      loading="eager"
      decoding="async"
    />
  );
}

function greetingForHour(hour: number): string {
  if (hour >= 5 && hour < 12) return 'morning';
  if (hour >= 12 && hour < 17) return 'afternoon';
  return 'evening';
}

export function DesktopMeridianApp({
  state,
  theme,
  onToggleTheme,
}: {
  state: MeridianShowcaseState;
  theme: ShowcaseTheme;
  onToggleTheme: () => void;
}) {
  const [view, setView] = useState<ShowcaseView>('product');
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [forYouCollapsed, setForYouCollapsed] = useState(false);
  const [activityCollapsed, setActivityCollapsed] = useState(false);
  // Collapsed by default. The header still reports "N of 7 stages observed
  // this turn", so the signal survives while the chip row's height goes to the
  // transcript. Expand it for the proof beat.
  const [auroraEvidenceCollapsed, setAuroraEvidenceCollapsed] = useState(true);
  // Collapsed by default: the nav is product chrome, and the 136px it gives
  // back goes to the transcript and result cards, which is what a room reads.
  // Presenters can expand it to show the surrounding product.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true);
  const [navPanel, setNavPanel] = useState<NavPanelId | null>(null);
  const greetingPart = greetingForHour(new Date().getHours());
  const isProduct = view === 'product';
  const isLadder = view === 'ladder';
  // Phase 5 is the durable-workflow rung, and the flight-disruption replan is
  // what that rung means. Opening it on the recovery workspace lets the change
  // of surface carry the change of phase.
  const isRecovery = isLadder && state.selectedPhase === 5;
  const activePhase = SHOWCASE_PHASES.find((p) => p.phase === state.selectedPhase);
  const recoveryStage = deriveRecoveryStage(state);
  const runtimeStatus =
    state.backendStatus === 'online'
      ? {
          className: 'is-live',
          label: 'Meridian live',
          detail: 'USD',
        }
      : state.backendStatus === 'offline'
        ? {
            className: 'is-off',
            label: 'Meridian offline',
            detail: 'Live data unavailable',
          }
        : {
            className: 'is-checking',
            label: 'Connecting to Meridian',
            detail: 'Live data pending',
          };

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;

    // Narrow viewports force the rail closed, but going wide again must not
    // re-open it — that used to discard a presenter's manual choice every time
    // the window crossed the breakpoint.
    const compactSidebar = window.matchMedia('(max-width: 1180px)');
    const collapseWhenNarrow = () => {
      if (compactSidebar.matches) setSidebarCollapsed(true);
    };

    collapseWhenNarrow();
    compactSidebar.addEventListener('change', collapseWhenNarrow);
    return () => compactSidebar.removeEventListener('change', collapseWhenNarrow);
  }, []);

  const openProduct = () => setView('product');
  const openPhase = (phase: Phase) => {
    state.setSelectedPhase(phase);
    setView('ladder');
  };
  const clearIntoLadder = () => {
    state.clearChat();
    state.setSelectedPhase(1);
    setMemoryOpen(false);
    setNavPanel(null);
    setView('ladder');
  };

  const openNavItem = (id: NavItemId) => {
    if (id === 'concierge') {
      setNavPanel(null);
      setMemoryOpen(false);
      return;
    }
    if (id === 'preferences') {
      setNavPanel(null);
      setMemoryOpen(true);
      return;
    }
    setMemoryOpen(false);
    setNavPanel(id as NavPanelId);
  };

  return (
    <div
      className={`mds-desktop-app is-projector ${
        isProduct
          ? 'is-discovery'
          : isRecovery
            ? 'is-experience is-finale'
            : 'is-proof is-ladder'
      }${sidebarCollapsed ? ' is-sidebar-collapsed' : ''}`}
    >
      <aside className="mds-desktop-sidebar">
        <div className="mds-sidebar-head">
          <div className="mds-brand">
            <BrandMark />
            <span className="mds-brand-name">Meridian</span>
          </div>
          <IconTooltip label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
            <button
              type="button"
              className="mds-sidebar-toggle"
              onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
              aria-label={sidebarCollapsed ? 'Expand navigation sidebar' : 'Collapse navigation sidebar'}
              aria-expanded={!sidebarCollapsed}
            >
              {sidebarCollapsed
                ? <PanelLeftOpen size={18} aria-hidden="true" />
                : <PanelLeftClose size={18} aria-hidden="true" />}
            </button>
          </IconTooltip>
        </div>
        <nav className="mds-nav-items" aria-label="Desktop navigation">
          {navItems.map((item) => {
            const isActive =
              (item.id === 'concierge' && navPanel === null && !memoryOpen) ||
              (item.id === 'preferences' && memoryOpen) ||
              navPanel === (item.id as NavPanelId);
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className={`mds-nav-item${isActive ? ' is-active' : ''}`}
                aria-current={isActive ? 'page' : undefined}
                aria-label={item.label}
                title={sidebarCollapsed ? item.label : undefined}
                onClick={() => openNavItem(item.id)}
              >
                <span className="mds-nav-icon" aria-hidden="true">
                  <Icon size={18} strokeWidth={1.8} />
                </span>
                <span className="mds-nav-label">{item.label}</span>
                {item.id === 'messages' && state.messages.length > 0 && (
                  <b>{state.messages.length}</b>
                )}
              </button>
            );
          })}
        </nav>
        <div className="mds-sidebar-spacer" />
        <button
          type="button"
          className="mds-account-mini"
          onClick={() => openNavItem('profile')}
          aria-label="Open Alex Morgan profile"
        >
          <span className="mds-avatar is-photo" aria-hidden="true">
            <img
              src={ALEX_IMAGE_URL}
              alt={ALEX_NAME}
              width="640"
              height="960"
              loading="lazy"
            />
          </span>
          <div className="mds-account-copy">
            <strong>Alex Morgan</strong>
            <span className="mds-account-loyalty">
              <span>Hotel Platinum</span>
              <span>Airline Premier</span>
            </span>
          </div>
          <span className="mds-account-chevron" aria-hidden="true">›</span>
        </button>
      </aside>

      <main className="mds-desktop-main">
        <div className="mds-desktop-scroll">
          <div className="mds-top-actions">
            <div className="mds-top-status">
              <span
                className={`mds-status-pill ${runtimeStatus.className}`}
                role="status"
                aria-live="polite"
                aria-atomic="true"
              >
                <span className="mds-status-dot" aria-hidden="true" />
                {runtimeStatus.label}
                <span className="mds-status-sep" aria-hidden="true">·</span>
                <span className="mds-status-unit">{runtimeStatus.detail}</span>
              </span>
              <IconTooltip label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
                <button
                  type="button"
                  className="mds-theme-toggle"
                  onClick={onToggleTheme}
                  aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                >
                  {theme === 'dark'
                    ? <Sun size={17} aria-hidden="true" />
                    : <Moon size={17} aria-hidden="true" />}
                </button>
              </IconTooltip>
            </div>
          </div>

          <nav className="mds-ladder-nav" aria-label="Meridian capability ladder">
            <button
              type="button"
              className={`mds-ladder-nav-product${isProduct ? ' is-active' : ''}`}
              aria-current={isProduct ? 'page' : undefined}
              onClick={openProduct}
              title="The Meridian experience the ladder builds toward"
            >
              Product
            </button>
            <span className="mds-ladder-nav-divider" aria-hidden="true" />
            <ol className="mds-ladder-nav-rungs">
              {SHOWCASE_PHASES.map((phase) => {
                const active = isLadder && state.selectedPhase === phase.phase;
                // Rungs below the current one stay lit: each phase adds to the
                // stack rather than replacing it, and the nav should say so.
                const carried = isLadder && state.selectedPhase > phase.phase;
                return (
                  <li key={phase.phase}>
                    <button
                      type="button"
                      className={`mds-ladder-nav-rung${active ? ' is-active' : ''}${
                        carried ? ' is-carried' : ''
                      }`}
                      aria-current={active ? 'step' : undefined}
                      onClick={() => openPhase(phase.phase)}
                      title={`${phase.description}. ${phase.proofPoint}.`}
                      aria-label={`Phase ${phase.phase}, ${phase.label}: ${phase.description}. ${phase.proofPoint}.`}
                    >
                      <span className="mds-ladder-nav-index" aria-hidden="true">
                        {phase.phase}
                      </span>
                      <span className="mds-ladder-nav-copy" aria-hidden="true">
                        <strong>{phase.label}</strong>
                        <small>{phase.capability}</small>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </nav>

          {isProduct ? (
            <DiscoveryWorkspace
              state={state}
              greeting={greetingPart}
              onClear={clearIntoLadder}
            />
          ) : !isRecovery ? (
            <>
              <div className="mds-headline-row mds-ladder-headline">
                <div>
                  <h1>{activePhase ? `${activePhase.label} · ${activePhase.capability}` : 'Capability ladder'}</h1>
                  <p>{activePhase?.description ?? 'SQL → MCP → Retrieval → Production → Durable workflow'}</p>
                </div>
              </div>

              <AuroraEvidenceStrip
                state={state}
                collapsed={auroraEvidenceCollapsed}
                onToggleCollapsed={() => setAuroraEvidenceCollapsed((prev) => !prev)}
              />

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
                    <X size={13} />
                  </button>
                </div>
              )}

              {state.error && (
                <div className="mds-error-banner" role="alert">
                  <span className="mds-error-banner-copy">
                    Meridian could not reach the live concierge.
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
                    <button
                      type="button"
                      className="mds-error-dismiss"
                      onClick={state.clearError}
                    >
                      Dismiss
                    </button>
                  </span>
                </div>
              )}

              <ChatTranscript state={state} proofMode />

              <div className="mds-main-actions">
                <button
                  type="button"
                  onClick={() => void state.replayLastPrompt()}
                  disabled={!state.lastPrompt || state.isLoading}
                >
                  Rerun across {state.phaseLabel}
                </button>
                <button type="button" onClick={() => setMemoryOpen(true)}>
                  Inspect memory
                </button>
                <button
                  type="button"
                  onClick={state.clearChat}
                  disabled={state.isLoading || (state.messages.length === 0 && state.traceSpans.length === 0)}
                >
                  Clear chat
                </button>
              </div>
            </>
          ) : (
            <RecoveryWorkspace
              state={state}
              onOpenProof={() => setActivityCollapsed(false)}
              showComposer={false}
            />
          )}
        </div>

        {(isLadder && (!isRecovery || recoveryStage === 'ready')) && (
          <div className="mds-desktop-dock">
            {!isRecovery ? (
              <ChatComposer state={state} proofMode />
            ) : (
              <ChatComposer state={state} recoveryMode />
            )}
          </div>
        )}
      </main>

      {isLadder && (
        <aside className="mds-desktop-right">
          <>
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
          </>
        </aside>
      )}

      <TripDetailDrawer state={state} />
      <ComparisonDialog state={state} />
      <MemoryDrawer state={state} open={memoryOpen} onClose={() => setMemoryOpen(false)} />
      <NavPanelDrawer state={state} panel={navPanel} onClose={() => setNavPanel(null)} />
      {state.workspaceNotice && (
        <div className="mds-toast" role="status">{state.workspaceNotice}</div>
      )}
    </div>
  );
}
