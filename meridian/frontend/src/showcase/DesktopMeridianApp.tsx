import { useEffect, useRef, useState } from 'react';
import type { ComponentType } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  ChevronLeft,
  ChevronRight,
  Compass,
  Mail,
  Moon,
  ArrowRight,
  AlertTriangle,
  RefreshCw,
  PanelLeftClose,
  PanelLeftOpen,
  Settings2,
  Sun,
  UserRound,
} from 'lucide-react';
import { BoardingPass, ConciergeBell } from './icons/TravelIcons';
import { ChatComposer } from './components/ChatComposer';
import { CapabilityBrief } from './components/CapabilityBrief';
import { SURFACES, useJourney, useSurfaceUrlState } from './journey/useJourney';
import { PresenterProof } from './surfaces/PresenterProof';
import { ConciergeRail } from './surfaces/ConciergeRail';
import { JourneyContinuityRail } from './surfaces/JourneyContinuityRail';
import { ChatTranscript } from './components/ChatTranscript';
import { ComparisonDialog } from './components/ComparisonDialog';
import { DiscoveryWorkspace } from './components/DiscoveryWorkspace';
import { MemoryDrawer } from './components/MemoryDrawer';
import { NavPanelDrawer } from './components/NavPanelDrawer';
import type { NavPanelId } from './components/NavPanelDrawer';
import { RecoveryWorkspace } from './components/RecoveryWorkspace';
import { WorkflowWorkspace } from './components/WorkflowWorkspace';
import { SessionClose } from './surfaces/SessionClose';
import { SolutionBriefing } from './surfaces/SolutionBriefing';
import { TracePanel } from './components/TracePanel';
import { TravelerContextPanel } from './components/TravelerContextPanel';
import { TripDetailDrawer } from './components/TripDetailDrawer';
import { IconTooltip } from './components/ShowcaseTooltip';
import type { MeridianShowcaseState } from './hooks/useMeridianShowcase';
import type { Phase } from '../types';
import { MERIDIAN_MARK_SRC } from '../lib/meridianBrand';
import { ALEX_IMAGE_URL, ALEX_NAME } from './lib/personas';
import { prefersReducedMotion } from './lib/prefersReducedMotion';
import { SHOWCASE_PHASES } from './lib/showcaseAdapters';

type NavItemId = 'concierge' | 'trips' | 'discover' | 'profile' | 'preferences' | 'messages';
type ShowcaseTheme = 'dark' | 'light';

/** Lucide's own marks and the travel set drawn to match it share this shape.
 *  Lucide types `size` as string | number, so widen rather than narrow. */
type NavIcon = ComponentType<{
  size?: string | number;
  strokeWidth?: string | number;
}>;

