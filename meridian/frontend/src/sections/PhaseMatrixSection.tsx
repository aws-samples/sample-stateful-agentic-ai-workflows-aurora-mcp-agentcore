import { FadeIn } from '../components/FadeIn';

type PhaseMatrixRow = {
  phase: string;
  mode: string;
  primaryAgent: string;
  specialists: string;
  tools: string;
  skills: string;
};

const PHASE_MATRIX: PhaseMatrixRow[] = [
  {
    phase: 'Phase 1',
    mode: 'SQL',
    primaryAgent: 'SQLAgent',
    specialists: '—',
    tools: 'run_sql',
    skills: 'sql_filter',
  },
  {
    phase: 'Phase 2',
    mode: 'MCP',
    primaryAgent: 'MCPAgent',
    specialists: '—',
    tools: 'postgres.run_query',
    skills: 'run_query',
  },
  {
    phase: 'Phase 3',
    mode: 'Retrieval',
    primaryAgent: 'RetrievalAgent',
    specialists: 'SearchAgent, PackageAgent, BookingAgent',
    tools: 'trips.hybrid_search, availability.lookup, bookings.hold',
    skills: '_semantic_search_tool, _check_availability_tool, _process_booking_tool',
  },
  {
    phase: 'Phase 4',
    mode: 'Production',
    primaryAgent: 'ProductionAgent',
    specialists: 'MemoryAgent, RetrievalAgent',
    tools: 'memory.recall, memory.write_fact, tools/call(semantic_trip_search)',
    skills:
      'runtime_session, gateway_search, recall_session_context, recall_traveler_preferences, recall_similar_interactions, persist_turn',
  },
  {
    phase: 'Phase 5',
    mode: 'Orchestration',
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
          <div className="mp-label-row">Quick reference · phase map</div>
          <h2>Agents, tools, and skills by phase.</h2>
          <p>
            Keep this as your presenter cheat sheet: each row maps one phase to the exact agent,
            specialist, tool, and skill names used across the demo.
          </p>
        </div>
      </FadeIn>

      <FadeIn delay={0.06}>
        <div className="mp-phase-matrix-wrap">
          <table className="mp-phase-matrix" aria-label="Phase agent tool skill matrix">
            <thead>
              <tr>
                <th>Phase</th>
                <th>Mode</th>
                <th>Primary agent</th>
                <th>Specialists</th>
                <th>Tools</th>
                <th>Skills</th>
              </tr>
            </thead>
            <tbody>
              {PHASE_MATRIX.map((row) => (
                <tr key={row.phase}>
                  <td>{row.phase}</td>
                  <td>{row.mode}</td>
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
    </section>
  );
}
