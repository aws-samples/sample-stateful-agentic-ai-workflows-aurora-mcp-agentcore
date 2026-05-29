/**
 * TraceHero — the dominant element on the Demo Stage.
 *
 * Renders the span timeline as a stack of animated horizontal bars. Each row
 * shows kind tag, name + detail, latency bar, ms. Rows are clickable buttons
 * that surface the inspector drawer.
 */
import { ConciergeResponseCard } from './ConciergeResponseCard';
import { RecommendationDeck } from './RecommendationDeck';
import type { StageRecommendation, StageSpan } from '../types';

const KIND_LABEL: Record<string, string> = {
  orchestration: 'orch',
  memory: 'memory',
  tool: 'mcp tool',
  data: 'aurora',
  model: 'model',
  synthesis: 'compose',
  security: 'policy',
};

interface TraceHeroProps {
  spans: StageSpan[];
  activeIndex: number;
  selectedIndex: number | null;
  totalLatencyMs: number;
  onSelect: (idx: number) => void;
  view: 'audience' | 'builder';
  /** Natural-language reply rendered in the footer of the panel. */
  assistantReply: string;
  /** One-line provenance summary (e.g. "supervisor.plan → memory.recall → …"). */
  reasoning: string;
  /** Reveal state for the response card. */
  replyPhase: 'pending' | 'composing' | 'composed';
  /** Top recommendation, surfaced under the reply once composed. */
  primaryRecommendation?: StageRecommendation | null;
  /** Full recommendation set, rendered inline under the reply once the
   *  typewriter finishes — so the trace literally produces the cards. */
  recommendations?: StageRecommendation[];
  /** True once the reply typewriter has landed; gates the inline deck. */
  showDeck?: boolean;
  /** Bubbled up when the reply typewriter finishes, so the parent can
   *  reveal the product deck right after the stream lands. */
  onReplyStreamComplete?: () => void;
  /** When true, the trace span list folds away (the completed reply stays)
   *  so the answer + product cards rise into view without scrolling. */
  collapsed?: boolean;
  /** Toggles the collapsed state via the header arrow. */
  onToggleCollapsed?: () => void;
}

export function TraceHero({
  spans,
  activeIndex,
  selectedIndex,
  totalLatencyMs,
  onSelect,
  view,
  assistantReply,
  reasoning,
  replyPhase,
  primaryRecommendation,
  recommendations = [],
  showDeck = false,
  onReplyStreamComplete,
  collapsed = false,
  onToggleCollapsed,
}: TraceHeroProps) {
  const peak = Math.max(...spans.map((s) => s.latencyMs ?? 0), 1);

  return (
    <section className={`ds-panel ds-trace-hero${collapsed ? ' is-collapsed' : ''}`} aria-label="Agent trace">
      <header className="ds-trace-hero-header">
        <div className="ds-trace-hero-title">
          <span className="ds-trace-hero-eyebrow">The trip you describe</span>
          <h1 className="ds-trace-hero-heading">
            The trace that <em>proves it</em>.
          </h1>
        </div>
        <div className="ds-trace-stats">
          <div>
            Spans
            <b>{spans.length}</b>
          </div>
          <div>
            Latency
            <b>{totalLatencyMs}ms</b>
          </div>
          <div>
            Active
            <b>{activeIndex < 0 ? '—' : `${activeIndex + 1}/${spans.length}`}</b>
          </div>
          {onToggleCollapsed && (
            <button
              type="button"
              className="ds-trace-collapse"
              onClick={onToggleCollapsed}
              aria-expanded={!collapsed}
              aria-label={collapsed ? 'Expand trace' : 'Collapse trace'}
              title={collapsed ? 'Expand trace' : 'Collapse trace'}
            >
              <span className="ds-trace-collapse-chevron" aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </span>
            </button>
          )}
        </div>
      </header>

      <div className="ds-trace-canvas" role="list" hidden={collapsed}>
        {spans.map((span, idx) => {
          const widthPct = Math.max(8, Math.round(((span.latencyMs ?? 0) / peak) * 100));
          const isActive = idx === activeIndex;
          const isPending = activeIndex >= 0 && idx > activeIndex;
          const isSelected = selectedIndex === idx;
          return (
            <button
              key={span.id}
              type="button"
              className={`ds-trace-row kind-${span.kind}${isActive ? ' is-active' : ''}${isPending ? ' is-pending' : ''}${isSelected ? ' is-selected' : ''}`}
              role="listitem"
              aria-label={`${span.name} ${span.latencyMs} milliseconds`}
              aria-pressed={isSelected}
              onClick={() => onSelect(idx)}
            >
              <span className="ds-trace-tag">{KIND_LABEL[span.kind] ?? span.kind}</span>
              <div className="ds-trace-meta">
                <span className="ds-trace-name">{span.name}</span>
                <span className="ds-trace-detail">
                  {view === 'builder' ? span.source ?? span.detail ?? span.component ?? '' : span.detail ?? span.component ?? ''}
                </span>
              </div>
              <div className="ds-trace-bar-cell">
                <div className="ds-trace-bar" aria-hidden="true">
                  <span style={{ width: isPending ? '0%' : `${widthPct}%` }} />
                </div>
              </div>
              <span className="ds-trace-ms">{span.latencyMs}ms</span>
            </button>
          );
        })}
      </div>

      <ConciergeResponseCard
        reply={assistantReply}
        reasoning={reasoning}
        phase={replyPhase}
        primary={primaryRecommendation ?? null}
        onStreamComplete={onReplyStreamComplete}
      />

      {/* The cards land INSIDE the panel, right under the reply — so the
          trace visibly produces the results instead of leaving a static
          deck parked below. Revealed only after the typewriter finishes. */}
      {showDeck && recommendations.length > 0 && (
        <div className="ds-trace-hero-deck">
          <RecommendationDeck recommendations={recommendations} />
        </div>
      )}
    </section>
  );
}
