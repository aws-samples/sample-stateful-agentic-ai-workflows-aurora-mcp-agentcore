/**
 * TraceSpan — rich telemetry row for the agent trace panel
 */
import type { ActivityEntry } from '../types';

const CATEGORY_LABELS: Record<string, string> = {
  runtime: 'Runtime',
  memory_short: 'Short-term memory',
  memory_long: 'Long-term memory',
  orchestration: 'Orchestration',
  model: 'Model',
  tool: 'Tool / MCP',
  data: 'Data plane',
  synthesis: 'Synthesis',
  security: 'Security',
};

const STATUS_LABELS: Record<string, string> = {
  ok: 'ok',
  cache_hit: 'cache hit',
  streaming: 'streaming',
  held: 'held',
  delegated: 'delegated',
  preview: 'preview',
};

interface TraceSpanProps {
  entry: ActivityEntry;
  index: number;
  isCurrentStep: boolean;
  isPending?: boolean;
  phaseColor: string;
}

function activityIcon(type: string) {
  switch (type) {
    case 'search':
      return '🔍';
    case 'embedding':
      return '🧠';
    case 'mcp':
      return '🔌';
    case 'database':
      return '🗄️';
    case 'reasoning':
      return '💭';
    case 'result':
      return '✅';
    case 'error':
      return '❌';
    case 'availability':
      return '📅';
    case 'order':
      return '🧾';
    case 'security':
      return '🔐';
    default:
      return '⚡';
  }
}

export function TraceSpan({ entry, index, isCurrentStep, isPending, phaseColor }: TraceSpanProps) {
  const pc = phaseColor;
  const t = entry.telemetry;
  const spanNum = String(index + 1).padStart(2, '0');

  return (
    <div
      className={`trace-span${isCurrentStep ? ' active' : ''}${isPending ? ' pending' : ''}`}
      style={{
        borderLeftColor: isCurrentStep ? pc : 'transparent',
        background: isCurrentStep ? `${pc}08` : undefined,
      }}
    >
      <div className="trace-span-head">
        <span className="trace-span-num">{spanNum}</span>
        <span className="trace-span-icon" style={{ animation: isCurrentStep ? 'stepPulse 1s ease-in-out infinite' : 'none' }}>
          {activityIcon(entry.activity_type)}
        </span>
        <div className="trace-span-titles">
          <div className="trace-span-title" style={{ color: isCurrentStep ? pc : undefined }}>
            {entry.title}
          </div>
          <div className="trace-span-meta">
            <span className="trace-span-agent">{entry.agent_name ?? 'agent'}</span>
            {entry.agent_file && <span className="trace-span-file">{entry.agent_file}</span>}
          </div>
        </div>
        <div className="trace-span-badges">
          {t?.category && (
            <span className={`trace-cat trace-cat-${t.category}`}>{CATEGORY_LABELS[t.category] ?? t.category}</span>
          )}
          {t?.component && <span className="trace-component">{t.component}</span>}
          {t?.status && <span className={`trace-status trace-status-${t.status}`}>{STATUS_LABELS[t.status] ?? t.status}</span>}
        </div>
        {entry.execution_time_ms != null ? (
          <span className="activity-time">{entry.execution_time_ms}ms</span>
        ) : isCurrentStep ? (
          <span className="activity-time" style={{ color: pc }}>...</span>
        ) : isPending ? (
          <span className="activity-time">—</span>
        ) : null}
      </div>

      {t?.memory?.shortTerm && (
        <div className="trace-memory-block trace-memory-short">
          <div className="trace-memory-label">{t.memory.shortTerm.label}</div>
          <ul className="trace-memory-list">
            {t.memory.shortTerm.items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {t?.memory?.longTerm && (
        <div className="trace-memory-block trace-memory-long">
          <div className="trace-memory-label">{t.memory.longTerm.label}</div>
          <table className="trace-facts-table">
            <thead>
              <tr>
                <th>Key</th>
                <th>Value</th>
                <th>Source</th>
                <th>Conf.</th>
              </tr>
            </thead>
            <tbody>
              {t.memory.longTerm.facts.map((f) => (
                <tr key={f.key}>
                  <td>{f.key}</td>
                  <td>{f.value}</td>
                  <td>{f.source ?? '—'}</td>
                  <td>{f.confidence != null ? `${(f.confidence * 100).toFixed(0)}%` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {t?.fields && t.fields.length > 0 && (
        <div className="trace-fields">
          {t.fields.map((f) => (
            <div key={f.label} className="trace-field">
              <span className="trace-field-label">{f.label}</span>
              <span className={`trace-field-value${f.mono ? ' mono' : ''}`}>{f.value}</span>
            </div>
          ))}
        </div>
      )}

      {t?.tokens && (
        <div className="trace-tokens">
          {t.tokens.input != null && <span>in {t.tokens.input.toLocaleString()} tok</span>}
          {t.tokens.output != null && <span>out {t.tokens.output.toLocaleString()} tok</span>}
        </div>
      )}

      {entry.details && !t?.memory && (
        <div className="activity-detail" style={{ borderLeftColor: `${pc}30` }}>
          {entry.details}
        </div>
      )}

      {entry.sql_query && (
        <pre className="trace-sql">{entry.sql_query}</pre>
      )}
    </div>
  );
}
