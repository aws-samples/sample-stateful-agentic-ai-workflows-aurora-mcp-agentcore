import { ArrowRight, Braces, FileJson, MessageSquare, ShieldCheck, Workflow, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { ServiceMark, type ServiceMarkName } from '../components/ServiceMark';
import './briefingWalkthrough.css';

type Step = { title: string; detail: string; service?: ServiceMarkName; icon?: LucideIcon; incoming?: boolean };

function StepContent({ title, detail, service, icon: Icon, incoming }: Step) {
  return <div className="mds-brief-step">
    {incoming && <ArrowRight className="mds-brief-prep-connector" size={20} aria-hidden="true" />}
    {service ? <ServiceMark name={service} size={32} /> : Icon ? <Icon size={28} aria-hidden="true" /> : null}
    <div><strong>{title}</strong><span>{detail}</span></div>
  </div>;
}

function MiniFlow({ label, steps }: { label: string; steps: Step[] }) {
  return <ol className="mds-brief-mini-flow" aria-label={label}>{steps.map((step, index) => <li key={step.title}>
    {index > 0 && <ArrowRight className="mds-brief-connector" size={20} aria-hidden="true" />}
    <StepContent {...step} />
  </li>)}</ol>;
}

function Phase({ number, name, claim, children }: { number: number; name: string; claim: string; children: ReactNode }) {
  return <article className={`mds-brief-phase mds-brief-phase-${number}`} aria-labelledby={`brief-phase-${number}`}>
    <div className="mds-brief-phase-heading"><h3 id={`brief-phase-${number}`}>Phase {number} · {name}</h3><p>{claim}</p></div>
    {children}
  </article>;
}

export function BriefingPreparation() {
  return <section className="mds-brief-section mds-brief-preparation" aria-labelledby="brief-preparation-heading">
    <div className="mds-brief-section-heading"><h2 id="brief-preparation-heading">Prepare the data before the question</h2><p>Seed-time preparation gives each tool something reliable to read.</p></div>
    <div className="mds-brief-prep-labels" aria-hidden="true"><span>Source records</span><span>Preparation</span><span>Ready for the tools</span></div>
    <ol className="mds-brief-prep-paths" aria-label="Source records, preparation and Aurora stores">
      <li>
        <div><h3>Package facts</h3><p>Prices, durations and seats</p></div>
        <StepContent incoming icon={FileJson} title="Load typed records" detail="Consistent package IDs and duration inventory" />
        <StepContent incoming service="aurora" title="Aurora catalog" detail="Exact facts for SQL and tool lookups" />
      </li>
      <li>
        <div><h3>Package descriptions</h3><p>“Ubud villa, yoga mornings, temple tour…”</p></div>
        <StepContent incoming service="bedrock" title="Embed meaning, index words" detail="Cohere Embed v4 on Bedrock; full-text processing in PostgreSQL" />
        <StepContent incoming service="aurora" title="Two search indexes" detail="pgvector for meaning · tsvector for words" />
      </li>
      <li>
        <div><h3>Traveler facts</h3><p>Preferences and a saved budget</p></div>
        <StepContent incoming icon={ShieldCheck} title="Bind workload to traveler" detail="Identity grants establish the permitted traveler" />
        <StepContent incoming service="aurora" title="Scoped preferences" detail="Row-level security protects traveler context" />
      </li>
    </ol>
    <p className="mds-brief-note">The package ID connects descriptions, search candidates and current inventory. Query and catalog embeddings use the same model.</p>
  </section>;
}

export function BriefingPhases() {
  return <section className="mds-brief-section mds-brief-walkthrough" aria-labelledby="brief-phases-heading">
    <div className="mds-brief-section-heading"><h2 id="brief-phases-heading">Five phases, one connected system</h2><p>Each phase adds a capability to the same travel journey.</p></div>
    <div className="mds-brief-phase-pair">
      <Phase number={1} name="SQL" claim="Answer from exact rows.">
        <MiniFlow label="Phase 1 SQL path" steps={[
          { icon: MessageSquare, title: 'Question', detail: 'Destination + budget' },
          { icon: Braces, title: 'SQL filters', detail: 'Parameterized query' },
          { service: 'aurora', title: 'Aurora', detail: 'Data API → rows' },
        ]} />
        <p className="mds-brief-phase-evidence">Inspect the SQL and returned package records.</p>
      </Phase>
      <Phase number={2} name="MCP" claim="Put a reusable tool contract around the data.">
        <MiniFlow label="Phase 2 MCP path" steps={[
          { service: 'app-runner', title: 'FastAPI', detail: 'MCP client' },
          { icon: Braces, title: 'MCP server', detail: 'Named PostgreSQL tools' },
          { service: 'aurora', title: 'Aurora', detail: 'Same Data API' },
        ]} />
        <p className="mds-brief-phase-evidence">Inspect tool names, inputs and results. Gateway joins in Phase 4.</p>
      </Phase>
    </div>
    <Phase number={3} name="Retrieval" claim="Find the meaning as well as the words.">
      <figure className="mds-brief-retrieval" aria-labelledby="brief-retrieval-query">
        <div className="mds-brief-query"><MessageSquare size={23} aria-hidden="true" /><div><p id="brief-retrieval-query">“A Bali villa with yoga and a beach day”</p><span>Illustrative query · seeded catalog example</span></div></div>
        <div className="mds-brief-retrieval-layout">
          <ol className="mds-brief-search-flow" aria-label="Hybrid retrieval pipeline">
            <li>
              <StepContent service="bedrock" title="Embed the query" detail="Cohere Embed v4" />
              <p>Represent its meaning as a vector.</p>
            </li>
            <li>
              <ArrowRight className="mds-brief-connector" size={20} aria-hidden="true" />
              <div className="mds-brief-hybrid">
                <StepContent service="aurora" title="Search both ways" detail="Aurora PostgreSQL" />
                <div className="mds-brief-search-branches">
                  <div><strong>Meaning</strong><span>Query vector → pgvector</span></div>
                  <div><strong>Words</strong><span>Query text → tsvector</span></div>
                </div>
                <p className="mds-brief-merge">Merge candidates by package ID</p>
              </div>
            </li>
            <li>
              <ArrowRight className="mds-brief-connector" size={20} aria-hidden="true" />
              <div><StepContent service="bedrock" title="Rerank" detail="Cohere Rerank 3.5" /><p>Read the query and candidates together to order the matches.</p></div>
            </li>
          </ol>
          <div className="mds-brief-destination-output"><ArrowRight className="mds-brief-result-connector" size={20} aria-hidden="true" /><div className="mds-brief-destination">
            <img src="/travel/catalog/BCH-003.jpg" alt="Green rice terraces and palms in Bali" width={320} height={180} loading="lazy" />
            <div><span>Example candidate · BCH-003</span><h4>Bali Rice Terrace Retreat</h4><p>Ubud villa · yoga mornings · beach club day</p></div>
          </div></div>
        </div>
        <figcaption>The image illustrates a catalog candidate. Run Phase 3 in the capability ladder for measured scores and the actual result order; tools verify current prices, seats and budget separately.</figcaption>
      </figure>
    </Phase>
    <div className="mds-brief-phase-pair">
      <Phase number={4} name="Production" claim="Remember context. Govern every tool call.">
        <MiniFlow label="Phase 4 governed action path" steps={[
          { service: 'agentcore', title: 'Runtime', detail: 'Strands + Memory' },
          { service: 'agentcore', title: 'Gateway', detail: 'Cedar policy' },
          { service: 'lambda', title: 'Lambda', detail: 'Aurora transaction' },
        ]} />
        <p>The traveler confirms; the platform pins identity and saved budget. Cedar permits the call before the tool runs. Holds allow up to 6 travelers and 12 hours; Aurora checks inventory, ownership and expiry.</p>
        <p className="mds-brief-phase-evidence">Inspect the policy decision and persisted receipt.</p>
      </Phase>
      <Phase number={5} name="Workflow" claim="Resume the work after a worker stops.">
        <MiniFlow label="Phase 5 durable workflow path" steps={[
          { icon: Workflow, title: 'LangGraph', detail: 'Worker in FastAPI' },
          { service: 'aurora', title: 'Checkpoint', detail: 'Thread + hold intent' },
          { service: 'agentcore', title: 'Gateway', detail: 'Replay the same hold' },
        ]} />
        <p>After lease release or expiry, a replacement resumes the same thread and reuses the request and booking IDs. Aurora checkpoints restore execution; AgentCore Memory supplies Phase 4 conversation context.</p>
        <p className="mds-brief-phase-evidence">Verify the same booking ID, original expiry and one hold.</p>
      </Phase>
    </div>
    <p className="mds-brief-note">Fictional Meridian inventory. Holds and confirmations affect the demo catalog; no supplier is contacted and no payment is taken.</p>
  </section>;
}
