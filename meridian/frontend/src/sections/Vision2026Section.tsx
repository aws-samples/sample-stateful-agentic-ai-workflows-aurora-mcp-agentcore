/**
 * Vision2026Section — session narrative: memory, MCP, AgentCore runtime, orchestration
 */
import { FadeIn } from '../components/FadeIn';
import { TravelerPersona } from '../components/TravelerPersona';
import { useAgentBridge } from '../context/AgentBridge';

const pillars = [
  {
    num: '01',
    title: 'Contextual',
    serif: 'memory',
    desc: 'Short-term turn context in the agent runtime. Long-term facts in Aurora with pgvector — party size, travel dates, dietary needs — recalled before every search.',
    tags: ['Session state', 'memory.facts', 'pgvector recall'],
  },
  {
    num: '02',
    title: 'MCP',
    serif: 'servers',
    desc: 'Model Context Protocol exposes Aurora tools with schemas and IAM auth — so agents never hardcode SQL or connection strings.',
    tags: ['Tool catalog', 'RDS Data API', 'Secure connect'],
  },
  {
    num: '03',
    title: 'Agent',
    serif: 'runtime',
    desc: 'Bedrock AgentCore hosts durable, governed execution. Strands Agents wire supervisor delegation and @tool memory to Claude on Bedrock.',
    tags: ['AgentCore', 'Strands SDK', 'Supervisor'],
  },
  {
    num: '04',
    title: 'Multi-agent',
    serif: 'workflows',
    desc: 'Supervisor routes to Search, Availability, Policy, and Booking specialists — multi-turn itineraries that read live package data from Aurora.',
    tags: ['Supervisor', 'Specialists', 'Multi-turn'],
  },
  {
    num: '05',
    title: 'Trace-first',
    serif: 'observability',
    desc: 'Every span is permalinked: agent, tool, SQL, latency, token spend. Replay traces without re-running the LLM.',
    tags: ['agent_traces', 'Replay', 'Permalinks'],
  },
  {
    num: '06',
    title: 'Governed',
    serif: 'autonomy',
    desc: 'Plans surface before commit — budgets, scopes, confirm-before-charge. Autonomy with guardrails on real money and inventory.',
    tags: ['Plan → confirm', 'Scopes', 'Policy agent'],
  },
  {
    num: '07',
    title: 'Identity',
    serif: 'guardrails',
    desc: 'AgentCore Identity scopes each turn with workload credentials so memory, tools, and writes run under explicit least-privilege envelopes.',
    tags: ['Workload identity', 'IAM scoped', 'Least privilege'],
  },
  {
    num: '08',
    title: 'Cost-aware',
    serif: 'execution',
    desc: 'Trace-level token and latency metrics keep each turn explainable and budgetable, with policy gates before expensive downstream actions.',
    tags: ['Token telemetry', 'Latency budget', 'Policy gates'],
  },
  {
    num: '09',
    title: 'Human-in',
    serif: 'the-loop',
    desc: 'Critical actions stay reviewable: plans, holds, and confirmations are surfaced in plain language before any irreversible booking step.',
    tags: ['Review first', 'Confirm before charge', 'Safe automation'],
  },
];

const runtimeStack = [
  { label: 'Orchestration', value: 'Strands supervisor · specialist agents' },
  { label: 'Runtime', value: 'Amazon Bedrock AgentCore' },
  { label: 'Models', value: 'Claude on Bedrock · Cohere Embed v4' },
  { label: 'Data plane', value: 'Aurora PostgreSQL · MCP · RDS Data API' },
];

export function Vision2026Section() {
  const { openConcierge } = useAgentBridge();

  return (
    <section id="vision2026" className="mp-section">
      <FadeIn>
        <div className="mp-section-h" style={{ marginBottom: 40 }}>
          <div className="mp-label-row">Beyond Phase 5 · what's next</div>
          <h2>Memory, runtime, and orchestration.</h2>
          <p style={{ maxWidth: 680 }}>
            Build agentic workflows with Aurora and MCP — contextual memory, multi-turn queries,
            and multi-agent orchestration on Amazon Bedrock AgentCore and Strands Agents.
          </p>
        </div>
      </FadeIn>

      <FadeIn delay={0.05}>
        <div className="runtime-strip mp-fancy-panel">
          {runtimeStack.map((row) => (
            <div key={row.label} className="runtime-strip-row mp-fancy-inset">
              <span className="runtime-strip-label">{row.label}</span>
              <span className="runtime-strip-value">{row.value}</span>
            </div>
          ))}
        </div>
      </FadeIn>

      <FadeIn delay={0.1}>
        <div className="memory-preview-card mp-fancy-panel">
          <div>
            <div className="memory-preview-eyebrow">Wave 01 · Memory of me</div>
            <p className="memory-preview-copy">
              Meet <strong>Alex & Jordan Chen</strong> — demo travelers from SFO planning a Tokyo culture
              trip. Agents recall their party size, dates, and dietary needs from Aurora before routing.
            </p>
          </div>
          <TravelerPersona
            variant="card"
            active={false}
            onActivate={() =>
              openConcierge({
                phase: 4,
                prompt: 'Tokyo trip for two in October — use everything you know about us.',
                send: true,
              })
            }
          />
        </div>
      </FadeIn>

      <div className="mp-vision-grid">
        {pillars.map((p, i) => (
          <FadeIn key={p.num} delay={0.12 + i * 0.04}>
            <article
              className="vision-pillar mp-fancy-panel"
              role="button"
              tabIndex={0}
              onClick={() => {
                if (p.num === '02') {
                  document.getElementById('system')?.scrollIntoView({ behavior: 'smooth' });
                } else {
                  openConcierge({ phase: 4, focus: true });
                }
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  if (p.num === '02') {
                    document.getElementById('system')?.scrollIntoView({ behavior: 'smooth' });
                  } else {
                    openConcierge({ phase: 4, focus: true });
                  }
                }
              }}
            >
              <span className="vision-pillar-num">Pillar {p.num}</span>
              <h3 className="vision-pillar-title">
                {p.title} <em className="serif">{p.serif}</em>
              </h3>
              <p className="vision-pillar-desc">{p.desc}</p>
              <div className="vision-pillar-tags">
                {p.tags.map((t) => (
                  <span key={t} className="phase-tag">
                    {t}
                  </span>
                ))}
              </div>
            </article>
          </FadeIn>
        ))}
      </div>

      <FadeIn delay={0.35}>
        <p className="vision-footnote">
          From 50 trips/day to 500,000 — same Aurora, same agent, five rungs higher. Each rung
          composes onto the last; nothing is rewritten. 10,000× the throughput, two founders, no
          new database. The ladder, not a separate product.
        </p>
      </FadeIn>
    </section>
  );
}
