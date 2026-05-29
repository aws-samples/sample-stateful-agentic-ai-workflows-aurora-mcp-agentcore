/**
 * SystemProofRail — right rail with Aurora schema, MCP tool catalog, and
 * governance gates. Each card highlights when the relevant span is active.
 */
import type { StageScenario, StageSpan, StageSystemId } from '../types';

// Real Aurora tables (backend/db/schema.sql). `match` lists substrings we
// look for in the active span's title/detail to decide when a table lights.
const AURORA_TABLES: { name: string; kind: string; match: string[] }[] = [
  { name: 'trip_packages', kind: 'data + vector', match: ['semantic_trip_search', 'trip_packages', 'search', 'hybrid'] },
  { name: 'traveler_preferences', kind: 'long-term memory', match: ['recall_traveler_preferences', 'traveler_preferences', 'preference'] },
  { name: 'trip_interactions', kind: 'semantic recall', match: ['recall_similar_interactions', 'trip_interactions', 'similar'] },
  { name: 'conversation_messages', kind: 'session memory', match: ['recall_session_context', 'conversation_messages', 'session'] },
  { name: 'bookings', kind: 'holds', match: ['booking', 'hold', 'availability'] },
  { name: 'agent_traces', kind: 'observability', match: ['persist_turn', 'audit', 'synthes'] },
];

// Real Phase 4 (Production) operations — the AgentCore Gateway tool plus the
// Strands @tool memory methods the concierge binds. `match` is the substring
// we look for in a span title to decide when this row is the one firing.
// (Names mirror backend/agents/production_04/memory_agent.py + gateway.py.)
const MCP_TOOLS: { name: string; meta: string; match: string[] }[] = [
  { name: 'semantic_trip_search', meta: 'Gateway · pgvector', match: ['semantic_trip_search', 'tools/call', 'tools/list'] },
  { name: 'recall_session_context', meta: 'Strands · session', match: ['recall_session_context'] },
  { name: 'recall_traveler_preferences', meta: 'Strands · long-term', match: ['recall_traveler_preferences'] },
  { name: 'recall_similar_interactions', meta: 'Strands · semantic', match: ['recall_similar_interactions'] },
  { name: 'persist_turn', meta: 'Strands · write-back', match: ['persist_turn'] },
  { name: 'claude.compose', meta: 'Bedrock Opus', match: ['compose', 'claude', 'bedrock', 'concierge polish'] },
];

interface SystemProofRailProps {
  scenario: StageScenario;
  activeSpan: StageSpan | null;
  activeSystem: StageSystemId | null;
}

function ShieldIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M8 1.5L13.5 3.5V8C13.5 11.3137 11.0376 13.7461 8 14.5C4.9624 13.7461 2.5 11.3137 2.5 8V3.5L8 1.5Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M5.5 8.2L7.2 9.9L10.5 6.6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function SystemProofRail({ scenario, activeSpan, activeSystem }: SystemProofRailProps) {
  // Build a searchable haystack from each span (title + detail), so the
  // rail can match the REAL operations the backend emitted rather than
  // guessing from coarse span "kind". A tool/table lights only when its
  // match substrings actually appear in the trace.
  const spanText = (s: { name?: string; detail?: string }): string =>
    `${s.name ?? ''} ${s.detail ?? ''}`.toLowerCase();
  const allSpanText = scenario.spans.map(spanText);
  const activeText = activeSpan ? spanText(activeSpan) : '';

  const matchedAny = (needles: string[], haystacks: string[]): boolean =>
    needles.some((n) => haystacks.some((h) => h.includes(n.toLowerCase())));

  // A tool/table was "called" this turn if any span text matches it.
  const toolCalledThisTurn = (needles: string[]) => matchedAny(needles, allSpanText);
  // …and is "calling" right now if the ACTIVE span matches it.
  const toolCallingNow = (needles: string[]) => activeText !== '' && matchedAny(needles, [activeText]);

  const tableTouchedNow = (needles: string[]) => activeText !== '' && matchedAny(needles, [activeText]);
  const tableTouchedThisTurn = (needles: string[]) => matchedAny(needles, allSpanText);

  return (
    <aside className="ds-rail" aria-label="System proof">
      <section
        className={`ds-rail-card${activeSystem === 'aurora' || activeSystem === 'memory' ? ' is-active' : ''}`}
        data-system={activeSystem === 'memory' ? 'memory' : 'aurora'}
        aria-label="Aurora spine"
      >
        <header className="ds-rail-card-head">
          <div className="ds-rail-card-title">Aurora spine</div>
          <div className="ds-rail-card-sub">tables touched</div>
        </header>
        <div className="ds-aurora-spine">
          {AURORA_TABLES.map((t) => {
            const touchingNow = tableTouchedNow(t.match);
            const touchedTurn = tableTouchedThisTurn(t.match);
            return (
              <div
                key={t.name}
                className={`ds-aurora-table${touchingNow ? ' is-touched' : touchedTurn ? ' is-used' : ''}`}
              >
                <span>{t.name}</span>
                <span>{t.kind}</span>
              </div>
            );
          })}
        </div>
      </section>

      <section
        className={`ds-rail-card${activeSystem === 'mcp' || activeSystem === 'model' ? ' is-active' : ''}`}
        data-system="mcp"
        aria-label="MCP tool catalog"
      >
        <header className="ds-rail-card-head">
          <div className="ds-rail-card-title">Agent tools</div>
          <div className="ds-rail-card-sub">Gateway + Strands</div>
        </header>
        <div>
          {MCP_TOOLS.map((tool) => {
            const calling = toolCallingNow(tool.match);
            const wasCalled = toolCalledThisTurn(tool.match);
            // Three states: actively firing (bright), used this turn (dim
            // highlight), idle (no highlight). So the rail shows exactly
            // which tools ran — not the whole block lighting at once.
            const cls = calling ? ' is-calling' : wasCalled ? ' is-called' : '';
            return (
              <div key={tool.name} className={`ds-mcp-tool${cls}`}>
                <span>{tool.name}</span>
                <span>{tool.meta}</span>
              </div>
            );
          })}
        </div>
      </section>

      <section
        className={`ds-rail-card${activeSystem === 'governance' ? ' is-active' : ''}`}
        data-system="governance"
        aria-label="Governance"
      >
        <header className="ds-rail-card-head">
          <div className="ds-rail-card-title">Governance</div>
          <div className="ds-rail-card-sub">policy gates</div>
        </header>
        <div className="ds-gov">
          <div className="ds-gov-row">
            <ShieldIcon />
            <div>
              <b>{scenario.governance.scope}</b>
              <span>per-traveler scope (live)</span>
            </div>
          </div>
          <div className="ds-gov-row">
            <ShieldIcon />
            <div>
              <b>{scenario.governance.budgetCap}</b>
              <span>RLS enforcement (pattern)</span>
            </div>
          </div>
          <div className="ds-gov-row">
            <ShieldIcon />
            <div>
              <b>{scenario.governance.confirmation}</b>
              <span>workload identity (live)</span>
            </div>
          </div>
          <div className="ds-gov-row">
            <ShieldIcon />
            <div>
              <b>{scenario.governance.audit}</b>
              <span>observability (live)</span>
            </div>
          </div>
        </div>
      </section>
    </aside>
  );
}
