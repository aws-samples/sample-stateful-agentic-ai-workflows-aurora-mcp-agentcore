import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import type { ShowcaseTraceSpan } from '../lib/showcaseAdapters';

export function TracePanel({ state, compact = false }: { state: MeridianShowcaseState; compact?: boolean }) {
  const sqlSpans = state.traceSpans.filter((span) => span.sql);
  const memoryFacts = state.memoryFacts;
  const activeSpans = compact ? state.traceSpans.slice(0, 4) : state.traceSpans;

  return (
    <section className={`mds-panel mds-trace-panel${compact ? ' is-compact' : ''}`}>
      <div className="mds-panel-head">
        <strong>Meridian activity</strong>
        <span className={`mds-live-state ${state.isLoading || state.isReplaying ? 'is-live' : ''}`}>
          {state.isLoading ? 'Running' : state.isReplaying ? 'Replay' : 'Live'}
        </span>
      </div>
      {!compact && (
        <div className="mds-trace-tabs" role="tablist" aria-label="Trace filters">
          {(['spans', 'memory', 'sql', 'cost'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              className={state.traceTab === tab ? 'is-active' : ''}
              onClick={() => state.setTraceTab(tab)}
            >
              {tab}
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
            sqlSpans.map((span) => <pre key={span.id}>{span.sql}</pre>)
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

function TraceSpanRow({
  span,
  index,
  active,
  expanded,
  onToggle,
}: {
  span: ShowcaseTraceSpan;
  index: number;
  active: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <button type="button" className={`mds-span-row${active ? ' is-active' : ''}`} onClick={onToggle}>
      <span className="mds-span-check">{index + 1}</span>
      <span className="mds-span-main">
        <span className="mds-span-title">{span.name}</span>
        <span className="mds-span-meta">
          {span.category} · {span.status} · {span.latencyMs}ms
          {span.component ? ` · ${span.component}` : ''}
        </span>
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
