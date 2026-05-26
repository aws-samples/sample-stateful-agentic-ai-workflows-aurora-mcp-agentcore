/**
 * StageTopBar — keynote brand bar, live system badges, phase + trace id.
 */
import { MeridianMark } from '../../components/MeridianMark';
import type { StageSystemId } from '../types';

const SYSTEMS: { id: StageSystemId | 'aurora_pg' | 'pgvector' | 'bedrock' | 'strands' | 'langgraph'; label: string; matches: StageSystemId | null }[] = [
  { id: 'aurora_pg', label: 'Aurora PostgreSQL', matches: 'aurora' },
  { id: 'pgvector', label: 'pgvector', matches: 'mcp' },
  { id: 'mcp', label: 'MCP', matches: 'mcp' },
  { id: 'bedrock', label: 'Bedrock', matches: 'model' },
  { id: 'strands', label: 'Strands', matches: 'orchestration' },
  { id: 'langgraph', label: 'LangGraph', matches: 'orchestration' },
];

interface StageTopBarProps {
  phaseLabel: string;
  traceId: string;
  activeSystem: StageSystemId | null;
}

export function StageTopBar({ phaseLabel, traceId, activeSystem }: StageTopBarProps) {
  return (
    <header className="ds-topbar" role="banner">
      <div className="ds-brand">
        <MeridianMark variant="stage" />
        <div className="ds-brand-text">
          <span className="ds-brand-name">Meridian Demo Stage</span>
          <span className="ds-brand-sub">Build agentic workflows with Aurora and MCP</span>
        </div>
      </div>

      <nav className="ds-systems" aria-label="Live system stack">
        {SYSTEMS.map((s) => {
          const active = s.matches != null && s.matches === activeSystem;
          return (
            <span
              key={s.id}
              className={`ds-system-chip${active ? ' is-active' : ''}`}
              data-system={s.matches ?? ''}
              role="status"
            >
              <span className="ds-system-dot" aria-hidden="true" />
              {s.label}
            </span>
          );
        })}
      </nav>

      <div className="ds-status-bar">
        <span className="ds-live-pill" aria-label="Live demo phase">
          <span className="ds-live-dot" aria-hidden="true" />
          {phaseLabel}
        </span>
        <span className="ds-trace-id" aria-label="Trace identifier">
          <span>trace</span>
          {traceId}
        </span>
      </div>
    </header>
  );
}
