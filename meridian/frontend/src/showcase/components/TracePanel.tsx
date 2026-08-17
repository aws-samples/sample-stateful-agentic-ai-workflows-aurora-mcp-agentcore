import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Copy, RefreshCw, RotateCcw } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { SHOWCASE_PHASES, type ShowcaseTraceSpan } from '../lib/showcaseAdapters';
import { WorkflowGraph } from './WorkflowGraph';
import { RlsProbeCard } from './RlsProbeCard';
import { McpToolContractPanel } from './McpToolContractPanel';
import { WorkflowStateInspector } from './WorkflowStateInspector';
import { IconTooltip } from './ShowcaseTooltip';

// Maps raw trace spans into five audience-readable progress steps.
const THINKING_PHASES: { id: string; label: string; matches: (span: ShowcaseTraceSpan) => boolean }[] = [
  {
    id: 'understand',
    label: 'Understanding request',
    matches: (s) =>
      ['orchestration', 'security', 'runtime'].includes(s.category) ||
      s.type === 'delegation' ||
      /classify|identity|scope|session|routing|strands agent|supervisor/i.test(s.name),
  },
  {
    id: 'recall',
    label: 'Recalling traveler context',
    matches: (s) =>
      ['memory_short', 'memory_long'].includes(s.category) ||
      /recall|memory|preferences|interaction/i.test(s.name),
  },
  {
    id: 'inventory',
    label: 'Querying live travel data',
    matches: (s) =>
      ['data', 'tool'].includes(s.category) ||
      /sql|pgvector|run_query|tools\/call|gateway|availability|trip_packages|booking|hybrid|embed|cohere/i.test(s.name),
  },
  {
    id: 'curate',
    label: 'Evaluating options',
    matches: (s) =>
      s.category === 'model' || /rerank|rank|compose|synthes|claude|opus|reasoning/i.test(s.name),
  },
  {
    id: 'optimize',
    label: 'Preparing response',
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
  const map = new Map<string, string>();
  spans.forEach((span) => {
    const matchedIdx = THINKING_PHASES.findIndex((phase) => phase.matches(span));
    if (matchedIdx >= 0) {
      map.set(span.id, THINKING_PHASES[matchedIdx].id);
    }
  });
  return map;
}

export function TracePanel({
  state,
  compact = false,
  collapsed = false,
  onToggleCollapsed,
}: {
  state: MeridianShowcaseState;
  compact?: boolean;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}) {
  const sqlSpans = state.traceSpans.filter((span) => span.sql);
  const memoryFacts = state.memoryFacts;
  const agentCount = new Set(state.traceSpans.map((span) => span.agent).filter(Boolean)).size;
  const activeSpans = compact ? state.traceSpans.slice(0, 4) : state.traceSpans;
  const phaseMeta = SHOWCASE_PHASES.find((phase) => phase.phase === state.selectedPhase);
  const hasTraceActivity =
    state.traceSpans.length > 0 || state.isLoading || state.isReplaying;
  const className = [
    'mds-panel',
    'mds-trace-panel',
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
            aria-label={collapsed ? 'Expand activity panel' : 'Collapse activity panel'}
            title={collapsed ? 'Expand activity' : 'Collapse activity'}
          >
            <span className="mds-collapse-chevron" aria-hidden="true">
              <span className="mds-collapse-chevron-inner">
                <ChevronDown size={12} strokeWidth={2.6} />
              </span>
            </span>
            <strong>Activity</strong>
            {collapsed && (
              <span className="mds-collapse-hint">
                {state.traceSpans.length} spans
              </span>
            )}
          </button>
        ) : (
          <strong>Activity</strong>
        )}
        <span
          className={`mds-live-state${
            state.isLoading || state.isReplaying
              ? ' is-live'
              : state.backendStatus === 'offline'
                ? ' is-offline'
                : ''
          }`}
          title={state.backendStatus === 'offline' ? 'Backend offline' : undefined}
        >
          {state.isLoading
            ? 'Running'
            : state.isReplaying
              ? 'Replay'
              : state.backendStatus === 'online'
                ? 'Live'
                : state.backendStatus === 'checking'
                  ? 'Connecting'
                  : 'Offline'}
        </span>
      </div>

      {!collapsed && (
        <>
          <div className="mds-trace-scroll">
            {/* Progress rail fills top-to-bottom as spans land. */}
            {hasTraceActivity && <ThinkingPhases state={state} />}

            {!compact && (
              <div className="mds-trace-summary">
                <span>{state.phaseLabel}</span>
                {phaseMeta && state.traceSpans.length > 0 && (
                  <span className="mds-proof-pill">{phaseMeta.proofPoint}</span>
                )}
                {state.traceSpans.length > 0 && <span>{state.traceSpans.length} spans</span>}
                {agentCount > 0 && <span>{agentCount} agents</span>}
                {state.totalLatencyMs > 0 && <span>{state.totalLatencyMs}ms</span>}
              </div>
            )}
            {!compact && state.selectedPhase === 2 && <McpToolContractPanel state={state} />}
            {!compact && state.selectedPhase === 5 && <WorkflowStateInspector state={state} />}
            {!compact && (
              <div className="mds-trace-tabs" role="group" aria-label="Trace filters">
                {/* RLS is a Phase 4 proof point, so other phases keep the lean tab set. */}
                {(state.selectedPhase === 4
                  ? (['spans', 'memory', 'sql', 'rls'] as const)
                  : (['spans', 'memory', 'sql'] as const)
                ).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    className={state.traceTab === tab ? 'is-active' : ''}
                    aria-pressed={state.traceTab === tab}
                    onClick={() => state.setTraceTab(tab)}
                  >
                    {tab === 'spans'
                      ? 'Trace'
                      : tab === 'memory'
                        ? 'Memory'
                        : tab === 'sql'
                          ? 'SQL'
                          : 'RLS'}
                  </button>
                ))}
              </div>
            )}

            {/* Phase 5 shows the executed graph path; spans remain the detail view. */}
            {(state.traceTab === 'spans' || compact) &&
              state.selectedPhase === 5 &&
              state.traceSpans.length > 0 && <WorkflowGraph state={state} />}

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
                {memoryFacts.length === 0 ? (
                  <div className="mds-empty">
                    Aurora-backed memory recalls at Phase 4+.
                  </div>
                ) : (
                  memoryFacts.map((fact) => (
                    <div key={fact.key}>
                      <span>{fact.key}</span>
                      <b>{fact.value}</b>
                    </div>
                  ))
                )}
              </div>
            ) : state.traceTab === 'rls' ? (
              <RlsProbeCard travelerId={state.travelerId} />
            ) : (
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
            )}
          </div>

          {!compact && (
            <div className="mds-trace-actions">
              <IconTooltip label="Replay trace">
                <button
                  type="button"
                  onClick={state.replayTrace}
                  disabled={!state.traceSpans.length || state.isLoading}
                  aria-label="Replay trace"
                >
                  <RotateCcw size={16} aria-hidden="true" />
                </button>
              </IconTooltip>
              <IconTooltip label="Rerun query">
                <button
                  type="button"
                  onClick={state.replayLastPrompt}
                  disabled={!state.lastPrompt || state.isLoading}
                  aria-label="Rerun query"
                >
                  <RefreshCw size={16} aria-hidden="true" />
                </button>
              </IconTooltip>
              <CopyTraceButton state={state} />
            </div>
          )}
        </>
      )}
    </section>
  );
}

