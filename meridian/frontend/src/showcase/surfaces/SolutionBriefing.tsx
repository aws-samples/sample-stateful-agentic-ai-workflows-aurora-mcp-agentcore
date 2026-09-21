import { ArrowRight, ChevronDown } from 'lucide-react';
import type { ReactNode } from 'react';
import { BriefingArchitecture } from './BriefingArchitecture';
import { BriefingPhases, BriefingPreparation } from './BriefingWalkthrough';
import { CONTROLS, PHASES, POLICIES, TOOLS } from './solutionBriefingContent';

function Detail({ title, children }: { title: string; children: ReactNode }) {
  return <details className="mds-brief-detail">
    <summary>{title}<ChevronDown size={18} aria-hidden="true" /></summary>
    <div className="mds-brief-detail-body">{children}</div>
  </details>;
}
function Facts({ items }: { items: [string, string][] }) {
  return <dl className="mds-brief-facts">{items.map(([title, body]) => (
    <div key={title}><dt>{title}</dt><dd>{body}</dd></div>
  ))}</dl>;
}

export function SolutionBriefing({ onOpenLadder, onOpenEvidence }: { onOpenLadder: () => void; onOpenEvidence: () => void }) {
  return (
    <section className="mds-brief" aria-labelledby="mds-brief-title">
      <header className="mds-brief-head">
        <h1 id="mds-brief-title">Solution briefing</h1>
        <p>A travel concierge that remembers the traveler, governs each action and resumes interrupted work.</p>
      </header>
      <section className="mds-brief-section mds-brief-overview" aria-labelledby="brief-architecture-heading">
        <div className="mds-brief-section-heading"><h2 id="brief-architecture-heading">The architecture</h2><p>Two execution paths sharing governed tools and durable workflow state.</p></div>
        <BriefingArchitecture />
      </section>
      <BriefingPreparation />
      <BriefingPhases />
      <section className="mds-brief-section mds-brief-reference" aria-labelledby="brief-reference-heading">
        <div className="mds-brief-section-heading"><h2 id="brief-reference-heading">Technical reference</h2><p>Open the implementation detail when you need it.</p></div>
        <Detail title="Data preparation & the five phases">
          <h3>Prepare the inputs</h3>
          <p><code>scripts/travel_catalog.py</code> supplies fictional packages and traveler preferences. <code>scripts/seed_data.py</code> loads Aurora and embeds package descriptions with Cohere Embed v4 on Bedrock. Query and corpus use the same embedding model.</p>
          <p>Aurora combines pgvector similarity and full-text search; Bedrock reranks the candidates. Similarity does not prove availability: tools read prices, duration inventory and the authorized traveler’s saved budget from Aurora.</p>
          <h3>Build one capability at a time</h3>
          <ol className="mds-brief-phases">{PHASES.map(([name, claim, body]) => <li key={name}><h4>{name} · {claim}</h4><p>{body}</p></li>)}</ol>
          <h3>Delivery and service boundaries</h3>
          <p>CloudFront delivers the S3 site and routes API requests to FastAPI on App Runner. Local development uses Vite and the same backend. AgentCore resources are declared in <code>agentcore.json</code>. Aurora stores the catalog, identity bindings, access audit, traveler preferences, journeys, checkpoints, leases and bookings; the RDS Data API provides connectionless access.</p>
        </Detail>
        <Detail title="Tool contracts & Cedar policies">
          <p>The model interprets the request, chooses tools and writes grounded replies. Application code establishes the traveler, pins explicit confirmation and the saved budget, and validates the returned inventory. The model cannot choose its own authorization scope or budget ceiling.</p>
          <h3>Four MCP tools, two Lambda targets</h3>
          <p>Runtime discovers tools with <code>tools/list</code> and invokes them with <code>tools/call</code>. The Phase 5 worker uses the same governed hold tool.</p>
          <dl className="mds-brief-tools">{TOOLS.map(([name, kind, body]) => <div key={name}><dt><span>{kind}</span><code>{name}</code></dt><dd>{body}</dd></div>)}</dl>
          <h3>Configured Cedar policies</h3>
          <p><code>MeridianGovernance</code> uses ENFORCE mode and default deny. A denied call does not execute the target. The gateway ARN’s account portion is abbreviated below; this reference is not a live policy decision.</p>
          {POLICIES.map(([name, plain, statement]) => <article className="mds-brief-policy" key={name}><h4><code>{name}</code></h4><p>{plain}</p><pre>{statement}</pre></article>)}
          <h3>Authorization at each boundary</h3><Facts items={CONTROLS} />
          <p>Temporal policy with Dogwood is an assessed extension, not enabled here. Its proposed same-package lookup and five-minute session condition are documented in the <a href="https://github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore/blob/main/meridian/docs/DOGWOOD_POLICY_ASSESSMENT.md">Dogwood policy assessment</a>.</p>
        </Detail>
        <Detail title="Recovery guarantees & evidence">
          <p>The workflow runs classify → search → availability → prepare_hold → hold → synthesize. Before the write, prepare_hold checkpoints the request and booking IDs. The worker renews its lease in <code>journey_executions</code>; both worker and Lambda check the lease, with another Lambda check inside the write transaction.</p>
          <Facts items={[
            ['Before the hold', 'After lease release or expiry, resume the saved graph with the intended request and booking IDs.'],
            ['Write committed, response lost', 'Retry the same intent. Aurora returns the existing booking with its original expiry. A permitted call alone does not prove that the write committed.'],
            ['After the hold checkpoint', 'Continue the remaining nodes. Compare the saved hold with the persisted booking and successful execution receipt.'],
          ]} />
          <p>A checkpoint and a Gateway write are separate transactions. The workflow may retry; Aurora makes this business effect idempotent. A hard process-death rehearsal and a lost-response rehearsal test different failure windows.</p>
          <h3>Follow the result to its evidence</h3>
          <p>System evidence shows SQL and tool results, retrieval scores, traveler binding and RLS records, policy decisions, and persisted hold identity. Recovery desk shows the active thread, checkpoints and worker lease. Missing records remain unavailable.</p>
          <p>ADOT instruments Phase 4 model, Gateway and Memory operations as CloudWatch spans. Trace IDs connect those operations to the displayed run. Phase 5 adds workflow nodes, checkpoints and its Gateway hold decision. This briefing explains the design without making service calls.</p>
        </Detail>
      </section>
      <footer className="mds-brief-footer">
        <p>See the capabilities in action, then inspect what the session observed.</p>
        <div><button className="mds-brief-link" type="button" onClick={onOpenLadder}>Open the capability ladder <ArrowRight size={17} aria-hidden="true" /></button>
          <button className="mds-brief-link" type="button" onClick={onOpenEvidence}>Inspect system evidence <ArrowRight size={17} aria-hidden="true" /></button></div>
      </footer>
    </section>
  );
}
