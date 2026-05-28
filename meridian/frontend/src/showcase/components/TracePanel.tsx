import { useEffect, useRef, useState } from 'react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import type { ShowcaseTraceSpan } from '../lib/showcaseAdapters';

// The five "thinking phases" Meridian narrates while a turn is in flight.
// Each phase maps onto a contiguous slice of the trace span timeline so the
// progress bar stays in sync with what the agent is actually doing.
const THINKING_PHASES: { id: string; label: string; matches: (span: ShowcaseTraceSpan) => boolean }[] = [
  {
    id: 'understand',
    label: 'Understanding your request',
    matches: (s) =>
      ['orchestration', 'security', 'runtime'].includes(s.category) ||
      s.type === 'delegation' ||
      /classify|identity|scope|session|routing|strands agent|supervisor/i.test(s.name),
  },
  {
    id: 'recall',
    label: 'Searching previously-matched destinations',
    matches: (s) =>
      ['memory_short', 'memory_long'].includes(s.category) ||
      /recall|memory|preferences|interaction|embed|cohere/i.test(s.name),
  },
  {
    id: 'inventory',
    label: 'Checking availability & pricing',
    matches: (s) =>
      ['data', 'tool'].includes(s.category) ||
      /sql|pgvector|run_query|tools\/call|gateway|availability|trip_packages|booking|hybrid/i.test(s.name),
  },
  {
    id: 'curate',
    label: 'Curating personalized recommendations',
    matches: (s) =>
      s.category === 'model' || /rerank|rank|compose|synthes|claude|opus|reasoning/i.test(s.name),
  },
  {
    id: 'optimize',
    label: 'Optimizing your journey',
    matches: (s) =>
      s.category === 'synthesis' ||
      s.type === 'result' ||
      /persist|workflow node: synthes|memory-grounded|workflowstate|response ready/i.test(s.name),
  },
];

interface PhaseProgress {
  status: 'pending' | 'active' | 'done';
  spanIds: string[];
}

function classifySpansToPhases(spans: ShowcaseTraceSpan[]): Map<string, string> {
  // Each span gets routed to the first matching phase. Spans we can't classify
  // anchor to whichever phase is currently "filling" so the bar still advances.
  const map = new Map<string, string>();
  let lastPhaseIndex = 0;
  spans.forEach((span) => {
    const matchedIdx = THINKING_PHASES.findIndex((phase) => phase.matches(span));
    if (matchedIdx >= 0) {
      lastPhaseIndex = matchedIdx;
      map.set(span.id, THINKING_PHASES[matchedIdx].id);
    } else {
      map.set(span.id, THINKING_PHASES[lastPhaseIndex].id);
    }
  });
  return map;
}

export function TracePanel({ state, compact = false }: { state: MeridianShowcaseState; compact?: boolean }) {
  const sqlSpans = state.traceSpans.filter((span) => span.sql);
  const memoryFacts = state.memoryFacts;
  const agentCount = new Set(state.traceSpans.map((span) => span.agent).filter(Boolean)).size;
  const activeSpans = compact ? state.traceSpans.slice(0, 4) : state.traceSpans;

  return (
    <section className={`mds-panel mds-trace-panel${compact ? ' is-compact' : ''}`}>
      <div className="mds-panel-head">
        <strong>Meridian activity</strong>
        <span className={`mds-live-state ${state.isLoading || state.isReplaying ? 'is-live' : ''}`}>
          {state.isLoading ? 'Running' : state.isReplaying ? 'Replay' : 'Live'}
        </span>
      </div>

      {/* Claude Desktop-style progress bar — fills top→bottom as spans land. */}
      <ThinkingPhases state={state} />

      {!compact && (
        <div className="mds-trace-summary">
          <span>{state.phaseLabel}</span>
          <span>{state.traceSpans.length} spans</span>
          <span>{agentCount} agents</span>
          <span>{state.totalLatencyMs}ms</span>
        </div>
      )}
      {!compact && (
        <div className="mds-trace-tabs" role="tablist" aria-label="Trace filters">
          {(['spans', 'memory', 'sql', 'cost'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              className={state.traceTab === tab ? 'is-active' : ''}
              onClick={() => state.setTraceTab(tab)}
            >
              {tab === 'spans' ? 'Trace' : tab === 'memory' ? 'Memory' : tab === 'sql' ? 'SQL' : 'Cost'}
            </button>
          ))}
        </div>
      )}

      {state.traceTab === 'spans' || compact ? (
        <div className="mds-span-list">
          {activeSpans.length === 0 ? (
            <div className="mds-empty">Submit a prompt to generate trace spans.</div>
          ) : (
            activeSpans.map((span, index) => (
              <TraceSpanRow
                key={span.id}
                span={span}
                index={index}
                active={state.replayIndex === index || (!state.isReplaying && state.expandedSpanId === span.id)}
                visible={!state.isReplaying || state.replayIndex >= index}
                expanded={!compact && state.expandedSpanId === span.id}
                onToggle={() => state.setExpandedSpanId(state.expandedSpanId === span.id ? null : span.id)}
              />
            ))
          )}
        </div>
      ) : state.traceTab === 'memory' ? (
        <div className="mds-memory-mini">
          {memoryFacts.map((fact) => (
            <div key={fact.key}>
              <span>{fact.key}</span>
              <b>{fact.value}</b>
            </div>
          ))}
        </div>
      ) : state.traceTab === 'sql' ? (
        <div className="mds-sql-list">
          {sqlSpans.length ? (
            sqlSpans.map((span) => (
              <div key={span.id}>
                <small>{span.file ?? span.agent ?? 'SQL span'}</small>
                <pre>{span.sql}</pre>
              </div>
            ))
          ) : (
            <div className="mds-empty">No SQL snippet on this turn.</div>
          )}
        </div>
      ) : (
        <div className="mds-cost-card">
          <b>${state.estimatedCostUsd.toFixed(4)}</b>
          <span>{state.traceSpans.length} spans · {state.totalLatencyMs}ms · approximate</span>
        </div>
      )}

      {!compact && (
        <div className="mds-trace-actions">
          <button type="button" onClick={state.replayTrace} disabled={!state.traceSpans.length || state.isLoading}>
            Replay trace
          </button>
          <button type="button" onClick={state.replayLastPrompt} disabled={!state.lastPrompt || state.isLoading}>
            Rerun query
          </button>
        </div>
      )}
    </section>
  );
}

