import { FadeIn } from '../components/FadeIn';
import { SHOWCASE_PHASES, type ShowcasePhaseOption } from '../showcase/lib/showcaseAdapters';

type PhaseMatrixRow = {
  phase: ShowcasePhaseOption;
  primaryAgent: string;
  specialists: string;
  tools: string;
  skills: string;
};

const PHASE_MATRIX: PhaseMatrixRow[] = [
  {
    phase: SHOWCASE_PHASES[0],
    primaryAgent: 'SQLAgent',
    specialists: '—',
    tools: 'run_sql',
    skills: 'sql_filter',
  },
  {
    phase: SHOWCASE_PHASES[1],
    primaryAgent: 'MCPAgent',
    specialists: '—',
    tools: 'postgres.run_query',
    skills: 'run_query',
  },
  {
    phase: SHOWCASE_PHASES[2],
    primaryAgent: 'RetrievalAgent',
    specialists: 'SearchAgent, PackageAgent, BookingAgent',
    tools: 'trips.hybrid_search, availability.lookup, bookings.hold',
    skills: '_hybrid_search_tool, _check_availability_tool, _process_booking_tool',
  },
  {
    phase: SHOWCASE_PHASES[3],
    primaryAgent: 'ProductionAgent',
    specialists: 'MemoryAgent, RetrievalAgent',
    tools: 'memory.recall, memory.write_fact, tools/call(semantic_trip_search)',
    skills:
      'runtime_session, gateway_search, recall_session_context, recall_traveler_preferences, recall_similar_interactions, persist_turn',
  },
  {
    phase: SHOWCASE_PHASES[4],
    primaryAgent: 'OrchestrationAgent',
    specialists: 'SearchAgent, PackageAgent',
    tools: 'LangGraph StateGraph + PostgresSaver checkpoints',
    skills: 'classify, checkpoint, synthesize',
  },
];

export function PhaseMatrixSection() {
  return (
    <section id="phase-matrix" className="mp-section">
      <FadeIn>
        <div className="mp-section-h">
          <div className="mp-label-row">Quick reference · mode map</div>
          <h2>Five phases, one capability ladder.</h2>
          <p>
            Keep this as your presenter cheat sheet: each row maps the audience takeaway to the
            exact agent, specialist, tool, and proof point used in the demo.
          </p>
        </div>
      </FadeIn>

      <FadeIn delay={0.06}>
        <div className="mp-phase-matrix-wrap">
          <table className="mp-phase-matrix" aria-label="Phase agent tool skill matrix">
            <thead>
              <tr>
                <th>Mode</th>
                <th>What changed</th>
                <th>Audience takeaway</th>
                <th>Proof point</th>
                <th>Primary agent</th>
                <th>Specialists</th>
                <th>Tools</th>
                <th>Skills</th>
              </tr>
            </thead>
            <tbody>
              {PHASE_MATRIX.map((row) => (
                <tr key={row.phase.label}>
                  <td>{row.phase.label}</td>
                  <td>
                    <b>{row.phase.capability}</b>
                    <span>{row.phase.adds ?? row.phase.description}</span>
                  </td>
                  <td>{row.phase.takeaway}</td>
                  <td>
                    <code>{row.phase.proofPoint}</code>
                  </td>
                  <td>{row.primaryAgent}</td>
                  <td>{row.specialists}</td>
                  <td>{row.tools}</td>
                  <td>{row.skills}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </FadeIn>

      <FadeIn delay={0.12}>
        <div className="mp-phase-compare" aria-label="Same goal across five maturity levels">
          <div className="mp-phase-compare-head">
            <span>Closing comparison</span>
            <strong>Same user goal, five maturity levels.</strong>
          </div>
          <div className="mp-phase-compare-grid">
            {SHOWCASE_PHASES.map((phase) => (
              <div key={phase.label} className="mp-phase-compare-item">
                <div>
                  <span>{phase.label}</span>
                  <b>{phase.capability}</b>
                </div>
                <p>{phase.takeaway}</p>
                <code>{phase.proofPoint}</code>
              </div>
            ))}
          </div>
        </div>
      </FadeIn>
    </section>
  );
}
