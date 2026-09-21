import { useState } from 'react';
import { Check, ChevronDown, Circle, Copy, Loader2, RefreshCw, RotateCcw, ShieldX, Workflow, X } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { SHOWCASE_PHASES, type ShowcaseTraceSpan } from '../lib/showcaseAdapters';
import { WorkflowGraph } from './WorkflowGraph';
import { RlsProbeCard } from './RlsProbeCard';
import { McpToolContractPanel } from './McpToolContractPanel';
import { WorkflowStateInspector } from './WorkflowStateInspector';
import { IconTooltip } from './ShowcaseTooltip';
import { ServiceMark, type ServiceMarkName } from './ServiceMark';
import { deriveAuroraEvidence, isPhaseProofObserved } from '../lib/showcaseProof';

// Maps raw trace spans into five audience-readable progress steps. A span is
// claimed by the first step that matches it, so a step whose spans are all
// claimed by an earlier one can never land. That is why the runtime's opening
// and closing spans are matched by name here rather than by their shared
// category: "turn started" is the request being understood, "turn complete" is
// the model having evaluated the options.
const THINKING_PHASES: { id: string; label: string; matches: (span: ShowcaseTraceSpan) => boolean }[] = [
  {
    id: 'understand',
    label: 'Understanding request',
    matches: (s) =>
      /^Processing with/i.test(s.name) ||
      ['orchestration', 'security'].includes(s.category) ||
      s.type === 'delegation' ||
      (!['memory_short', 'memory_long', 'synthesis'].includes(s.category) &&
        /classify|identity|scope|session|routing|strands agent|supervisor|turn started/i.test(s.name)),
  },
  {
    id: 'recall',
    label: 'Recalling traveler context',
    matches: (s) =>
      s.category !== 'synthesis' && !/checkpoint|persist|disabled/i.test(s.name) && (
        ['memory_short', 'memory_long'].includes(s.category) ||
        /recall|memory|preferences|interaction/i.test(s.name)),
  },
  {
    id: 'inventory',
    label: 'Querying live travel data',
    matches: (s) =>
      s.category !== 'model' && !/rerank/i.test(s.name) && (
        ['data', 'tool'].includes(s.category) ||
        /sql|pgvector|run_query|tools\/call|gateway|availability|trip_packages|booking|hybrid|embed|cohere/i.test(s.name)),
  },
  {
    id: 'curate',
    label: 'Evaluating options',
    matches: (s) =>
      s.category === 'model' ||
      (s.category !== 'synthesis' && /rerank|rank|compose|synthes|claude|opus|reasoning|turn complete/i.test(s.name)),
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
  // The pill asserts the phase's proof point, so it waits for the
  // evidence behind that claim rather than for any span at all.
  const proofObserved = isPhaseProofObserved(
    state.selectedPhase,
    deriveAuroraEvidence({
      selectedPhase: state.selectedPhase,
      traceSpans: state.traceSpans,
      recommendations: state.recommendations,
    }),
  );
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
            {/* Recorded steps carry their own status and observed service sources. */}
            {hasTraceActivity && <ThinkingPhases state={state} />}

            {!compact && (
              <div className="mds-trace-summary">
                <span>{state.phaseLabel}</span>
                {phaseMeta && proofObserved && (
                  <span className="mds-proof-pill">{phaseMeta.proofPoint}</span>
                )}
                {state.traceSpans.length > 0 && <span>{state.traceSpans.length} spans</span>}
                {agentCount > 0 && <span>{agentCount} agents</span>}
                {state.totalLatencyMs > 0 && <span>{state.totalLatencyMs}ms recorded</span>}
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
      timing_basis: 'Sum of recorded span durations; nested spans may overlap.',
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

const ACTIVITY_SERVICES: { name: ServiceMarkName; label: string; matches: (span: ShowcaseTraceSpan) => boolean }[] = [
  { name: 'agentcore', label: 'AgentCore', matches: span => /agentcore/i.test(`${span.name} ${span.component ?? ''}`) },
  { name: 'aurora', label: 'Aurora', matches: span => Boolean(span.sql) || /aurora|postgres|pgvector/i.test(`${span.name} ${span.component ?? ''}`) },
  { name: 'bedrock', label: 'Bedrock', matches: span => /claude|cohere|bedrock(?!\s+agentcore)/i.test(`${span.name} ${span.component ?? ''}`) },
  { name: 'lambda', label: 'Lambda', matches: span => /lambda/i.test(`${span.name} ${span.component ?? ''}`) },
];

function ThinkingPhases({ state }: { state: MeridianShowcaseState }) {
  const spans = state.traceSpans;
  const phaseBySpan = classifySpansToPhases(spans);
  // Trace arrives with the HTTP response. Never animate guessed tool progress
  // while waiting, or turn absent evidence into a successful step.
  if (state.isLoading && !spans.length) {
    return <div className="mds-thinking mds-thinking-wait" role="status">
      <Loader2 size={18} aria-hidden="true" />
      <div><strong>Working on your request</strong><p>Activity appears with the response.</p></div>
    </div>;
  }
  const phases = THINKING_PHASES.filter(phase =>
    (phase.id !== 'recall' || state.selectedPhase === 5 || (state.selectedPhase === 4 && state.memoryEnabled)) &&
    spans.some(span => phaseBySpan.get(span.id) === phase.id));
  const reached = state.isReplaying ? spans.slice(0, Math.max(0, state.replayIndex + 1)) : spans;
  const currentSpan = spans[state.replayIndex];
  const currentPhase = state.isReplaying && currentSpan ? phaseBySpan.get(currentSpan.id) : undefined;
  const statusLabels = { done: 'Complete', active: 'Replaying', pending: 'Upcoming', unconfirmed: 'Unconfirmed', error: 'Failed', denied: 'Blocked' };

  return (
    <div className="mds-thinking" aria-live="polite">
      <p className="mds-thinking-caption">{state.isReplaying ? 'Replaying recorded activity' : 'Recorded activity'}</p>
      <ol className="mds-thinking-list" aria-label="Recorded request steps">
        {phases.map(phase => {
          const recorded = reached.filter(span => phaseBySpan.get(span.id) === phase.id);
          // Keep earlier evidence when replay revisits an earlier group. A
          // canonical phase index is not the execution order of the trace.
          const status = recorded.some(span => span.status === 'denied') ? 'denied'
            : recorded.some(span => span.status === 'error') ? 'error'
              : currentPhase === phase.id ? 'active'
                : recorded.some(span => ['ok', 'delegated'].includes(span.status)) ? 'done'
                  : recorded.length ? 'unconfirmed' : 'pending';
          const services = ACTIVITY_SERVICES.filter(service => recorded.some(service.matches));
          const StatusIcon = status === 'done' ? Check : status === 'error' ? X
            : status === 'denied' ? ShieldX : status === 'active' ? Loader2 : Circle;
          return (
            <li key={phase.id} className={`mds-thinking-item is-${status}`} aria-current={status === 'active' ? 'step' : undefined}>
              <span className="mds-thinking-marker" aria-hidden="true"><StatusIcon size={17} strokeWidth={2} /></span>
              <span className="mds-thinking-copy">
                <span>{phase.label}</span>
                <span className="mds-thinking-meta">
                  <span className="mds-thinking-status">{statusLabels[status]}</span>
                  {services.map(service => <span className="mds-thinking-service" key={service.name}>
                    <ServiceMark name={service.name} size={16} /><span>{service.label}</span>
                  </span>)}
                  {recorded.length > 0 && services.length === 0 && <span className="mds-thinking-service"><Workflow size={15} aria-hidden="true" /><span>Meridian app</span></span>}
                </span>
              </span>
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
  const denied = span.status === 'denied';
  const failed = span.status === 'error';
  const statusLabel = denied ? 'Denied by policy' : failed ? 'Failed' : span.status;

  // A div with button semantics: the expanded detail can carry a real link
  // (the CloudWatch trace), which HTML does not allow inside a <button>.
  return (
    <div
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      className={`mds-span-row${active ? ' is-active' : ''}${visible ? '' : ' is-pending'}${denied ? ' is-denied' : ''}${failed ? ' is-failed' : ''}`}
      style={{ animationDelay }}
      onClick={onToggle}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onToggle();
        }
      }}
    >
      <span className="mds-span-check">{index + 1}</span>
      <span className="mds-span-main">
        <span className="mds-span-title">{span.name}</span>
        <span className="mds-span-meta">
          {span.category} · {statusLabel} · {span.latencyMs === null ? 'timing not recorded' : `${span.latencyMs}ms`}
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
                {field.label}: <SpanFieldValue value={field.value} />
              </small>
            ))}
          </span>
        )}
      </span>
    </div>
  );
}

/** Field values that are URLs (the CloudWatch trace link) open in a new tab. */
function SpanFieldValue({ value }: { value: string }) {
  if (!/^https:\/\//.test(value)) return <>{value}</>;
  return (
    <a href={value} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>
      {value.includes('console.aws.amazon.com') ? 'Open in CloudWatch' : value}
    </a>
  );
}