function ThinkingPhases({ state }: { state: MeridianShowcaseState }) {
  const spans = state.traceSpans;
  const phaseBySpan = classifySpansToPhases(spans);
  const isStreaming = state.isLoading || state.isReplaying;

  // While loading without trace spans yet, drive a synthetic progressive bar so
  // the user sees motion the moment they hit submit.
  const [syntheticTick, setSyntheticTick] = useState(0);
  const tickRef = useRef<number | null>(null);
  useEffect(() => {
    if (state.isLoading && spans.length === 0) {
      setSyntheticTick(0);
      const start = Date.now();
      const id = window.setInterval(() => {
        const elapsed = Date.now() - start;
        const next = Math.min(THINKING_PHASES.length - 1, Math.floor(elapsed / 380));
        setSyntheticTick(next);
      }, 200);
      tickRef.current = id;
      return () => {
        window.clearInterval(id);
        tickRef.current = null;
      };
    }
    setSyntheticTick(0);
    return undefined;
  }, [state.isLoading, spans.length]);

  const progress: PhaseProgress[] = THINKING_PHASES.map((phase) => ({
    status: 'pending',
    spanIds: spans.filter((span) => phaseBySpan.get(span.id) === phase.id).map((span) => span.id),
  }));

  if (spans.length > 0) {
    if (state.isReplaying) {
      const reachedSpanIndex = Math.max(0, state.replayIndex);
      const reachedSpan = spans[reachedSpanIndex];
      const reachedPhaseId = reachedSpan ? phaseBySpan.get(reachedSpan.id) : undefined;
      const reachedPhaseIndex = THINKING_PHASES.findIndex((p) => p.id === reachedPhaseId);
      progress.forEach((p, idx) => {
        if (idx < reachedPhaseIndex) p.status = 'done';
        else if (idx === reachedPhaseIndex) p.status = 'active';
        else p.status = 'pending';
      });
    } else if (state.isLoading) {
      // Streaming new turn: phase is active iff at least one span landed in it.
      progress.forEach((p) => {
        p.status = p.spanIds.length ? 'done' : 'pending';
      });
      const firstPending = progress.findIndex((p) => p.status === 'pending');
      if (firstPending !== -1) progress[firstPending].status = 'active';
    } else {
      progress.forEach((p) => {
        p.status = 'done';
      });
    }
  } else if (state.isLoading) {
    progress.forEach((p, idx) => {
      if (idx < syntheticTick) p.status = 'done';
      else if (idx === syntheticTick) p.status = 'active';
      else p.status = 'pending';
    });
  }

  return (
    <div className={`mds-thinking${isStreaming ? ' is-streaming' : ''}`} aria-live="polite">
      <ol className="mds-thinking-list">
        {THINKING_PHASES.map((phase, idx) => {
          const status = progress[idx].status;
          return (
            <li key={phase.id} className={`mds-thinking-item is-${status}`}>
              <span className="mds-thinking-rail" aria-hidden="true">
                <span className="mds-thinking-dot" />
              </span>
              <span className="mds-thinking-copy">{phase.label}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function TraceSpanRow({
  span,
  index,
  active,
  visible,
  expanded,
  onToggle,
}: {
  span: ShowcaseTraceSpan;
  index: number;
  active: boolean;
  visible: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  // Stagger the fan-in animation by index so 12 spans don't pop at once.
  // The CSS keyframe takes 320ms; delaying each subsequent row by 35ms
  // gives the bottom row a 380ms head-start without feeling sluggish.
  const animationDelay = `${Math.min(index * 35, 480)}ms`;

  return (
    <button
      type="button"
      className={`mds-span-row${active ? ' is-active' : ''}${visible ? '' : ' is-pending'}`}
      style={{ animationDelay }}
      onClick={onToggle}
    >
      <span className="mds-span-check">{index + 1}</span>
      <span className="mds-span-main">
        <span className="mds-span-title">{span.name}</span>
        <span className="mds-span-meta">
          {span.category} · {span.status} · {span.latencyMs}ms
          {span.component ? ` · ${span.component}` : ''}
        </span>
        {(span.agent || span.file) && (
          <span className="mds-span-source">
            {span.agent ?? 'Agent'}{span.file ? ` · ${span.file}` : ''}
          </span>
        )}
        {expanded && (
          <span className="mds-span-detail">
            {span.details || span.output || 'No output payload on this span.'}
            {span.sql && <code>{span.sql}</code>}
            {span.fields.map((field) => (
              <small key={`${span.id}-${field.label}`}>
                {field.label}: {field.value}
              </small>
            ))}
          </span>
        )}
      </span>
    </button>
  );
}
