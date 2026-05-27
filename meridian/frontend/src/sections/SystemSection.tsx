/**
 * SystemSection — Meridian Pro substrate panel
 *
 * Two side-by-side surfaces: Aurora schema map + MCP tool catalog. Each tool
 * row has a `dry-run` action that opens a local drawer with sample input /
 * output (no backend round-trip), plus an "ask concierge" link that sends a
 * scripted prompt into the live workspace.
 */
import { useEffect, useState } from 'react';
import { FadeIn } from '../components/FadeIn';
import { useAgentBridge } from '../context/AgentBridge';
import { MCP_TOOL_CATALOG, type McpToolEntry } from '../lib/proDemoData';

interface SchemaTable {
  name: string;
  pk?: boolean;
  cols: { name: string; type: string; emphasis?: boolean }[];
  group: 'retrieval' | 'identity' | 'memory' | 'conversation';
}

const tables: SchemaTable[] = [
  {
    name: 'trip_packages',
    pk: true,
    group: 'retrieval',
    cols: [
      { name: 'package_id', type: 'varchar(50)', emphasis: true },
      { name: 'name', type: 'text' },
      { name: 'trip_type', type: 'text' },
      { name: 'destination', type: 'text' },
      { name: 'price_per_person', type: 'decimal' },
      { name: 'durations', type: 'jsonb' },
      { name: 'availability', type: 'jsonb' },
      { name: 'embedding', type: 'vector(1024)' },
      { name: 'search_vector', type: 'tsvector' },
    ],
  },
  {
    name: 'travelers',
    pk: true,
    group: 'identity',
    cols: [
      { name: 'traveler_id', type: 'varchar(50)', emphasis: true },
      { name: 'full_name', type: 'text' },
      { name: 'email', type: 'text' },
      { name: 'home_airport', type: 'text' },
      { name: 'created_at', type: 'ts' },
    ],
  },
  {
    name: 'traveler_preferences',
    group: 'memory',
    cols: [
      { name: 'preference_id', type: 'varchar(50)', emphasis: true },
      { name: 'traveler_id', type: 'fk' },
      { name: 'preference_type', type: 'text' },
      { name: 'preference_key', type: 'text' },
      { name: 'preference_value', type: 'text' },
      { name: 'confidence', type: 'float' },
      { name: 'source', type: 'text' },
    ],
  },
  {
    name: 'conversations',
    pk: true,
    group: 'conversation',
    cols: [
      { name: 'conversation_id', type: 'varchar(50)', emphasis: true },
      { name: 'traveler_id', type: 'fk' },
      { name: 'started_at', type: 'ts' },
      { name: 'last_message_at', type: 'ts' },
      { name: 'summary', type: 'text' },
    ],
  },
  {
    name: 'conversation_messages',
    group: 'conversation',
    cols: [
      { name: 'message_id', type: 'varchar(50)', emphasis: true },
      { name: 'conversation_id', type: 'fk' },
      { name: 'role', type: 'text' },
      { name: 'content', type: 'text' },
      { name: 'embedding', type: 'vector(1024)' },
    ],
  },
  {
    name: 'trip_interactions',
    group: 'memory',
    cols: [
      { name: 'interaction_id', type: 'varchar(50)', emphasis: true },
      { name: 'traveler_id', type: 'fk' },
      { name: 'conversation_id', type: 'fk' },
      { name: 'query_text', type: 'text' },
      { name: 'packages_shown', type: 'jsonb' },
      { name: 'embedding', type: 'vector(1024)' },
    ],
  },
  {
    name: 'bookings',
    pk: true,
    group: 'retrieval',
    cols: [
      { name: 'booking_id', type: 'varchar(50)', emphasis: true },
      { name: 'traveler_id', type: 'fk' },
      { name: 'status', type: 'text' },
      { name: 'total_amount', type: 'decimal' },
      { name: 'confirmed_at', type: 'ts' },
    ],
  },
  {
    name: 'booking_lines',
    group: 'retrieval',
    cols: [
      { name: 'line_id', type: 'serial', emphasis: true },
      { name: 'booking_id', type: 'fk' },
      { name: 'package_id', type: 'fk' },
      { name: 'duration', type: 'text' },
      { name: 'travelers_count', type: 'int' },
      { name: 'unit_price', type: 'decimal' },
    ],
  },
  {
    name: 'agent_traces',
    pk: true,
    group: 'conversation',
    cols: [
      { name: 'trace_id', type: 'varchar(50)', emphasis: true },
      { name: 'conversation_id', type: 'fk' },
      { name: 'agent_name', type: 'text' },
      { name: 'phase', type: 'int' },
      { name: 'total_latency_ms', type: 'int' },
    ],
  },
];

const tools = MCP_TOOL_CATALOG;

const SCHEMA_URL =
  'https://github.com/aws-samples/sample-dat309-agentic-workflows-aurora-mcp/blob/main/meridian/backend/db/schema.sql';

const DRY_RUN_PROMPTS: Record<string, string> = {
  'postgres.run_query': 'Dry-run: list 3 trip_packages in City Breaks under $3000',
  'trips.hybrid_search': 'Dry-run hybrid search: slow wine country week in Europe',
  'memory.recall': 'Dry-run memory recall for our Tokyo trip preferences',
  'memory.write_fact': 'Dry-run: remember we prefer boutique hotels over chains',
  'availability.lookup': 'Dry-run availability for Maldives package next month',
  'bookings.hold': 'Dry-run hold on Tuscan Vineyards package for two travelers',
  'claude.compose': 'Dry-run compose a short trip summary for Alex & Jordan',
};