// Copy active trace JSON for debugging or post-demo review.
function CopyTraceButton({ state }: { state: MeridianShowcaseState }) {
  const [copied, setCopied] = useState(false);
  const disabled = !state.traceSpans.length;

  const onCopy = async () => {
    if (disabled) return;
    const payload = {
      prompt: state.lastPrompt,
      phase: state.phaseLabel,
      model: state.modelLabel,
      embed: state.embedLabel,
      total_latency_ms: state.totalLatencyMs,
      span_count: state.traceSpans.length,
      spans: state.traceSpans.map((span) => ({
        index: state.traceSpans.indexOf(span) + 1,
        name: span.name,
        category: span.category,
        type: span.type,
        status: span.status,
        latency_ms: span.latencyMs,
        agent: span.agent,
        file: span.file,
        component: span.component,
        sql: span.sql,
        details: span.details,
        fields: span.fields,
      })),
    };
    const text = JSON.stringify(payload, null, 2);
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback for browsers that don't expose the async clipboard API.
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  const label = copied ? 'Trace copied' : "Copy this turn's trace as JSON";
  return (
    <IconTooltip label={label}>
      <button
        type="button"
        onClick={onCopy}
        disabled={disabled}
        aria-label={copied ? 'Trace copied' : 'Copy trace'}
      >
        {copied
          ? <Check size={16} aria-hidden="true" />
          : <Copy size={16} aria-hidden="true" />}
      </button>
    </IconTooltip>
  );
}

function ThinkingPhases({ state }: { state: MeridianShowcaseState }) {
  const spans = state.traceSpans;
  const phaseBySpan = classifySpansToPhases(spans);
  const isStreaming = state.isLoading || state.isReplaying;

  // Show immediate progress before the first real span arrives.
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
      const reachedPhaseIds = spans
        .slice(0, reachedSpanIndex + 1)
        .map((span) => phaseBySpan.get(span.id))
        .filter((phaseId): phaseId is string => Boolean(phaseId));
      const reachedPhaseId = reachedPhaseIds[reachedPhaseIds.length - 1];
      const reachedPhaseIndex = THINKING_PHASES.findIndex((p) => p.id === reachedPhaseId);
      progress.forEach((p, idx) => {
        if (idx < reachedPhaseIndex) p.status = 'done';
        else if (idx === reachedPhaseIndex) p.status = 'active';
        else p.status = 'pending';
      });
    } else if (state.isLoading) {
      // Streaming turn: mark landed phases done and keep the next phase active.
      progress.forEach((p) => {
        p.status = p.spanIds.length ? 'done' : 'pending';
      });
      const firstPending = progress.findIndex((p) => p.status === 'pending');
      if (firstPending !== -1) progress[firstPending].status = 'active';
    } else {
      progress.forEach((p) => {
        p.status = p.spanIds.length ? 'done' : 'pending';
      });
      progress[0].status = 'done';
      progress[progress.length - 1].status = 'done';
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
  // Stagger row entry so dense traces read as a sequence, not a flash.
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
