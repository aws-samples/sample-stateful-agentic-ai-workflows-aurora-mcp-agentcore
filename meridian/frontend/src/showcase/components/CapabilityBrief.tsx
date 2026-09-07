import { ArrowRight, ChevronDown } from 'lucide-react';
import type { Phase } from '../../types';
import { SHOWCASE_PHASES } from '../lib/showcaseAdapters';

const BRIEFS: Record<Phase, {
  title: string;
  description: string;
  route: string[];
  evidence: string;
  pattern: string;
}> = {
  1: {
    title: 'Query live trip data.',
    description: 'Find trips by price, destination, and trip type in Aurora.',
    route: ['Traveler request', 'SQL filters', 'Aurora rows'],
    evidence: 'Executed SQL and returned rows',
    pattern: 'SQL parameters carry the filter values. Aurora returns the matching catalog rows.',
  },
  2: {
    title: 'Give agents tools they can reuse.',
    description: 'Search, compare trips, and convert prices through named tools.',
    route: ['Agent request', 'MCP tool contract', 'Aurora rows'],
    evidence: 'Tool name, inputs, and result',
    pattern: 'An MCP contract defines each tool’s inputs and result. The local demo calls MCP servers over stdio.',
  },
  3: {
    title: 'Find trips by meaning.',
    description: 'Search by meaning and keywords, then put the best matches first.',
    route: ['pgvector + full-text search', 'Reranking', 'Relevant trips'],
    evidence: 'Candidate scores and rerank order',
    pattern: 'pgvector finds similar descriptions; PostgreSQL full-text search finds words. Cohere reranks the combined candidates.',
  },
  4: {
    title: 'Remember the traveler. Control access.',
    description: 'Use saved preferences only after checking who may access them.',
    route: ['Workload identity', 'Traveler context + RLS', 'Personalized response'],
    evidence: 'Traveler authorization, row scope, and audit event',
    pattern: 'Workload identity identifies the agent. A traveler grant allows access, RLS limits the rows, and an audit record captures the decision.',
  },
  5: {
    title: 'Resume work after interruption.',
    description: 'Save the current step in Aurora so another worker can pick up where it stopped.',
    route: ['Recovery request', 'Aurora checkpoint', 'Resume + verify'],
    evidence: 'Checkpoint, thread ID, and resume result',
    pattern: 'LangGraph saves values and the next step. A worker lease prevents competing runs; a stable hold request ID prevents duplicate holds on retry.',
  },
};

export function CapabilityBrief({ phase }: { phase: Phase }) {
  const current = SHOWCASE_PHASES.find(item => item.phase === phase)!;
  const brief = BRIEFS[phase];
  return (
    <section className="mc-capability-brief" aria-label={`${current.label} capability overview`}>
      <h1>{brief.title}</h1>
      <p className="mc-capability-description">{brief.description}</p>
      <details className="mc-capability-details">
        <summary>Architecture &amp; evidence <ChevronDown size={17} aria-hidden="true" /></summary>
        <ol className="mc-capability-route" aria-label="Request path">
          {brief.route.map((step, index) => (
            <li key={step}><span>{step}</span>{index < 2 && <ArrowRight size={18} aria-hidden="true" />}</li>
          ))}
        </ol>
        <dl className="mc-capability-facts">
          <div><dt>How it works</dt><dd>{brief.pattern}</dd></div>
          <div><dt>Technology</dt><dd>{phase === 5 ? 'LangGraph · Aurora PostgreSQL' : current.tech}</dd></div>
          <div><dt>Verify in the trace</dt><dd>{brief.evidence}</dd></div>
        </dl>
      </details>
    </section>
  );
}