const navItems: { id: NavItemId; label: string; icon: NavIcon }[] = [
  { id: 'concierge', label: 'Concierge', icon: ConciergeBell },
  { id: 'trips', label: 'Trips', icon: BoardingPass },
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
  const { view, journeyId, setView: writeView, setJourneyId } = useSurfaceUrlState();
  const [closing, setClosing] = useState(false);
  const setView = (next: typeof view) => { setClosing(false); writeView(next); };
  const isRecoveryView = view === 'recovery';
  const journey = useJourney(
    journeyId,
    setJourneyId,
    view === 'proof' || isRecoveryView || (view === 'ladder' && state.selectedPhase === 5),
    state.selectedPhase === 5 ? state.conversationId : null,
  );
  const refreshJourney = journey.refresh;
  const latestWorkflowRead = useRef<string | null>(null);
  useEffect(() => {
    if (state.selectedPhase !== 5 || !state.conversationId || state.isLoading) return;
    const receipt = `${state.conversationId}:${state.workflowStatus}`;
    if (latestWorkflowRead.current !== receipt) {
      latestWorkflowRead.current = receipt;
      refreshJourney();
    }
  }, [state.selectedPhase, state.conversationId, state.workflowStatus, state.isLoading, refreshJourney]);
  const restoredJourney = useRef<string | null>(null);
  useEffect(() => {
    if (!isRecoveryView || !journey.document || restoredJourney.current === journey.document.journey_id) return;
    if (!state.isLoading && !state.conversationId && !state.messages.length) {
      restoredJourney.current = journey.document.journey_id;
      state.restoreJourney(journey.document);
    }
  }, [isRecoveryView, journey.document, state]);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [forYouCollapsed, setForYouCollapsed] = useState(false);
  const [activityCollapsed, setActivityCollapsed] = useState(false);
  // Collapsed by default: the nav is product chrome, and the 136px it gives
  // back goes to the transcript and result cards, which is what a room reads.
  // Presenters can expand it to show the surrounding product.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() =>
    typeof window === 'undefined' || !window.matchMedia?.('(min-width: 1440px)').matches,
  );
  // The evidence rail is the proof surface, not the story. Folding it away
  // hands its ~400px to the transcript, which is what the room is reading
  // while a phase runs. Open by default: the ladder's whole argument is that
  // the claims are checkable, so the proof should be on screen unless the
  // presenter deliberately reclaims the width.
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [navPanel, setNavPanel] = useState<NavPanelId | null>(null);
  const greetingPart = greetingForHour(new Date().getHours());
  const isProduct = !closing && view === 'concierge';
  const isLadder = !closing && view === 'ladder';
  const isProof = !closing && view === 'proof';
  const isRecovery = !closing && view === 'recovery';
  const isBriefing = !closing && view === 'briefing';
  const isWorkflow = isLadder && state.selectedPhase === 5;
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

  // The row scrolls on a narrow screen, and a surface you cannot see is a
  // surface you will not find. Keep the active one in view.
  const surfaceRowRef = useRef<HTMLOListElement | null>(null);
  const phaseRowRef = useRef<HTMLOListElement | null>(null);
  useEffect(() => {
    const revealActiveSurface = () => {
      const active = surfaceRowRef.current?.querySelector('[data-active="true"]');
      active?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
      const phase = phaseRowRef.current?.querySelector('[aria-current="step"]');
      phase?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
    };
    revealActiveSurface();
    window.addEventListener('resize', revealActiveSurface);
    return () => window.removeEventListener('resize', revealActiveSurface);
  }, [view, state.selectedPhase]);

  const openProduct = () => setView('concierge');
  const openPhase = (phase: Phase) => {
    state.setSelectedPhase(phase);
    setView('ladder');
  };
  const clearIntoLadder = () => {
    state.clearChat();
    void state.setMemoryEnabled(false);
    state.setSelectedPhase(1);
    setMemoryOpen(false);
    setNavPanel(null);
    setView('ladder');
  };

  const openNavItem = (id: NavItemId) => {
    if (id === 'concierge') {
      setNavPanel(null);
      setMemoryOpen(false);
      setView('concierge');
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
          : isProof || closing || isBriefing
            ? 'is-presenter-proof'
            : isRecovery
              ? 'is-experience is-finale'
              : 'is-proof is-ladder'
      }${isRecoveryView ? ' is-continuity-rail' : ''}${
        sidebarCollapsed ? ' is-sidebar-collapsed' : ''
      }${
        railCollapsed ? ' is-rail-collapsed' : ''
      }`}
    >
      <aside className="mds-desktop-sidebar">
        <div className="mds-sidebar-head">
          <div className="mds-brand">
            <BrandMark />
            <span className="mds-brand-name">Meridian<small>TRAVEL CONCIERGE</small></span>
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
        <div className="mc-sidebar-note"><span>Every detail.</span><span>Every step of the way.</span><button type="button" onClick={() => setView('recovery')}>Your recovery desk<ArrowRight size={14} aria-hidden="true" /></button></div>
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
              <span>Traveler profile</span>
            </span>
          </div>
          <ChevronRight className="mds-account-chevron" size={16} aria-hidden="true" />
        </button>
      </aside>

      <header className="mds-shell-header">
        <div className="mc-audience-brand"><BrandMark /><span>Meridian</span></div>
        <nav className="mds-shell-surface-nav" aria-label="Meridian capability ladder">
          <ol
            className="mds-surface-switch"
            aria-label="Meridian surfaces"
            ref={surfaceRowRef}
          >
            {SURFACES.map((surface) => {
              const active = !closing && view === surface.id;
              return (
                <li key={surface.id}>
                  <button
                    type="button"
                    className={`mds-surface-tab${active ? ' is-active' : ''}`}
                    aria-current={active ? 'page' : undefined}
                    data-active={active ? 'true' : undefined}
                    disabled={surface.id === 'ladder' && isProduct && state.isLoading}
                    onClick={() => {
                      if (surface.id === 'concierge') openProduct();
                      else if (surface.id === 'ladder' && isProduct) clearIntoLadder();
                      else setView(surface.id);
                    }}
                    title={surface.blurb}
                  >
                    <strong>{surface.label}</strong>
                    <small>{surface.blurb}</small>
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>

        <div className="mds-shell-status">
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
      </header>

      <main className="mds-desktop-main">
        {state.connectionIssue && <div className="mc-connection-notice" role="status">
          <AlertTriangle size={20} aria-hidden="true" />
          <div><strong>{state.connectionIssue}</strong><p>Displayed trips may be a preview or the last loaded results. Reconnect before planning.</p></div>
          <button type="button" disabled={state.connectionRefreshing} onClick={() => void state.refreshConnection()}>
            <RefreshCw size={16} aria-hidden="true" />{state.connectionRefreshing ? 'Reconnecting…' : 'Reconnect'}
          </button>
        </div>}
        <div className="mds-desktop-scroll" tabIndex={0} role="region" aria-label="Travel workspace">
          {isLadder && (
          <nav className="mds-ladder-nav" aria-label="Capability ladder phases">
            <ol className="mds-ladder-nav-rungs" ref={phaseRowRef}>
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
          )}

          {/* Switching rungs clears the transcript, spans and results, so
              without a transition the whole column blinks out and back. Cross
              fade the swap instead: out fast, in a touch slower, keyed on the
              view so React unmounts cleanly between phases. */}
          <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={
              closing
                ? 'close'
                : isProof
                ? 'proof'
                : isBriefing
                  ? 'briefing'
                : isProduct
                  ? 'product'
                  : isRecovery
                    ? 'recovery'
                    : `phase-${state.selectedPhase}`
            }
            className="mds-view-swap"
            initial={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: 6 }}
            animate={prefersReducedMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
            exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: -4 }}
            transition={
              prefersReducedMotion
                ? { duration: 0.12 }
                : { duration: 0.26, ease: [0.22, 0.61, 0.36, 1] }
            }
          >
          {isLadder && <CapabilityBrief phase={state.selectedPhase} />}
          {closing ? <SessionClose onEvidence={() => setView('proof')} onConcierge={openProduct} /> : isBriefing ? (
            <SolutionBriefing onOpenLadder={() => setView('ladder')} />
          ) : isProof ? (
            <>
            <PresenterProof
              document={journey.document}
              loading={journey.loading}
              error={journey.error}
              onRefresh={journey.refresh}
              onOpenRecovery={() => setView('recovery')}
            />
            <footer className="mc-session-handoff">
              <div><h2>Bring it back to the traveler.</h2><p>How Aurora and AgentCore turn saved context into a clear next step.</p></div>
              <button type="button" className="mc-session-primary" onClick={() => setClosing(true)}>Session takeaways <ArrowRight size={18} aria-hidden="true" /></button>
            </footer>
            </>
          ) : isProduct ? (
            <DiscoveryWorkspace
              state={state}
              greeting={greetingPart}
              onClear={clearIntoLadder}
              onDiscover={() => openNavItem('discover')}
            />
          ) : isWorkflow ? <WorkflowWorkspace state={state} onOpenRecovery={() => setView('recovery')} /> : !isRecovery ? (
            <>
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

              {(state.messages.length > 0 || state.isLoading) && <ChatTranscript state={state} proofMode />}

              {(state.lastPrompt || state.messages.length > 0 || state.traceSpans.length > 0) && <div className="mds-main-actions">
                <button
                  type="button"
                  onClick={() => void state.replayLastPrompt()}
                  disabled={!state.lastPrompt || state.isLoading}
                >
                  Rerun last prompt
                </button>
                <button
                  type="button"
                  onClick={state.clearChat}
                  disabled={state.isLoading || (state.messages.length === 0 && state.traceSpans.length === 0)}
                >
                  Clear chat
                </button>
              </div>}
            </>
          ) : (
            <RecoveryWorkspace
              state={state}
              journeyDocument={journey.document}
              onOpenProof={() => { journey.refresh(); setView('proof'); }}
              showHeading={!isLadder}
            />
          )}
          </motion.div>
          </AnimatePresence>
        </div>

        {/* Concierge and the ladder share one dock. Moving between them should
            change what is on screen, not where the screen's controls are. */}
        {(isProduct || (isLadder && !isWorkflow)) && (
          <div className="mds-desktop-dock">
            {isProduct ? (
              <ChatComposer state={state} conciergeMode />
            ) : (
              <ChatComposer state={state} proofMode />
            )}
          </div>
        )}
      </main>

      {isProduct && (
        <aside className="mds-desktop-right is-concierge" aria-label="Your trip brief">
          <ConciergeRail state={state} onSaved={() => openNavItem('trips')} onRecovery={() => setView('recovery')} />
        </aside>
      )}

      {isRecovery && (
        <aside
          className="mds-desktop-right is-continuity"
          aria-label="Journey continuity"
        >
          <JourneyContinuityRail document={journey.document} error={journey.error} />
        </aside>
      )}

      {isLadder && (
        <aside
          className={`mds-desktop-right${railCollapsed ? ' is-collapsed' : ''}`}
          aria-label="System proof"
        >
          {/* The drawer's only control, on its edge. The label shows just
              when shut - open, the panel below carries its own "Aurora
              evidence" header and repeating it printed the title twice. */}
          <button
            type="button"
            className="mds-rail-handle"
            onClick={() => setRailCollapsed((collapsed) => !collapsed)}
            aria-expanded={!railCollapsed}
            aria-label={
              railCollapsed
                ? 'Open the Aurora evidence drawer'
                : 'Close the Aurora evidence drawer'
            }
          >
            <ChevronLeft size={15} strokeWidth={2.6} aria-hidden="true" />
            {railCollapsed && <span>Aurora evidence</span>}
          </button>
          {!railCollapsed && (
          <>
            {/* Two surfaces, read top to bottom: the traveler state this turn
                ran against, then the span-level detail behind it. The stage
                strip that used to sit above them tried to fit seven stages and
                their values into the rail's width and never became legible. */}
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
          )}
        </aside>
      )}

      <TripDetailDrawer state={state} />
      <ComparisonDialog state={state} />
      <MemoryDrawer state={state} open={memoryOpen} onClose={() => setMemoryOpen(false)} />
      <NavPanelDrawer state={state} travelerMode={isProduct} panel={navPanel} onClose={() => setNavPanel(null)} />
      {state.workspaceNotice && (
        <div className="mds-toast" role="status">{state.workspaceNotice}</div>
      )}
    </div>
  );
}
