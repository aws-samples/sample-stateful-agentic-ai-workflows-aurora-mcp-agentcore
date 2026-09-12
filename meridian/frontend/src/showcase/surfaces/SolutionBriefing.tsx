import { ArrowRight, ChevronDown } from 'lucide-react';
import { useRef, type ReactNode } from 'react';
import { ServiceMark, type ServiceMarkName } from '../components/ServiceMark';
import { BriefingArchitecture } from './BriefingArchitecture';
import { CODE_DECIDES, CONTROLS, MODEL_DECIDES, PHASES, POLICIES, SERVICES, SERVICE_MARKS, TOOLS } from './solutionBriefingContent';

const SECTIONS = [
  ['architecture', 'Architecture'], ['data', 'Prepared data'], ['phases', 'Five phases'],
  ['policy', 'Governed actions'], ['state', 'Memory & recovery'], ['evidence', 'Evidence'],
] as const;
const PREPARATION: [string, string, ServiceMarkName, string, string][] = [
  ['Packages, durations and seats', 'Load typed records with consistent package IDs', 'aurora', 'Aurora catalog', 'Prices, duration inventory and highlights'],
  ['Package descriptions', 'Create embeddings with Cohere Embed v4 on Bedrock', 'aurora', 'Aurora search indexes', 'pgvector similarity + full-text search'],
  ['Traveler facts and budgets', 'Store preferences; bind workload to traveler', 'aurora', 'Aurora under row-level security', 'Saved context and the authorized budget cap'],
  ['Tool contracts and business rules', 'Define MCP schemas and Cedar policies', 'agentcore', 'AgentCore Gateway + Policy', 'Validated inputs and a permit before execution'],
];

