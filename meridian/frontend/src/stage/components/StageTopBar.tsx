/**
 * StageTopBar - brand bar, stack context, selected phase, and recorded trace.
 */
import { MeridianMark } from '../../components/MeridianMark';
import type { StageSystemId } from '../types';

const SYSTEMS: { id: StageSystemId | 'aurora_pg' | 'pgvector' | 'bedrock' | 'agentcore' | 'strands' | 'langgraph'; label: string; matches: StageSystemId | null }[] = [
  { id: 'aurora_pg', label: 'Aurora PostgreSQL', matches: 'aurora' },
  { id: 'agentcore', label: 'AgentCore', matches: 'orchestration' },
  { id: 'pgvector', label: 'pgvector', matches: 'mcp' },
  { id: 'mcp', label: 'MCP', matches: 'mcp' },
  { id: 'bedrock', label: 'Bedrock', matches: 'model' },
  { id: 'strands', label: 'Strands', matches: 'orchestration' },
  { id: 'langgraph', label: 'LangGraph', matches: 'orchestration' },
];

interface StageTopBarProps {
  phaseLabel: string;
  traceId: string;
  traceStatus: string;
}

export function StageTopBar({ phaseLabel, traceId, traceStatus }: StageTopBarProps) {
  return (
    <header className="ds-topbar" role="banner">
      <div className="ds-brand">
        <MeridianMark variant="stage" />
        <div className="ds-brand-text">
          <span className="ds-brand-name">Meridian Demo Stage</span>
          <span className="ds-brand-sub">Stateful workflows with Aurora, MCP, and AgentCore</span>
        </div>
      </div>

      {/* The stack this demo is built on. These are context, not a live
          readout - kept calm and uniform so a single span-driven chip never
          "dances" alone. The trace status distinguishes loading, failure,
          and playback of a recorded response. */}
      <nav className="ds-systems" aria-label="System stack">
        {SYSTEMS.map((s) => (
          <span key={s.id} className="ds-system-chip" data-system={s.matches ?? ''}>
            <span className="ds-system-dot" aria-hidden="true" />
            {s.label}
          </span>
        ))}
      </nav>

      <div className="ds-status-bar">
        <span className="ds-live-pill" aria-label="Selected demo phase">
          {phaseLabel}
        </span>
        <span className="ds-trace-id" aria-label="Trace status" role="status">
          <span>{traceStatus}</span>
          {traceId}
        </span>
      </div>
    </header>
  );
}
