import { ArrowRight, ChevronDown } from 'lucide-react';
import type { Phase } from '../../types';
import { SHOWCASE_PHASES } from '../lib/showcaseAdapters';

const BRIEFS: Record<Phase, {
  title: string;
  description: string;
  route: string[];
  evidence: string;
  pattern: string;
  callout: string;
}> = {
  1: {
    title: 'Query live trip data.',
    description: 'Find trips by price, destination, and trip type in Aurora.',
    route: ['Traveler request', 'SQL filters', 'Aurora rows'],
    evidence: 'Executed SQL and returned rows',
    pattern: 'SQL parameters carry the filter values. Aurora returns the matching catalog rows.',
    callout: 'Here, the price limit is per traveler. The business meaning comes before the query.',
  },
  2: {
    title: 'Give agents tools they can reuse.',
    description: 'Search, compare trips, and convert prices through named tools.',
    route: ['Agent request', 'MCP tool contract', 'Aurora rows'],
    evidence: 'Tool name, inputs, and result',
    pattern: 'An MCP contract defines each tool’s inputs and result. The local app calls MCP servers over stdio.',
    callout: 'A named tool still needs authorization and validated inputs. FX rates here are illustrative.',
  },
  3: {
    title: 'Find trips by meaning.',
    description: 'Search by meaning and keywords, then put the best matches first.',
    route: ['pgvector + full-text search', 'Reranking', 'Relevant trips'],
    evidence: 'Candidate scores and rerank order',
    pattern: 'pgvector finds similar descriptions; PostgreSQL full-text search finds words. Cohere reranks the combined candidates.',
    callout: 'Relevance finds candidates. Current price, availability and access still need checking.',
  },
  4: {
    title: 'Remember the traveler. Govern the action.',
    description: 'Use saved preferences only after checking who may access them, and let policy decide every tool call.',
    route: ['Workload identity', 'Runtime + Gateway tools', 'Cedar decision', 'RLS write + audit'],
    evidence: 'Traveler authorization, Cedar allow or deny, hold row, and audit event',
    pattern: 'Workload identity identifies the agent. A traveler grant allows access. The agent calls tools through the gateway, Cedar policy sees the arguments before code runs, RLS limits the rows, and an audit record captures each decision.',
    callout: 'The application pins traveler, confirmation and budget. The model cannot grant itself authority.',
  },
  5: {
    title: 'Resume work after interruption.',
    description: 'Save the current step in Aurora so another worker can pick up where it stopped.',
    route: ['Recovery request', 'Saved step in Aurora', 'Resume + verify'],
    evidence: 'Saved step, execution attempts, request ID, booking ID and original expiry',
    pattern: 'A Strands Graph in its own AgentCore Runtime saves each step as a snapshot in AWS Aurora. A worker lease prevents competing runs; a stable hold request ID prevents duplicate holds on retry.',
    callout: 'A lost reply can follow a committed hold. Reuse the saved intent and verify the original receipt.',
  },
};

export function CapabilityBrief({ phase }: { phase: Phase }) {
  const current = SHOWCASE_PHASES.find(item => item.phase === phase)!;
  const brief = BRIEFS[phase];
  return (
    <section className="mc-capability-brief" aria-label={`${current.label} capability overview`}>
      <h1>{brief.title}</h1>
      <p className="mc-capability-description">{brief.description}</p>
      <p className="mc-capability-callout">{brief.callout}</p>
      <details className="mc-capability-details">
        <summary>Architecture &amp; evidence <ChevronDown size={17} aria-hidden="true" /></summary>
        <ol className="mc-capability-route" aria-label="Request path">
          {brief.route.map((step, index) => (
            <li key={step}><span>{step}</span>{index < brief.route.length - 1 && <ArrowRight size={18} aria-hidden="true" />}</li>
          ))}
        </ol>
        <dl className="mc-capability-facts">
          <div><dt>How it works</dt><dd>{brief.pattern}</dd></div>
          <div><dt>Technology</dt><dd>{current.tech}</dd></div>
          <div><dt>Verify in the trace</dt><dd>{brief.evidence}</dd></div>
        </dl>
      </details>
    </section>
  );
}