function Detail({ title, children }: { title: string; children: ReactNode }) {
  return <details className="mds-brief-detail">
    <summary>{title}<ChevronDown size={16} aria-hidden="true" /></summary>
    <div className="mds-brief-detail-body">{children}</div>
  </details>;
}
function Flow({ label, steps }: { label: string; steps: [string, string][] }) {
  return <ol className="mds-brief-flow" aria-label={label}>{steps.map(([title, detail], index) => (
    <li key={title}>
      {index > 0 && <ArrowRight className="mds-brief-flow-arrow" size={20} aria-hidden="true" />}
      <div><strong>{title}</strong><span>{detail}</span></div>
    </li>
  ))}</ol>;
}
function Facts({ items }: { items: [string, string][] }) {
  return <dl className="mds-brief-facts">{items.map(([title, body]) => (
    <div key={title}><dt>{title}</dt><dd>{body}</dd></div>
  ))}</dl>;
}
function SectionHeading({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return <div className="mds-brief-section-heading">
    <h2 id={`brief-${id}-heading`} tabIndex={-1}>{title}</h2><p>{children}</p>
  </div>;
}

export function SolutionBriefing({ onOpenLadder }: { onOpenLadder: () => void }) {
  const briefingRef = useRef<HTMLElement>(null);
  const navigate = (id: string) => {
    const heading = briefingRef.current?.querySelector<HTMLElement>(`#brief-${id}-heading`);
    heading?.scrollIntoView({ block: 'start', behavior: 'instant' });
    heading?.focus({ preventScroll: true });
  };
  return (
    <section className="mds-brief" aria-labelledby="mds-brief-title" ref={briefingRef}>
      <header className="mds-brief-head">
        <div><h1 id="mds-brief-title">Solution briefing</h1>
          <p>Remember the traveler. Govern the action. Resume the journey.</p>
          <span className="mds-brief-note">Fictional inventory · architecture and implementation guide</span>
        </div>
        <button className="mds-brief-link" type="button" onClick={onOpenLadder}>Open the capability ladder <ArrowRight size={17} aria-hidden="true" /></button>
      </header>
      <nav className="mds-brief-nav" aria-label="Solution briefing sections">
        {SECTIONS.map(([id, label]) => <button key={id} type="button" aria-controls={`brief-${id}`} onClick={() => navigate(id)}>{label}</button>)}
      </nav>
      <section id="brief-architecture" className="mds-brief-section" aria-labelledby="brief-architecture-heading">
        <SectionHeading id="architecture" title="The request, the tools and the state">Managed concierge and durable workflow share one governed write path.</SectionHeading>
        <div className="mds-brief-architecture-grid">
          <BriefingArchitecture />
          <aside className="mds-brief-access" aria-label="Architecture boundaries">
            <h3>At each boundary</h3>
            <Facts items={[
              ['Viewer → application', 'CloudFront delivers the site; App Runner hosts FastAPI.'],
              ['Workload → traveler', 'Identity bindings authorize the caller before Aurora applies row-level security.'],
              ['Agent → tool', 'IAM-signed MCP calls pass through AgentCore Gateway and Cedar policy.'],
              ['Worker → state', 'Aurora checkpoints, worker leases and replay-safe holds preserve progress.'],
            ]} />
          </aside>
        </div>
        <Detail title="Inspect service roles and delivery">
          <div className="mds-brief-services">{SERVICES.map(([name, role]) => <article key={name}>
            {SERVICE_MARKS[name] && <ServiceMark name={SERVICE_MARKS[name]} size={28} />}
            <div><h3>{name}</h3><p>{role}</p></div>
          </article>)}</div>
          <p>The published path uses CloudFront for viewer access and API routing, S3 for the static site, and App Runner for the backend. Local development uses Vite and the same FastAPI application. The AgentCore resources are declared in <code>agentcore.json</code>.</p>
        </Detail>
      </section>
      <section id="brief-data" className="mds-brief-section" aria-labelledby="brief-data-heading">
        <SectionHeading id="data" title="Prepare the data before the question">Seed records and configured policies become inputs the tools can trust.</SectionHeading>
        <div className="mds-brief-preparation" role="table" aria-label="Source records, preparation and prepared stores">
          <div className="mds-brief-prep-head" role="row"><span role="columnheader">Source</span><span role="columnheader">Preparation</span><span role="columnheader">Ready for the tools</span></div>
          {PREPARATION.map(([source, action, mark, store, detail]) => <div className="mds-brief-prep-row" role="row" key={source}>
            <strong role="cell">{source}</strong>
            <span role="cell"><ArrowRight size={18} aria-hidden="true" />{action}</span>
            <div role="cell"><ArrowRight size={18} aria-hidden="true" /><ServiceMark name={mark} size={28} /><div><strong>{store}</strong><small>{detail}</small></div></div>
          </div>)}
        </div>
        <Flow label="Retrieval sequence" steps={[
          ['Words + meaning', 'Aurora full-text search and pgvector'],
          ['Fuse + rerank', 'Combine candidates; Bedrock reranks'],
          ['Verify the option', 'Check duration inventory and budget'],
        ]} />
        <Detail title="Inspect the data and retrieval boundaries">
          <p><code>scripts/travel_catalog.py</code> supplies the fictional packages, travelers and preferences. <code>scripts/seed_data.py</code> loads Aurora and embeds package descriptions. The query and corpus must use the same embedding model.</p>
          <p>Similarity finds candidates; it does not prove availability. Prices and seats are read from Aurora. The saved budget comes from the authorized traveler’s preferences. Checkpoints, worker leases and new holds are written during execution.</p>
        </Detail>
      </section>
      <section id="brief-phases" className="mds-brief-section" aria-labelledby="brief-phases-heading">
        <SectionHeading id="phases" title="Five phases, one traveler">Each phase adds a capability and a piece of inspectable evidence.</SectionHeading>
        <ol className="mds-brief-phases">{[
          ['SQL', 'Ground in live rows', 'Parameterized Aurora queries', 'SQL + returned rows'],
          ['MCP', 'Reuse named tools', 'Search, compare and currency', 'Tool inputs + results'],
          ['Retrieval', 'Search by meaning', 'Hybrid retrieval + reranking', 'Candidate scores + order'],
          ['Production', 'Act under policy', 'Runtime, Gateway and Memory', 'Cedar decision + receipt'],
          ['Workflow', 'Resume saved work', 'LangGraph + Aurora checkpoints', 'Same thread + one hold'],
        ].map(([name, title, service, proof], index) => <li key={name}>
          <div className="mds-brief-phase-label"><span>{index + 1}</span><h3>{name}</h3></div>
          <strong>{title}</strong><p>{service}</p><small>{proof}</small>
        </li>)}</ol>
        <div className="mds-brief-boundaries"><Facts items={[
          ['Model', 'Interprets the request, sequences tools and writes grounded prose.'],
          ['Application', 'Binds the traveler, pins confirmation and budget, and validates inventory.'],
          ['Policy', 'Decides whether each gateway tool call may execute.'],
        ]} /></div>
        <div className="mds-brief-detail-pair">
          <Detail title="Inspect the phase-by-phase implementation"><Facts items={PHASES.map(([name, claim, body]) => [name, `${claim} ${body}`])} /></Detail>
          <Detail title="Inspect model and platform responsibilities"><h3>The model decides</h3><Facts items={MODEL_DECIDES} /><h3>The platform decides</h3><Facts items={CODE_DECIDES} /></Detail>
        </div>
      </section>
      <section id="brief-policy" className="mds-brief-section" aria-labelledby="brief-policy-heading">
        <SectionHeading id="policy" title="A confirmed action, then a policy decision">AgentCore Policy evaluates Cedar before a gateway target runs.</SectionHeading>
        <Flow label="Governed action sequence" steps={[
          ['Traveler confirms', 'Platform pins identity, budget and intent'],
          ['Gateway + Cedar', 'An applicable permit is required'],
          ['Lambda → Aurora', 'Write under traveler scope; return a receipt'],
        ]} />
        <div className="mds-brief-policy-rules"><Facts items={[
          ['Courtesy hold', 'Confirmed · up to 12 hours · up to 6 travelers · within saved budget'],
          ['Booking confirmation', 'Confirmed · within saved budget; Aurora checks ownership and expiry'],
          ['Denied call', 'Target does not run. The trace shows the refusal and its reason.'],
        ]} /></div>
        <p className="mds-brief-note">These are Meridian catalog reservations. No supplier is contacted and no payment is taken.</p>
        <div className="mds-brief-detail-pair">
          <Detail title="Read the three Cedar policies">
            <p><code>MeridianGovernance</code> is configured in ENFORCE mode. These are the configured policies, with the account portion of the gateway ARN abbreviated.</p>
            {POLICIES.map(([name, plain, statement]) => <article className="mds-brief-policy" key={name}><h3><code>{name}</code></h3><p>{plain}</p><pre>{statement}</pre></article>)}
          </Detail>
          <Detail title="Inspect the four MCP tools">
            <p>Two Lambda targets, one gateway. The runtime discovers tools with <code>tools/list</code> and invokes them with <code>tools/call</code>. The Phase 5 worker uses the same hold tool.</p>
            {TOOLS.map(([name, kind, body]) => <article className="mds-brief-tool" key={name}><span className={`mds-brief-kind is-${kind.toLowerCase()}`}>{kind}</span><div><h3><code>{name}</code></h3><p>{body}</p></div></article>)}
          </Detail>
        </div>
        <Detail title="Discuss temporal policy with Dogwood">
          <p>Cedar is configured today. Dogwood is an assessed extension, not enabled here: require a successful lookup of the same package within five minutes before a hold, in the same authenticated policy session.</p>
          <p>Persist the policy session across restarts and keep caller identities explicit. Combine the temporal condition with the existing hold conditions; an older, broader permit must not still admit the call. Aurora still validates capacity and replay, and the application still establishes traveler approval.</p>
        </Detail>
      </section>
      <section id="brief-state" className="mds-brief-section" aria-labelledby="brief-state-heading">
        <SectionHeading id="state" title="Remember context. Resume execution.">Traveler preferences, conversation context and workflow progress have different jobs.</SectionHeading>
        <div className="mds-brief-state-grid">{([
          ['aurora', 'Traveler facts', 'Aurora preferences', 'Saved preferences and budget, read under the traveler’s row-level scope.'],
          ['agentcore', 'Conversation context', 'AgentCore Memory', 'Phase 4 session context with a semantic strategy, scoped to traveler and conversation.'],
          ['aurora', 'Execution state', 'Aurora + LangGraph', 'Checkpoints, worker leases and hold intent let a new worker continue the same thread.'],
        ] satisfies [ServiceMarkName, string, string, string][]).map(([mark, title, service, body]) => <article key={title}><ServiceMark name={mark} size={30} /><div><h3>{title}</h3><span>{service}</span><p>{body}</p></div></article>)}</div>
        <Flow label="Durable recovery sequence" steps={[
          ['Save progress', 'Checkpoint each workflow node in Aurora'],
          ['Replace the worker', 'Resume after the previous lease clears'],
          ['Replay safely', 'Reuse the hold request and booking IDs'],
          ['Return to the traveler', 'Confirm the held trip in Concierge'],
        ]} />
        <Detail title="Inspect durability and authorization controls">
          <p>The recovery sequence is classify, search, availability, prepare_hold, hold and synthesize. The prepare_hold node checkpoints the request and booking IDs before the write. The worker renews its lease in <code>journey_executions</code>. Before a hold, both the worker and the Lambda check the lease; the Lambda checks again inside the write transaction.</p>
          <p>The checkpointed request ID and booking ID let a resumed worker retrieve the existing hold with its original expiry. The recovery proof must read back the same booking and hold count; a successful response alone does not prove replay safety.</p>
          <Facts items={CONTROLS} />
        </Detail>
        <Detail title="Walk through three failure windows">
          <Facts items={[
            ['Before the hold', 'Resume the saved graph after lease takeover. The checkpoint carries the intended request and booking IDs.'],
            ['Write committed, response lost', 'Retry the same intent. Aurora returns the existing booking with its original expiry. A permitted call alone does not establish whether the write committed.'],
            ['After the hold checkpoint', 'Continue the remaining nodes. Compare the saved hold with the persisted booking and the successful execution receipt.'],
          ]} />
          <p>A checkpoint and a Gateway write are not one transaction. The workflow may retry; Aurora makes the business effect idempotent. A hard process-death rehearsal and a lost-response rehearsal test different failure windows.</p>
        </Detail>
      </section>
      <section id="brief-evidence" className="mds-brief-section" aria-labelledby="brief-evidence-heading">
        <SectionHeading id="evidence" title="Follow the result back to its evidence">The briefing explains the design. System evidence shows what the current session observed.</SectionHeading>
        <div className="mds-brief-evidence"><Facts items={[
          ['Retrieval', 'SQL, tool inputs, candidates and the returned rank order'],
          ['Authorization', 'Traveler binding, RLS scope and allow/deny audit records'],
          ['Policy', 'Gateway decision, pinned arguments and the hold or booking receipt'],
          ['Durability', 'Thread, checkpoint, worker lease and persisted hold identity'],
        ]} /></div>
        <Detail title="Inspect observability and proof boundaries">
          <p>ADOT instruments the Phase 4 runtime. Model calls, gateway calls and Memory operations produce CloudWatch spans; trace IDs connect those operations to the displayed run. Phase 5 adds workflow nodes, checkpoints and its gateway hold decision.</p>
          <p>The Recovery desk and System evidence surface read the active journey. Missing records remain unavailable; they are not inferred from narration. This briefing does not make service calls or report a live policy decision.</p>
        </Detail>
      </section>
      <footer className="mds-brief-footer"><p>State belongs to the journey. Decisions remain inspectable.</p><span>Meridian · Aurora PostgreSQL, MCP and AgentCore</span></footer>
    </section>
  );
}
