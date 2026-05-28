import { FadeIn } from '../components/FadeIn';

type PhaseMatrixRow = {
  modeLabel: string;
  mode: string;
  primaryAgent: string;
  specialists: string;
  tools: string;
  skills: string;
};

const PHASE_MATRIX: PhaseMatrixRow[] = [
  {
    modeLabel: 'SQL',
    mode: 'SQL',
    primaryAgent: 'SQLAgent',
    specialists: '—',
    tools: 'run_sql',
    skills: 'sql_filter',
  },
  {
    modeLabel: 'MCP',
    mode: 'MCP',
    primaryAgent: 'MCPAgent',
    specialists: '—',
    tools: 'postgres.run_query',
    skills: 'run_query',
  },
  {
    modeLabel: 'Retrieval',
    mode: 'Retrieval',
    primaryAgent: 'RetrievalAgent',
    specialists: 'SearchAgent, PackageAgent, BookingAgent',
    tools: 'trips.hybrid_search, availability.lookup, bookings.hold',
    skills: '_semantic_search_tool, _check_availability_tool, _process_booking_tool',
  },
  {
    modeLabel: 'Production',
    mode: 'Production',
    primaryAgent: 'ProductionAgent',
    specialists: 'MemoryAgent, RetrievalAgent',
    tools: 'memory.recall, memory.write_fact, tools/call(semantic_trip_search)',
    skills:
      'runtime_session, gateway_search, recall_session_context, recall_traveler_preferences, recall_similar_interactions, persist_turn',
  },
  {
    modeLabel: 'Workflow',
    mode: 'Workflow',
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
          <h2>Agents, tools, and skills by mode.</h2>
          <p>
            Keep this as your presenter cheat sheet: each row maps one mode to the exact agent,
            specialist, tool, and skill names used across the demo.
          </p>
        </div>
      </FadeIn>

      <FadeIn delay={0.06}>
        <div className="mp-phase-matrix-wrap">
          <table className="mp-phase-matrix" aria-label="Phase agent tool skill matrix">
            <thead>
              <tr>
                <th>Mode</th>
                <th>Mode</th>
                <th>Primary agent</th>
                <th>Specialists</th>
                <th>Tools</th>
                <th>Skills</th>
              </tr>
            </thead>
            <tbody>
              {PHASE_MATRIX.map((row) => (
                <tr key={row.modeLabel}>
                  <td>{row.modeLabel}</td>
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
