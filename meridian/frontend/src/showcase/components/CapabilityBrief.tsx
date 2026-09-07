import { ArrowRight, ChevronDown } from 'lucide-react';
import type { Phase } from '../../types';
import { SHOWCASE_PHASES } from '../lib/showcaseAdapters';

const BRIEFS: Record<Phase, {
  title: string;
  description: string;
  route: string[];
  evidence: string;
}> = {
  1: {
    title: 'Query live trip data.',
    description: 'Answer concrete requests with filters over the Aurora trip catalog.',
    route: ['Traveler request', 'SQL filters', 'Aurora rows'],
    evidence: 'Executed SQL and returned rows',
  },
  2: {
    title: 'Give agents a tool contract.',
    description: 'Reuse named operations for search, comparison, and pricing.',
    route: ['Agent request', 'MCP tool contract', 'Aurora rows'],
    evidence: 'Tool name, inputs, and result',
  },
  3: {
    title: 'Find trips by meaning.',
    description: 'Combine semantic and keyword search, then rerank the strongest matches.',
    route: ['pgvector + full-text search', 'Reranking', 'Relevant trips'],
    evidence: 'Candidate scores and rerank order',
  },
  4: {
    title: 'Remember the traveler. Control access.',
    description: 'Carry traveler context across turns, with explicit authorization and row-level security.',
    route: ['Workload identity', 'Traveler context + RLS', 'Personalized response'],
    evidence: 'Traveler authorization, row scope, and audit event',
  },
  5: {
    title: 'Resume work after interruption.',
    description: 'Save execution state in Aurora so another worker can continue the same journey.',
    route: ['Recovery request', 'Aurora checkpoint', 'Resume + verify'],
    evidence: 'Checkpoint, thread ID, and resume result',
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
          <div><dt>Technology</dt><dd>{phase === 5 ? 'LangGraph · Aurora PostgreSQL' : current.tech}</dd></div>
          <div><dt>Verify in the trace</dt><dd>{brief.evidence}</dd></div>
        </dl>
      </details>
    </section>
  );
}
