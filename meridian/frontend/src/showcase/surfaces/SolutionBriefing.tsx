import { ArrowRight, ChevronDown } from 'lucide-react';
import type { ReactNode } from 'react';
import { BriefingArchitecture } from './BriefingArchitecture';
import { BriefingPhases, BriefingPreparation } from './BriefingWalkthrough';
import { CONTROLS, POLICIES, TOOLS } from './solutionBriefingContent';

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
        <p>Ground the answer. Govern the action. Recover the work.</p>
      </header>
      <details name="solution-briefing" className="mds-brief-section mds-brief-overview" open aria-labelledby="brief-architecture-heading">
        <summary className="mds-brief-section-heading"><div><h2 id="brief-architecture-heading"><span className="mds-brief-section-number" aria-hidden="true">01</span>The architecture</h2><p>Two execution paths. One governed tool boundary.</p></div><ChevronDown size={22} aria-hidden="true" /></summary>
        <div className="mds-brief-section-body">
          <BriefingArchitecture />
        </div>
      </details>
      <BriefingPreparation />
      <BriefingPhases />
      <details name="solution-briefing" className="mds-brief-section mds-brief-reference" aria-labelledby="brief-reference-heading">
        <summary className="mds-brief-section-heading"><div><h2 id="brief-reference-heading"><span className="mds-brief-section-number" aria-hidden="true">04</span>Verify the boundaries</h2><p>Check permission, committed outcomes and recovery.</p></div><ChevronDown size={22} aria-hidden="true" /></summary>
        <div className="mds-brief-section-body">
        <Facts items={[
          ['Identity', 'The application binds the traveler and saved budget.'],
          ['Permission', 'Cedar decides whether a tool may run.'],
          ['Outcome', 'Aurora records what actually committed.'],
        ]} />
        <Detail title="Implementation reference">
          <Facts items={[
            ['Seed', 'travel_catalog.py defines fictional inventory; seed_data.py loads Aurora and embeds descriptions.'],
            ['Retrieve', 'Query and catalog use the same embedding model. Tools recheck price, availability and access.'],
            ['Deliver', 'CloudFront serves S3 and routes the API to FastAPI on App Runner. Local development uses Vite and the same backend.'],
            ['Persist', 'The RDS Data API connects Aurora catalog, traveler state, checkpoints, leases and bookings. AgentCore resources are declared in agentcore.json.'],
          ]} />
          <p>AgentCore also offers <a href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-integrate-lang.html">LangGraph checkpoint persistence</a>; Meridian uses Aurora. A checkpoint is separate from the booking receipt.</p>
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
          <p><a href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html">AgentCore Policy supports temporal conditions through Dogwood</a>. Meridian uses Cedar per-call policies; the proposed same-package lookup and five-minute session condition remain an <a href="https://github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore/blob/main/meridian/docs/DOGWOOD_POLICY_ASSESSMENT.md">assessed extension</a>, not enabled here.</p>
          <p>Trusted identity and row-level security govern access. Application deadlines, bounded retries and concurrency controls address workload pressure; separate pools alone do not reserve database compute.</p>
        </Detail>
        <Detail title="Recovery guarantees & evidence">
          <p>The workflow runs classify → search → availability → prepare_hold → hold → synthesize. Before the write, prepare_hold checkpoints the request and booking IDs. The worker renews its lease in <code>journey_executions</code>; both worker and Lambda check the lease, with another Lambda check inside the write transaction.</p>
          <Facts items={[
            ['Before the hold', 'After lease release or expiry, resume the saved graph with the intended request and booking IDs.'],
            ['Write committed, response lost', 'Retry the same intent. Aurora returns the existing booking with its original expiry. A permitted call alone does not prove that the write committed.'],
            ['After the hold checkpoint', 'Continue the remaining nodes. Compare the saved hold with the persisted booking and successful execution receipt.'],
          ]} />
          <p>A checkpoint and a Gateway write are separate transactions. The workflow may retry; Aurora makes this business effect idempotent. A hard process-death rehearsal and a lost-response rehearsal test different failure windows.</p>
          <p>Browser reload proves saved-state readback. The separate lost-response rehearsal discards a real committed reply, resumes on a replacement worker and checks the original receipt. It uses its own journey; it does not inject a fault into the open browser session.</p>
          <h3>Follow the result to its evidence</h3>
          <p>System evidence shows SQL and tool results, retrieval scores, traveler binding and RLS records, policy decisions, and persisted hold identity. Recovery desk shows the active thread, checkpoints and worker lease. Missing records remain unavailable.</p>
          <p>ADOT instruments Phase 4 model, Gateway and Memory operations as CloudWatch spans. Trace IDs connect those operations to the displayed run. Phase 5 adds workflow nodes, checkpoints and its Gateway hold decision. This briefing explains the design without making service calls.</p>
        </Detail>
        </div>
      </details>
      <footer className="mds-brief-footer">
        <div><button className="mds-brief-link" type="button" onClick={onOpenLadder}>Open the capability ladder <ArrowRight size={17} aria-hidden="true" /></button>
          <button className="mds-brief-link" type="button" onClick={onOpenEvidence}>Inspect system evidence <ArrowRight size={17} aria-hidden="true" /></button></div>
      </footer>
    </section>
  );
}
