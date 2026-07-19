/**
 * HowItWorksSection — Meridian Pro Journey rail
 *
 * Five-stop journey (Query → Tool → Intent → Trust → Durable Workflow)
 * with done / live / next states.
 */
import { FadeIn } from '../components/FadeIn';
import { useAgentBridge } from '../context/AgentBridge';
import { PHASE_JOURNEY_SUB } from '../lib/phaseLabels';
import type { Phase } from '../types';

interface JourneyStep {
  num: string;
  ph: string;
  title: string;
  serif: string;
  desc: string;
  chips: string[];
  scale: string;
  persona: string;
  skills: string[];
}

const steps: JourneyStep[] = [
  {
    num: '01',
    ph: PHASE_JOURNEY_SUB[1],
    title: 'SQL',
    serif: '',
    desc: 'The lab. Direct RDS Data API. Fast for exact matches — and it breaks when the user asks for comparison plus currency conversion.',
    chips: ['RDS Data API', 'SQL · WHERE'],
    scale: '~50 trips/day · small team, one ops console',
    persona: 'Alex asks for city trips under $2,000 — a SQL WHERE clause returns matching rows. Comparing categories and converting every price to euros needs domain tools SQL does not own.',
    skills: ['sql_filter'],
  },
  {
    num: '02',
    ph: PHASE_JOURNEY_SUB[2],
    title: 'MCP',
    serif: '',
    desc: 'Two MCP servers in one turn. postgres-mcp wraps the catalog; meridian-concierge adds domain tools (compare_packages, currency_convert, seasonal_price_band). Typed schemas, IAM auth — but intent still loses.',
    chips: ['postgres-mcp', 'meridian-concierge', 'tool registry'],
    scale: '~500 trips/day · booking, pricing, and support agents share one catalog',
    persona: 'A cross-category comparison in euros routes to compare_packages + currency_convert. Tokyo’s November range hits seasonal_price_band — but a slow wine-country prompt still has nothing literal to keyword on.',
    skills: ['run_query', 'compare_packages', 'currency_convert', 'seasonal_price_band'],
  },
  {
    num: '03',
    ph: PHASE_JOURNEY_SUB[3],
    title: 'Retrieval',
    serif: '',
    desc: 'Where intent finally lands. Cohere Embed v4 + hybrid pgvector + tsvector + Cohere Rerank 3.5. Strands supervisor delegates to SearchAgent / PackageAgent. Memory recall remains out of scope — that gap motivates Production.',
    chips: ['pgvector HNSW', 'tsvector', 'Cohere v4', 'Cohere Rerank', 'Strands supervisor'],
    scale: '~5,000 trips/day · customer-facing natural language',
    persona: 'Alex asks for a slow, romantic wine-country week. pgvector + Cohere Rerank surface Tuscany Wine & Wellness. Then Alex asks what was decided last time — Retrieval honestly says it has no prior-turn memory.',
    skills: ['hybrid_search', 'availability', 'rerank'],
  },
  {
    num: '04',
    ph: PHASE_JOURNEY_SUB[4],
    title: 'Production',
    serif: '',
    desc: 'The production concierge: AgentCore Runtime hosts the session, Gateway serves MCP tools, Memory mirrors events, and Aurora RLS scopes every query. Alex\'s Tokyo Oct 12-19 thread, shellfish allergy, and boutique-over-chain preference live in traveler_preferences — not in the prompt.',
    chips: ['AgentCore Runtime', 'AgentCore Gateway', 'AgentCore Memory', 'Aurora RLS'],
    scale: '~50,000 trips/day · returning travelers expect to be known',
    persona: 'Alex asks "Family-friendly beach + snorkeling," then "Pick up where we left off." The recall lands because conversation_messages and trip_interactions sit behind Aurora RLS for trv_meridian_demo.',
    skills: ['recall_session', 'recall_preferences', 'recall_similar_interactions', 'persist_turn'],
  },
  {
    num: '05',
    ph: PHASE_JOURNEY_SUB[5],
    title: 'Workflow',
    serif: '',
    desc: 'LangGraph owns control flow when we want it inspectable, branchable, resumable. Explicit StateGraph (classify → search → availability → memory_recall → synthesize) with PostgresSaver checkpoints in Aurora. Production chained three jobs implicitly inside one Bedrock turn — Workflow routes the same query through named nodes so each step is debuggable and resumable.',
    chips: ['LangGraph', 'StateGraph', 'PostgresSaver', 'AgentCore'],
    scale: '~500,000 trips/day · multi-step workflows that span weeks',
    persona: 'Alex asks to plan the Kyoto extension by finding packages, then verifying duration availability. Each dependent step lands in its own checkpointed node.',
    skills: ['classify', 'search', 'availability', 'memory_recall', 'synthesize'],
  },
];

