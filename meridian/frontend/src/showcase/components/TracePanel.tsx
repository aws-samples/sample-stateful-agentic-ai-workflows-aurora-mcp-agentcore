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

// Classify specific actions before generic categories: older retrieval events
// arrive as "orchestration" even when they are database searches. Every event
// belongs to exactly one group, including unrecognized or unsuccessful events.
const ACTIVITY_GROUPS = [
  { id: 'understand', label: 'Understanding request' },
  { id: 'recall', label: 'Recalling traveler context' },
  { id: 'inventory', label: 'Querying live travel data' },
  { id: 'curate', label: 'Evaluating options' },
  { id: 'optimize', label: 'Preparing response' },
  { id: 'other', label: 'Additional activity' },
];

function activityGroup(span: ShowcaseTraceSpan): string {
  const { name, category, type } = span;
  if (/^Processing with|disabled/i.test(name) || category === 'security') return 'understand';
  if (category === 'synthesis' || type === 'result' || /checkpoint|persist|response ready|concierge polish|workflow node: synthes/i.test(name)) return 'optimize';
  if (['memory_short', 'memory_long'].includes(category) || /recall|memory|preferences|interaction/i.test(name)) return 'recall';
  if (/rerank|rank|compose|turn complete/i.test(name)) return 'curate';
  if (['data', 'tool', 'gateway'].includes(category) || /sql|pgvector|tools\/call|gateway|availability|trip_packages|booking|hybrid|embed|semantic search|lexical|catalog.*hydrat|search agent completed|eligibility filter/i.test(name)) return 'inventory';
  if (category === 'orchestration' || type === 'delegation' || /classify|identity|scope|session|routing|invoked|strands agent|supervisor|turn started/i.test(name)) return 'understand';
  if (category === 'model' || /claude|opus|reasoning/i.test(name)) return 'curate';
  return 'other';
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
                {state.traceSpans.length} events
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
            {hasTraceActivity ? <ActivityTrace key={state.traceSpans[0]?.id ?? 'waiting'} state={state} />
              : <div className="mds-empty">Ask a question to see the evidence behind the answer.</div>}

            {!compact && <details className="mds-trace-inspector">
              <summary>Inspect evidence<ChevronDown size={14} aria-hidden="true" /></summary>
              <div className="mds-trace-summary">
                <span>{state.phaseLabel}</span>
                {phaseMeta && proofObserved && (
                  <span className="mds-proof-pill">{phaseMeta.proofPoint}</span>
                )}
                {state.traceSpans.length > 0 && <span>{state.traceSpans.length} events</span>}
                {agentCount > 0 && <span>{agentCount} agents</span>}
                {state.totalLatencyMs > 0 && <span>{state.totalLatencyMs}ms recorded</span>}
              </div>
            {state.selectedPhase === 2 && <McpToolContractPanel state={state} />}
            {state.selectedPhase === 5 && <WorkflowStateInspector state={state} />}
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
                      ? 'Overview'
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

            {state.traceTab === 'spans' ? null : state.traceTab === 'memory' ? (
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
            </details>}
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
  { name: 'aurora', label: 'Aurora', matches: span => Boolean(span.sql) || /aurora|postgres|pgvector/i.test(`${span.name} ${span.component ?? ''}`) || (span.agent === 'SearchAgent' && /semantic search|catalog card details hydrated|lexical candidates merged/i.test(span.name)) },
  { name: 'bedrock', label: 'Bedrock', matches: span => /claude|cohere|bedrock(?!\s+agentcore)/i.test(`${span.name} ${span.component ?? ''}`) || (span.agent === 'SearchAgent' && /^(Generating text embedding|Text embedding generated)$/i.test(span.name)) },
  { name: 'lambda', label: 'Lambda', matches: span => /lambda/i.test(`${span.name} ${span.component ?? ''}`) },
];

function ActivityTrace({ state }: { state: MeridianShowcaseState }) {
  const spans = state.traceSpans;
  // The response carries the trace as a batch. Do not invent live progress.
  if (state.isLoading && !spans.length) {
    return <div className="mds-thinking mds-thinking-wait" role="status">
      <Loader2 size={18} aria-hidden="true" />
      <div><strong>Working on your request</strong><p>Activity appears with the response.</p></div>
    </div>;
  }
  const reached = state.isReplaying ? spans.slice(0, Math.max(0, state.replayIndex + 1)) : spans;
  const currentSpan = spans[state.replayIndex];
  const currentGroup = state.isReplaying && currentSpan ? activityGroup(currentSpan) : undefined;
  const statusLabels = { done: 'Complete', active: 'Replaying', pending: 'Upcoming', unconfirmed: 'Unconfirmed', error: 'Failed', denied: 'Blocked' };

  return (
    <div className="mds-thinking">
      <p className="mds-thinking-caption" role="status">{state.isReplaying ? 'Replaying recorded activity' : 'Recorded activity'} · {spans.length} events</p>
      <ol className="mds-thinking-list" aria-label="Recorded request steps">
        {ACTIVITY_GROUPS.filter(group => spans.some(span => activityGroup(span) === group.id)).map(group => {
          const recorded = reached.filter(span => activityGroup(span) === group.id);
          const status = recorded.some(span => span.status === 'denied') ? 'denied'
            : recorded.some(span => span.status === 'error') ? 'error'
              : currentGroup === group.id ? 'active'
                : !recorded.length ? 'pending'
                  : recorded.every(span => ['ok', 'delegated'].includes(span.status)) ? 'done' : 'unconfirmed';
          const services = ACTIVITY_SERVICES.filter(service => recorded.some(span => !/^Processing with/i.test(span.name) && service.matches(span)));
          const StatusIcon = status === 'done' ? Check : status === 'error' ? X
            : status === 'denied' ? ShieldX : status === 'active' ? Loader2 : Circle;
          return (
            <li key={group.id} className={`mds-thinking-item is-${status}`} aria-current={status === 'active' ? 'step' : undefined}>
              <details className="mds-activity-group">
                <summary>
                  <span className="mds-thinking-marker" aria-hidden="true"><StatusIcon size={17} strokeWidth={2} /></span>
                  <span className="mds-thinking-copy">
                    <span>{group.label}</span>
                    <span className="mds-thinking-meta">
                      <span className="mds-thinking-status">{statusLabels[status]}</span>
                      {services.map(service => <span className="mds-thinking-service" key={service.name}>
                        <ServiceMark name={service.name} size={16} /><span>{service.label}</span>
                      </span>)}
                      {recorded.length > 0 && services.length === 0 && <span className="mds-thinking-service"><Workflow size={15} aria-hidden="true" /><span>Meridian app</span></span>}
                    </span>
                  </span>
                  <ChevronDown className="mds-activity-chevron" size={15} aria-hidden="true" />
                </summary>
                <div className="mds-activity-events">
                  {recorded.length ? recorded.map(span => <TraceSpanRow key={span.id} span={span} index={spans.indexOf(span)} active={state.isReplaying && span.id === currentSpan?.id} />)
                    : <p className="mds-empty">This step has not been reached in the replay.</p>}
                </div>
              </details>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function TraceSpanRow({ span, index, active }: { span: ShowcaseTraceSpan; index: number; active: boolean }) {
  const denied = span.status === 'denied';
  const failed = span.status === 'error';
  const statusLabel = denied ? 'Denied by policy' : failed ? 'Failed' : span.status;
  return (
    <details className={`mds-activity-event${active ? ' is-active' : ''}${denied ? ' is-denied' : ''}${failed ? ' is-failed' : ''}`}>
      <summary>
        <span className="mds-activity-event-index">{index + 1}</span>
        <span>{span.name}</span>
        <ChevronDown size={13} aria-hidden="true" />
      </summary>
      <div className="mds-activity-event-detail">
        <p className="mds-activity-event-meta">{span.category} · {statusLabel}{span.latencyMs === null ? '' : ` · ${span.latencyMs}ms`}{span.component ? ` · ${span.component}` : ''}</p>
        {(span.agent || span.file) && <p className="mds-activity-event-source">{span.agent ?? 'Agent'}{span.file ? ` · ${span.file}` : ''}</p>}
        <p>{span.details || span.output || 'No output payload on this event.'}</p>
        {span.sql && <pre>{span.sql}</pre>}
        {span.fields.map(field => <p key={`${span.id}-${field.label}`}>{field.label}: <SpanFieldValue value={field.value} /></p>)}
      </div>
    </details>
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