function DryRunDrawer({
  tool,
  onClose,
  onSendToConcierge,
}: {
  tool: McpToolEntry;
  onClose: () => void;
  onSendToConcierge: (tool: McpToolEntry) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  return (
    <div className="mp-dry-backdrop" role="presentation" onClick={onClose}>
      <aside
        className="mp-dry-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`Dry-run ${tool.name}`}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mp-dry-head">
          <div>
            <div className="mp-dry-eyebrow">Dry-run · no backend call</div>
            <div className="mp-dry-title">{tool.name}</div>
            <div className="mp-dry-sub">
              {tool.sub} · {tool.ver} · p50 {tool.p50}
            </div>
          </div>
          <button
            type="button"
            className="mp-dry-close"
            onClick={onClose}
            aria-label="Close dry-run drawer"
          >
            ×
          </button>
        </header>
        <div className="mp-dry-body">
          <div className="mp-dry-section">
            <h4>Sample input</h4>
            <pre className="mp-dry-code">{tool.sampleInput}</pre>
          </div>
          <div className="mp-dry-section">
            <h4>Sample output</h4>
            <pre className="mp-dry-code">{tool.sampleOutput}</pre>
          </div>
          <div className="mp-dry-footer">
            <button
              type="button"
              className="mp-btn primary sm"
              onClick={() => onSendToConcierge(tool)}
            >
              Run live in concierge →
            </button>
            <span className="mp-dry-hint">
              Dry-runs are UI-only. Live runs hit the real MCP tool through the workspace.
            </span>
          </div>
        </div>
      </aside>
    </div>
  );
}

export function SystemSection() {
  const { openConcierge } = useAgentBridge();
  const [dryRunTool, setDryRunTool] = useState<McpToolEntry | null>(null);

  const runLive = (t: McpToolEntry) => {
    setDryRunTool(null);
    openConcierge({
      phase: t.name.startsWith('memory') ? 4 : t.name.includes('hybrid') ? 3 : 2,
      prompt: DRY_RUN_PROMPTS[t.name] ?? `Dry-run ${t.name}`,
      send: true,
    });
  };

  return (
    <section id="system" className="mp-section">
      <FadeIn>
        <div className="mp-section-h-row">
          <div className="mp-section-h">
            <div className="mp-label-row">Substrate · Aurora + MCP</div>
            <h2>The substrate, made legible.</h2>
            <p>
              Two views every reviewer wants: the Aurora schema that powers retrieval and memory,
              and the MCP tool catalog the agents can call. Both shipped as first-class surfaces —
              not an afterthought in a docs page.
            </p>
          </div>
          <div className="actions">
            <a className="mp-btn ghost sm" href={SCHEMA_URL} target="_blank" rel="noreferrer">
              Open schema.sql
            </a>
            <button
              type="button"
              className="mp-btn ghost sm"
              onClick={() =>
                openConcierge({
                  phase: 2,
                  prompt: 'Show me a dry-run of postgres.run_query for City Breaks under $3000',
                  send: true,
                })
              }
            >
              Try a tool
            </button>
          </div>
        </div>
      </FadeIn>

      <FadeIn delay={0.1}>
        <div className="mp-system">
          <div className="mp-panel">
            <div className="mp-panel-h">
              <h3>Aurora schema</h3>
              <span className="sub">PostgreSQL 17 · pgvector HNSW · Data API</span>
            </div>
            <div className="mp-panel-body">
              <div className="mp-schema">
                {tables.map((t) => (
                  <div key={t.name} className="mp-schema-table">
                    <h6>
                      {t.name} {t.pk && <span className="pk">PK</span>}
                    </h6>
                    <ul>
                      {t.cols.map((c) => (
                        <li key={c.name}>
                          {c.emphasis ? <b>{c.name}</b> : c.name} <span style={{ color: 'var(--mp-dim)' }}>{c.type}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
                <div className="mp-schema-legend">
                  <span><span className="dot" style={{ background: 'var(--mp-accent)' }} /> retrieval</span>
                  <span><span className="dot" style={{ background: 'var(--mp-sky)' }} /> identity</span>
                  <span><span className="dot" style={{ background: 'var(--mp-leaf)' }} /> memory</span>
                  <span><span className="dot" style={{ background: 'var(--mp-plum)' }} /> conversation</span>
                </div>
              </div>
            </div>
          </div>

          <div className="mp-panel">
            <div className="mp-panel-h">
              <h3>MCP tool catalog</h3>
              <span className="sub">{tools.length} tools · awslabs.postgres-mcp-server v3.1</span>
            </div>
            <div className="mp-panel-body">
              <div className="mp-mcp-row head">
                <div>Tool</div>
                <div>Version</div>
                <div>p50</div>
                <div>Health</div>
                <div />
              </div>
              {tools.map((t) => (
                <div key={t.name} className="mp-mcp-row">
                  <div className="nm">
                    {t.name}
                    <small>{t.sub}</small>
                  </div>
                  <div className="ver">{t.ver}</div>
                  <div className="ms">{t.p50}</div>
                  <div>
                    <span className={`health${t.health === 'warn' ? ' warn' : ''}`}>
                      {t.health === 'warn' ? 'degraded' : 'healthy'}
                    </span>
                  </div>
                  <div>
                    <button
                      type="button"
                      className="dry"
                      onClick={() => setDryRunTool(t)}
                      aria-label={`Dry-run ${t.name}`}
                    >
                      dry-run
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </FadeIn>

      {dryRunTool && (
        <DryRunDrawer
          tool={dryRunTool}
          onClose={() => setDryRunTool(null)}
          onSendToConcierge={runLive}
        />
      )}
    </section>
  );
}
