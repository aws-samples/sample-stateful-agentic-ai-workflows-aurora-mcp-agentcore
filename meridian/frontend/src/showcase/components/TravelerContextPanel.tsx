import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { ALEX_IMAGE_URL, ALEX_NAME } from '../lib/personas';

// Snake-case schema keys read as "authentic Aurora data" for some fields
// (no_red_eye, vegetarian_friendly) but feel awkward for multi-word
// concepts (loyalty_programs, travel_style, recent_trips). Whitelist the
// ones we want to keep snake_case; humanize the rest.
const VERBATIM_KEYS = new Set([
  'no_red_eye',
  'vegetarian_friendly',
  'home_airport',
  'budget_cap',
  'avoid_connections',
]);

function formatFactKey(key: string): string {
  if (VERBATIM_KEYS.has(key)) return key;
  return key.replace(/_/g, ' ');
}

// Respect the OS reduced-motion setting: spring pops become plain fades.
const prefersReducedMotion =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function TravelerContextPanel({
  state,
  onOpenMemory,
  compact = false,
  collapsed = false,
  onToggleCollapsed,
}: {
  state: MeridianShowcaseState;
  onOpenMemory: () => void;
  compact?: boolean;
  /** Collapsed mode keeps just the header + chevron so the trace panel
   *  below can claim the freed vertical space. Used on tall demos /
   *  zoomed-in views where the audience needs the trace fully visible. */
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}) {
  // Show every preference fact Aurora returned (capped only so the panel
  // doesn't scroll forever). Underscored keys are kept verbatim - the
  // schema renders them snake_case which reads as authentic data, but
  // multi-word slugs like "travel_style" get a single space for clarity.
  const facts = state.memoryFacts.slice(0, compact ? 6 : 14);

  // "Writeback you can watch": Phase 4's concierge persists each turn to
  // Aurora inside the RLS transaction, emitting a "Strands @tool persist_turn"
  // span. When that span appears on a turn we pulse a "+N written to Aurora"
  // badge on the header so the audience FEELS the write actually happen.
  // The row count is the persist_turn write shape: 2 conversation_messages +
  // 1 trip_interaction = 3 rows (backend/agents/production_04/memory_agent.py
  // persist_turn, ~lines 220-271). If a span ever exposes an explicit count
  // we prefer that.
  const persistSpan =
    state.selectedPhase >= 4
      ? state.traceSpans.find((s) => /persist[_ ]turn/i.test(s.name))
      : undefined;
  const writebackRows = (() => {
    if (!persistSpan) return 3;
    const rowsField = persistSpan.fields?.find((f) => /rows|count/i.test(f.label));
    const parsed = rowsField ? parseInt(rowsField.value, 10) : NaN;
    return Number.isFinite(parsed) ? parsed : 3;
  })();

  const [justWrote, setJustWrote] = useState(false);
  const lastPersistId = useRef<string | null>(null);
  useEffect(() => {
    if (!persistSpan) return;
    if (persistSpan.id === lastPersistId.current) return;
    lastPersistId.current = persistSpan.id;
    setJustWrote(true);
    const timer = setTimeout(() => setJustWrote(false), 2500);
    return () => clearTimeout(timer);
  }, [persistSpan]);

  const className = [
    'mds-panel',
    'mds-traveler-panel',
    compact ? 'is-compact' : '',
    collapsed ? 'is-collapsed' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <section className={className}>
      <div className="mds-panel-head">
        {onToggleCollapsed ? (
          <button
            type="button"
            className="mds-collapse-toggle"
            onClick={onToggleCollapsed}
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Expand For you panel' : 'Collapse For you panel'}
            title={collapsed ? 'Expand For you' : 'Collapse so the trace fills the rail'}
          >
            <span className="mds-collapse-chevron" aria-hidden="true">
              <span className="mds-collapse-chevron-inner">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </span>
            </span>
            <strong>For you</strong>
            {collapsed && state.memoryFacts.length > 0 && (
              <span className="mds-collapse-hint">{state.memoryFacts.length} facts</span>
            )}
          </button>
        ) : (
          <strong>For you</strong>
        )}
        <AnimatePresence>
          {justWrote && (
            <motion.span
              className="mds-writeback-badge"
              initial={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.7, y: -2 }}
              animate={prefersReducedMotion ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0 }}
              exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.8 }}
              transition={prefersReducedMotion ? { duration: 0 } : { type: 'spring', stiffness: 500, damping: 30 }}
              aria-live="polite"
            >
              <span className="mds-writeback-dot" aria-hidden="true" />
              +{writebackRows} written to Aurora
            </motion.span>
          )}
        </AnimatePresence>
        <button type="button" onClick={onOpenMemory}>
          Memory
        </button>
      </div>
      {!collapsed && (
        <>
          <div className="mds-profile-line">
            <span className="mds-avatar is-photo" aria-hidden="true">
              <img src={ALEX_IMAGE_URL} alt={ALEX_NAME} loading="lazy" />
            </span>
            <div>
              <strong>Alex Morgan</strong>
              <small>{state.travelerId}</small>
            </div>
          </div>
          <div className="mds-fact-list">
            {facts.map((fact) => (
              <div className="mds-fact-row" key={fact.key}>
                <span>{formatFactKey(fact.key)}</span>
                <b>{fact.value}</b>
              </div>
            ))}
          </div>
          <div className="mds-run-config">
            <div>
              <span>Mode</span>
              <b>{state.phaseLabel}</b>
            </div>
            <div>
              <span>Model</span>
              <b>{state.modelLabel}</b>
            </div>
            <div>
              <span>Embed</span>
              <b>{state.embedLabel}</b>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