const STEP_PHASE: Record<string, Phase> = {
  '01': 1,
  '02': 2,
  '03': 3,
  '04': 4,
  '05': 5,
};

function stateFor(stepPhase: Phase, currentPhase: Phase): 'done' | 'live' | 'next' {
  if (stepPhase === currentPhase) return 'live';
  if (stepPhase < currentPhase) return 'done';
  return 'next';
}

export function HowItWorksSection() {
  const { openConcierge, phase: currentPhase } = useAgentBridge();

  return (
    <section id="howitworks" className="mp-section">
      <FadeIn>
        <div className="mp-section-h-row">
          <div className="mp-section-h">
            <div className="mp-label-row">Five phases</div>
            <h2>Query → Tool → Intent → Trust → Durable Workflow.</h2>
            <p>
              Five steps on one Aurora catalog — each phase adds capability without throwing away
              the last. SQL grounds the query, MCP standardizes the tool contract, retrieval captures
              intent, production adds memory and trust, and LangGraph makes multi-step work durable.
            </p>
          </div>
          <div className="actions">
            <button
              type="button"
              className="mp-btn ghost sm"
              onClick={() => openConcierge({ phase: 1, clear: true, focus: true })}
            >
              Compare modes
            </button>
            <button
              type="button"
              className="mp-btn primary sm"
              onClick={() => openConcierge({ phase: 4, focus: true })}
            >
              Open console ↗
            </button>
          </div>
        </div>
      </FadeIn>

      <FadeIn delay={0.1}>
        <div className="mp-journey">
          <div className="mp-journey-rail">
            {steps.map((s) => {
              const stepPhase = STEP_PHASE[s.num];
              const state = stateFor(stepPhase, currentPhase);
              return (
              <button
                key={s.num}
                type="button"
                className={`mp-journey-step mp-fancy-panel ${state}`}
                aria-current={state === 'live' ? 'step' : undefined}
                onClick={() =>
                  openConcierge({ phase: stepPhase, focus: true })
                }
              >
                <span className="ph">{s.ph}</span>
                <div className="node">{s.num.slice(-1)}</div>
                <div className="ttl">{s.title}</div>
                <div className="desc">{s.desc}</div>
                <div className="mp-journey-arc mp-fancy-inset">
                  <div className="arc-row">
                    <span className="arc-label">At this scale</span>
                    <span className="arc-text">{s.scale}</span>
                  </div>
                  <div className="arc-row">
                    <span className="arc-label">Alex Morgan</span>
                    <span className="arc-text">{s.persona}</span>
                  </div>
                  <div className="arc-row">
                    <span className="arc-label">Skills</span>
                    <span className="arc-skills">
                      {s.skills.map((sk) => (
                        <code key={sk} className="arc-skill">{sk}</code>
                      ))}
                    </span>
                  </div>
                </div>
                <div className="stack">
                  {s.chips.map((c) => (
                    <span key={c} className="chip">
                      {c}
                    </span>
                  ))}
                </div>
              </button>
            );
            })}
          </div>
        </div>
      </FadeIn>
    </section>
  );
}
