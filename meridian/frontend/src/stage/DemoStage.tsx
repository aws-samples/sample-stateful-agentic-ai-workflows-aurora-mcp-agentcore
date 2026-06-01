/**
 * Meridian Demo Stage — cinematic, 16:9-friendly keynote surface.
 *
 * Routes:
 *   /demo-stage             → standard presenter mode
 *   /demo-stage?kiosk=1     → kiosk loop (3 scenarios on rotation, no chrome)
 *   /demo-stage?view=builder→ start in builder view (more technical labels)
 *
 * Keyboard:
 *   Space        play / pause
 *   ArrowRight   step to next span
 *   ArrowLeft    step to previous span
 *   R            replay current trace
 *   B            toggle audience / builder
 *
 * Backend:
 *   Requires POST /api/chat with the current scenario prompt. Trace spans,
 *   recommendations, and assistant replies come from the live response only.
 *
 * Launch:
 *   npm run dev
 *   open http://localhost:5173/demo-stage
 *   open http://localhost:5173/demo-stage?kiosk=1
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './demo-stage.css';
import { StageTopBar } from './components/StageTopBar';
import { TravelerIntentCard } from './components/TravelerIntentCard';
import { TraceHero } from './components/TraceHero';
import { SystemProofRail } from './components/SystemProofRail';
import { PresenterControls } from './components/PresenterControls';
import { SpanInspector } from './components/SpanInspector';
import { useStagePlayer } from './hooks/useStagePlayer';
import {
  DEFAULT_SCENARIO_ID,
  STAGE_SCENARIOS,
  getStageScenarioById,
} from './data/stageScenarios';
import { adaptChatResponseToScenario, sumLatency } from './utils/traceAdapter';
import { sendChatMessage } from '../api/client';
import type { StageScenario, StageSpan, StageSystemId, StageView } from './types';
import type { Phase } from '../types';

const KIOSK_SCENARIO_ORDER: StageScenario['id'][] = ['tokyo', 'recall', 'plan'];
const KIOSK_DWELL_MS = 6500;
const KIOSK_GITHUB_REPO = 'https://github.com/aws-samples/sample-dat309-agentic-workflows-aurora-mcp';
const ARCHITECTURE_IMAGE_SRC = '/kiosk/architecture.png';
const TRY_QR_IMAGE_SRC = '/kiosk/try-meridian-qr.png';
type KioskTab = 'demo' | 'architecture' | 'try';

// Chalk-talk session — shown on the "Try it live" pane to drive folks to
// the deeper session. Keep in one place so the date/room is easy to edit.
const CHALK_TALK = {
  code: 'DAT301-R',
  title: 'Build agentic workflows with Aurora and MCP',
  time: '4:15 – 5:15 PM · Chalk talk',
  room: 'Room 716A',
  speakers: 'Shayon Sanyal & Aditya Samant',
} as const;

// The three Meridian surfaces, so booth visitors can jump straight to any.
const MERIDIAN_SURFACES: { label: string; path: string; blurb: string }[] = [
  { label: 'Showcase', path: '/showcase', blurb: 'Full concierge — chat, trace, memory' },
  { label: 'Pro', path: '/', blurb: 'Chalk-talk deep dive · five modes' },
  { label: 'Kiosk', path: '/demo-stage?kiosk=1', blurb: 'This auto-playing booth' },
];

function readUrlFlags() {
  if (typeof window === 'undefined') return { kiosk: false, view: 'audience' as StageView, phase: 4 as Phase };
  const sp = new URLSearchParams(window.location.search);
  const kiosk = sp.get('kiosk') === '1' || sp.get('kiosk') === 'true';
  const view = (sp.get('view') === 'builder' ? 'builder' : 'audience') as StageView;
  const phaseRaw = Number.parseInt(sp.get('phase') ?? '4', 10);
  const phase = ([1, 2, 3, 4, 5].includes(phaseRaw) ? phaseRaw : 4) as Phase;
  return { kiosk, view, phase };
}

export function DemoStage() {
  const flags = useMemo(readUrlFlags, []);
  const [scenarioId, setScenarioId] = useState<StageScenario['id']>(DEFAULT_SCENARIO_ID);
  const [scenarioData, setScenarioData] = useState<StageScenario>(() => getStageScenarioById(DEFAULT_SCENARIO_ID));
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<StageView>(flags.view);
  const [selectedSpanIdx, setSelectedSpanIdx] = useState<number | null>(null);
  const [kiosk] = useState(flags.kiosk);
  // True once the reply typewriter finishes — gates the product deck so
  // cards fan in right after the stream lands (not before).
  const [replyStreamDone, setReplyStreamDone] = useState(false);
  // Collapses the center trace panel after completion so the reply +
  // product cards rise into view without scrolling. User can re-expand.
  const [traceCollapsed, setTraceCollapsed] = useState(false);
  // Bumped to re-trigger the scenario-load effect. In kiosk mode a failed
  // fetch schedules an auto-retry that increments this, so an unattended
  // booth quietly reconnects instead of parking on an error string.
  const [retryTick, setRetryTick] = useState(0);
  const [activeTab, setActiveTab] = useState<KioskTab>('demo');
  const [architectureMissing, setArchitectureMissing] = useState(false);
  const [qrMissing, setQrMissing] = useState(false);
  const phaseRef = useRef<Phase>(flags.phase);

  // Session cache of adapted scenarios, keyed by scenarioId. The kiosk
  // loops the same 3 scenarios forever, so after the first pass every
  // turn serves instantly from here — no recurring "Loading live trace…"
  // dead air. Still 100% live data; we just fetch each scenario once and
  // prefetch the NEXT one while the current plays.
  const scenarioCache = useRef<Map<StageScenario['id'], StageScenario>>(new Map());
  const inFlight = useRef<Set<StageScenario['id']>>(new Set());

  const totalLatency = useMemo(() => sumLatency(scenarioData.spans), [scenarioData]);

  // Player drives the trace animation.
  const {
    activeIndex,
    isPlaying,
    isComplete,
    play,
    pause,
    toggle,
    next,
    prev,
    replay,
  } = useStagePlayer({
    spans: scenarioData.spans,
    autoPlay: true,
    totalDurationMs: kiosk ? 5200 : 4400,
  });

  const activeSpan: StageSpan | null = activeIndex >= 0 ? scenarioData.spans[activeIndex] : null;
  const activeSystem: StageSystemId | null = activeSpan?.system ?? null;
  const inspectorSpan = selectedSpanIdx != null ? scenarioData.spans[selectedSpanIdx] ?? null : null;

  // The Concierge reply card walks through three phases that mirror the trace
  // player: pending (model span not reached) → composing (model span is the
  // active row) → composed (model span has passed, or trace finished).
  const modelSpanIdx = useMemo(
    () => scenarioData.spans.findIndex((s) => s.kind === 'model'),
    [scenarioData.spans],
  );
  const replyPhase: 'pending' | 'composing' | 'composed' = (() => {
    // Once the trace player finishes, the reply is fully composed — this
    // also covers the common case where the model span is the LAST span,
    // so activeIndex sits AT modelSpanIdx and never exceeds it (which
    // otherwise leaves the card stuck in "composing" and hides the cards).
    if (isComplete) return 'composed';
    if (modelSpanIdx === -1) {
      return activeIndex >= scenarioData.spans.length - 1 ? 'composed' : 'pending';
    }
    if (activeIndex < modelSpanIdx) return 'pending';
    if (activeIndex === modelSpanIdx) return 'composing';
    return 'composed';
  })();
  const primaryRecommendation =
    scenarioData.recommendations.find((r) => r.primary) ?? scenarioData.recommendations[0] ?? null;

  // Fetch + adapt one scenario, populating the session cache. Dedupes
  // concurrent requests for the same id (via inFlight) so a preload and a
  // direct view don't double-fetch. Returns the adapted scenario or throws.
  const fetchScenario = useCallback(async (id: StageScenario['id']): Promise<StageScenario> => {
    const cached = scenarioCache.current.get(id);
    if (cached) return cached;
    const template = getStageScenarioById(id);
    const res = await sendChatMessage({
      message: template.prompt,
      phase: phaseRef.current,
      customer_id: template.traveler.id,
    });
    const merged = adaptChatResponseToScenario(res, template);
    if (!merged) {
      throw new Error('Backend returned an empty trace — check AgentCore + Aurora configuration.');
    }
    scenarioCache.current.set(id, merged);
    return merged;
  }, []);

  // Reset the per-scenario reveal flags whenever the scenario changes, so
  // a new turn re-collapses the deck and re-expands the trace for replay.
  useEffect(() => {
    setReplyStreamDone(false);
    setTraceCollapsed(false);
  }, [scenarioId, retryTick]);

  // Auto-collapse the trace once the player finishes walking the spans, so
  // the answer and product cards rise into view without scrolling. We key
  // off `isComplete` (the player's own deterministic "trace done" signal)
  // rather than the reply typewriter — the deck still waits for the stream
  // (replyStreamDone), but the fold is the trace's beat, not the reply's.
  // (Earlier this rode on replyStreamDone, which got starved on the live
  // path once the typewriter stopped firing onComplete in the pending
  // phase — so the panel stopped collapsing.) A short delay lets the
  // audience register the completed trace before it folds. The presenter
  // can re-expand with the arrow at any time.
  useEffect(() => {
    if (!isComplete) return;
    const t = window.setTimeout(() => setTraceCollapsed(true), 1100);
    return () => window.clearTimeout(t);
  }, [isComplete]);

  // Pull trace data when scenario changes. Cache-first: a revisited
  // scenario renders instantly; a fresh one shows the trace building once.
  useEffect(() => {
    const template = getStageScenarioById(scenarioId);
    const cached = scenarioCache.current.get(scenarioId);
    if (cached) {
      // Instant: serve from cache, no loading state, no dead air.
      setScenarioData(cached);
      setLoadError(null);
      setLoading(false);
      return;
    }
    setScenarioData(template);
    setLoadError(null);
    setLoading(true);
    let cancelled = false;
    (async () => {
      try {
        const merged = await fetchScenario(scenarioId);
        if (cancelled) return;
        setScenarioData(merged);
      } catch (err) {
        if (!cancelled) {
          setLoadError(
            err instanceof Error ? err.message : 'Could not reach the Meridian backend.',
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [scenarioId, retryTick, fetchScenario]);

  // Prefetch the NEXT kiosk scenario while the current one plays, so the
  // autoplay advance lands on an already-cached (instant) turn. Fire-and-
  // forget; failures are silently ignored (the direct load will surface
  // any real error when that scenario becomes active).
  useEffect(() => {
    if (!kiosk) return;
    const idx = KIOSK_SCENARIO_ORDER.indexOf(scenarioId);
    if (idx === -1) return;
    const nextId = KIOSK_SCENARIO_ORDER[(idx + 1) % KIOSK_SCENARIO_ORDER.length];
    if (scenarioCache.current.has(nextId) || inFlight.current.has(nextId)) return;
    inFlight.current.add(nextId);
    fetchScenario(nextId)
      .catch(() => {
        /* preload is best-effort; ignore */
      })
      .finally(() => {
        inFlight.current.delete(nextId);
      });
  }, [scenarioId, kiosk, fetchScenario]);

  // Kiosk reconnect loop: if a scenario fetch fails (backend blip, Aurora
  // reconnect, deploy in progress), don't strand the booth on an error.
  // Quietly retry every few seconds until it comes back. The demo pane
  // shows a calm "Reconnecting to Aurora…" state in the meantime, and the
  // Architecture / Try-it-live tabs stay fully usable with no backend.
  const reconnectTimerRef = useRef<number | null>(null);
  useEffect(() => {
    if (!kiosk || !loadError) return;
    if (reconnectTimerRef.current != null) window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = window.setTimeout(() => {
      setRetryTick((t) => t + 1);
    }, 4000);
    return () => {
      if (reconnectTimerRef.current != null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };
  }, [kiosk, loadError, retryTick]);

  // Kiosk loop: when the trace finishes, advance to the next scenario.
  const kioskTimerRef = useRef<number | null>(null);
  useEffect(() => {
    if (!kiosk || activeTab !== 'demo') return;
    if (!isComplete) return;
    if (kioskTimerRef.current != null) window.clearTimeout(kioskTimerRef.current);
    kioskTimerRef.current = window.setTimeout(() => {
      const idx = KIOSK_SCENARIO_ORDER.indexOf(scenarioId);
      const nextId = KIOSK_SCENARIO_ORDER[(idx + 1) % KIOSK_SCENARIO_ORDER.length];
      setScenarioId(nextId);
    }, KIOSK_DWELL_MS);
    return () => {
      if (kioskTimerRef.current != null) {
        window.clearTimeout(kioskTimerRef.current);
        kioskTimerRef.current = null;
      }
    };
  }, [activeTab, isComplete, kiosk, scenarioId]);

  // Keyboard shortcuts. Disabled while typing in an input (defensive, even
  // though we don't expose any here).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
        return;
      }
      switch (e.key) {
        case '1':
          if (kiosk) setActiveTab('demo');
          break;
        case '2':
          if (kiosk) setActiveTab('architecture');
          break;
        case '3':
          if (kiosk) setActiveTab('try');
          break;
        case ' ':
        case 'Spacebar':
          if (activeTab !== 'demo') break;
          e.preventDefault();
          toggle();
          break;
        case 'ArrowRight':
          if (activeTab !== 'demo') break;
          e.preventDefault();
          next();
          break;
        case 'ArrowLeft':
          if (activeTab !== 'demo') break;
          e.preventDefault();
          prev();
          break;
        case 'r':
        case 'R':
          if (activeTab !== 'demo') break;
          e.preventDefault();
          replay();
          break;
        case 'b':
        case 'B':
          if (activeTab !== 'demo') break;
          e.preventDefault();
          setView((v) => (v === 'audience' ? 'builder' : 'audience'));
          break;
        case 'Escape':
          if (selectedSpanIdx != null) setSelectedSpanIdx(null);
          break;
        default:
          break;
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeTab, kiosk, toggle, next, prev, replay, selectedSpanIdx]);

  // Title for the browser tab — small touch but presenters appreciate it.
  useEffect(() => {
    const prev = document.title;
    document.title = kiosk ? 'Meridian · Kiosk · Aurora + MCP' : 'Meridian · Demo Stage';
    return () => {
      document.title = prev;
    };
  }, [kiosk]);

  const onSelectSpan = useCallback((idx: number) => {
    if (kiosk) return; // No drawer in kiosk mode.
    setSelectedSpanIdx((cur) => (cur === idx ? null : idx));
  }, [kiosk]);

  const onChangeScenario = useCallback((id: StageScenario['id']) => {
    setScenarioId(id);
    setSelectedSpanIdx(null);
  }, []);

  useEffect(() => {
    if (activeTab !== 'demo') setSelectedSpanIdx(null);
  }, [activeTab]);

  return (
    <div className={`ds-root${kiosk ? ' is-kiosk' : ''}`} data-view={view}>
      <div className="ds-shell">
        <StageTopBar
          phaseLabel={scenarioData.phaseLabel}
          traceId={scenarioData.traceId}
        />
        {kiosk && (
          <div className="ds-kiosk-tabs" role="tablist" aria-label="Kiosk screens">
            <button
              type="button"
              className={`ds-kiosk-tab${activeTab === 'demo' ? ' is-on' : ''}`}
              onClick={() => setActiveTab('demo')}
              role="tab"
              aria-selected={activeTab === 'demo'}
            >
              Demo experience
            </button>
            <button
              type="button"
              className={`ds-kiosk-tab${activeTab === 'architecture' ? ' is-on' : ''}`}
              onClick={() => setActiveTab('architecture')}
              role="tab"
              aria-selected={activeTab === 'architecture'}
            >
              Architecture
            </button>
            <button
              type="button"
              className={`ds-kiosk-tab${activeTab === 'try' ? ' is-on' : ''}`}
              onClick={() => setActiveTab('try')}
              role="tab"
              aria-selected={activeTab === 'try'}
            >
              Try it live
            </button>
          </div>
        )}

        {activeTab === 'demo' ? (
          <>
            <main className="ds-stage">
              {loadError && kiosk && (
                // Unattended booth: never show a raw fetch error to a
                // passerby. Calm, branded "reconnecting" state; the
                // reconnect loop above retries every 4s automatically.
                <div className="ds-load-status ds-load-reconnect" aria-live="polite">
                  <span className="ds-reconnect-dot" aria-hidden="true" />
                  Reconnecting to Aurora…
                </div>
              )}
              {loadError && !kiosk && (
                <div className="ds-load-error" role="alert">
                  {loadError}
                </div>
              )}
              {loading && !loadError && (
                <div className="ds-load-status" aria-live="polite">
                  Loading live trace from backend…
                </div>
              )}
              <TravelerIntentCard
                traveler={scenarioData.traveler}
                prompt={scenarioData.prompt}
                memoryActive={activeSpan?.kind === 'memory'}
              />

              <TraceHero
                spans={scenarioData.spans}
                activeIndex={activeIndex}
                selectedIndex={selectedSpanIdx}
                totalLatencyMs={totalLatency}
                onSelect={onSelectSpan}
                view={view}
                assistantReply={scenarioData.assistantReply}
                reasoning={scenarioData.reasoning}
                replyPhase={replyPhase}
                primaryRecommendation={primaryRecommendation}
                recommendations={scenarioData.recommendations}
                showDeck={replyStreamDone}
                onReplyStreamComplete={() => setReplyStreamDone(true)}
                collapsed={traceCollapsed}
                onToggleCollapsed={() => setTraceCollapsed((c) => !c)}
              />

              <SystemProofRail
                scenario={scenarioData}
                activeSpan={activeSpan}
                activeSystem={activeSystem}
              />
            </main>

            <PresenterControls
              isPlaying={isPlaying}
              isComplete={isComplete}
              canStep={activeIndex < scenarioData.spans.length - 1}
              view={view}
              scenarios={STAGE_SCENARIOS}
              scenarioId={scenarioId}
              onScenario={onChangeScenario}
              onTogglePlay={isPlaying ? pause : play}
              onStep={next}
              onPrev={prev}
              onReplay={replay}
              onView={setView}
            />
          </>
        ) : activeTab === 'architecture' ? (
          <section className="ds-kiosk-pane">
            <div className="ds-kiosk-pane-head">
              <h2>Meridian architecture map</h2>
              <p>
                End-to-end stack: booth UX, Strands orchestration, AgentCore runtime/gateway/memory,
                and Aurora PostgreSQL + pgvector retrieval.
              </p>
            </div>
            <div className="ds-kiosk-architecture">
              <img
                src={ARCHITECTURE_IMAGE_SRC}
                alt="Meridian architecture diagram"
                className="ds-kiosk-architecture-img"
                onLoad={() => setArchitectureMissing(false)}
                onError={() => setArchitectureMissing(true)}
              />
              {architectureMissing && (
                <div className="ds-kiosk-missing">
                  <b>Add your architecture board image</b>
                  <span>
                    Drop it at <code>meridian/frontend/public/kiosk/architecture-board.png</code>.
                    PNG, JPG, or WEBP all work (just keep the filename aligned).
                  </span>
                </div>
              )}
            </div>
          </section>
        ) : (
          <section className="ds-kiosk-pane">
            <div className="ds-kiosk-pane-head">
              <h2>Try Meridian yourself</h2>
              <p>Scan for the repo, explore the three surfaces, or join our chalk talk.</p>
            </div>

            {/* Chalk-talk invite — the deeper session this booth previews. */}
            <a
              className="ds-kiosk-session"
              href={KIOSK_GITHUB_REPO}
              target="_blank"
              rel="noreferrer"
            >
              <div className="ds-kiosk-session-badge">{CHALK_TALK.code}</div>
              <div className="ds-kiosk-session-body">
                <div className="ds-kiosk-session-title">{CHALK_TALK.title}</div>
                <div className="ds-kiosk-session-meta">
                  <span>{CHALK_TALK.time}</span>
                  <span>·</span>
                  <span>{CHALK_TALK.room}</span>
                </div>
                <div className="ds-kiosk-session-speakers">{CHALK_TALK.speakers}</div>
              </div>
              <div className="ds-kiosk-session-cta">Join us →</div>
            </a>

            {/* Jump to any of the three Meridian surfaces. */}
            <div className="ds-kiosk-surfaces">
              {MERIDIAN_SURFACES.map((s) => (
                <a key={s.path} className="ds-kiosk-surface" href={s.path}>
                  <div className="ds-kiosk-surface-label">{s.label}</div>
                  <div className="ds-kiosk-surface-blurb">{s.blurb}</div>
                  <code className="ds-kiosk-surface-path">{s.path}</code>
                </a>
              ))}
            </div>

            <div className="ds-kiosk-try">
              <div className="ds-kiosk-try-hero">
                <div className="ds-kiosk-rollup-label">Live at the booth</div>
                <h3>
                  Build agentic workflows with <em>Aurora and MCP</em>
                </h3>
                <p>
                  Aurora PostgreSQL + pgvector, MCP tool servers, Strands orchestration, and
                  AgentCore runtime/gateway/memory. Scan to clone the repo and run all three
                  surfaces locally in minutes.
                </p>
              </div>
              <div className="ds-kiosk-try-grid">
                <div className="ds-kiosk-qr-card">
                  <div className="ds-kiosk-qr-frame">
                    <img
                      src={TRY_QR_IMAGE_SRC}
                      alt="QR code to Meridian repository"
                      className="ds-kiosk-qr-img"
                      onLoad={() => setQrMissing(false)}
                      onError={() => setQrMissing(true)}
                    />
                  </div>
                </div>
                <div className="ds-kiosk-try-meta">
                  <div className="ds-kiosk-meta-block">
                    <div className="ds-kiosk-link-label">GitHub repository</div>
                    <a href={KIOSK_GITHUB_REPO} target="_blank" rel="noreferrer" className="ds-kiosk-link">
                      {KIOSK_GITHUB_REPO}
                    </a>
                  </div>
                  <div className="ds-kiosk-meta-block">
                    <div className="ds-kiosk-link-label">Booth shortcut</div>
                    <div className="ds-kiosk-shortcuts">
                      <code>/demo-stage?kiosk=1</code>
                      <code>/demo-stage?kiosk=1&amp;phase=3</code>
                    </div>
                  </div>
                  <div className="ds-kiosk-meta-grid">
                    <div className="ds-kiosk-meta-block">
                      <div className="ds-kiosk-link-label">Run locally</div>
                      <pre className="ds-kiosk-snippet">
{`cd meridian
source venv/bin/activate
uvicorn backend.main:app --reload --port 8000`}
                      </pre>
                    </div>
                    <div className="ds-kiosk-meta-block">
                      <div className="ds-kiosk-link-label">Strands + LangGraph</div>
                      <pre className="ds-kiosk-snippet">
{`# Retrieval
supervisor -> SearchAgent._hybrid_search_tool(...)

# Workflow (LangGraph)
StateGraph: classify -> branch -> synthesize`}
                      </pre>
                    </div>
                  </div>
                  <div className="ds-kiosk-meta-note">
                    Live stack: Aurora PostgreSQL · MCP · Strands · AgentCore · LangGraph
                  </div>
                </div>
              </div>
            </div>
            {qrMissing && (
              <div className="ds-kiosk-missing">
                <b>Add a QR image asset</b>
                <span>
                  Save one at <code>meridian/frontend/public/kiosk/try-meridian-qr.png</code> and it
                  will render here instantly.
                </span>
              </div>
            )}
          </section>
        )}
      </div>

      <SpanInspector
        span={inspectorSpan}
        onClose={() => setSelectedSpanIdx(null)}
      />
    </div>
  );
}

export default DemoStage;
