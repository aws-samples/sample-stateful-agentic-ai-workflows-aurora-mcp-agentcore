import { ArrowRight, ChevronDown, FileJson, MessageSquare, ShieldCheck, Workflow, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import type { Phase as CapabilityPhase } from '../../types';
import { ServiceMark, type ServiceMarkName } from '../components/ServiceMark';
import { PHASE_PATHS } from './briefingSteps';
import { StepPath } from './StepPath';
import { WorkflowSteps } from './WorkflowSteps';
import './briefingWalkthrough.css';

type Step = { title: string; detail: string; service?: ServiceMarkName; icon?: LucideIcon; incoming?: boolean };

function StepContent({ title, detail, service, icon: Icon, incoming }: Step) {
  return <div className="mds-brief-step">
    {incoming && <ArrowRight className="mds-brief-prep-connector" size={20} aria-hidden="true" />}
    {service ? <ServiceMark name={service} size={32} /> : Icon ? <Icon size={28} aria-hidden="true" /> : null}
    <div><strong>{title}</strong><span>{detail}</span></div>
  </div>;
}

function Phase({ number, name, claim, children, onOpen, busy }: {
  number: CapabilityPhase; name: string; claim: string; children: ReactNode;
  onOpen?: (phase: CapabilityPhase) => void; busy?: boolean;
}) {
  return <details name="briefing-capability" className={`mds-brief-phase mds-brief-phase-${number}`} aria-labelledby={`brief-phase-${number}`}>
    <summary className="mds-brief-phase-heading"><div><h3 id={`brief-phase-${number}`}>Phase {number} - {name}</h3><p>{claim}</p></div><ChevronDown size={20} aria-hidden="true" /></summary>
    <div className="mds-brief-phase-body">{children}
      {onOpen && <button type="button" className="mds-brief-link" disabled={busy} onClick={() => onOpen(number)}>Open Phase {number}: {name}<ArrowRight size={17} aria-hidden="true" /></button>}
    </div>
  </details>;
}

export function BriefingPreparation() {
  return <details name="solution-briefing" className="mds-brief-section mds-brief-preparation" aria-labelledby="brief-preparation-heading">
    <summary className="mds-brief-section-heading"><div><h2 id="brief-preparation-heading"><span className="mds-brief-section-number" aria-hidden="true">02</span>Prepare the data</h2><p>Load facts. Index descriptions. Scope the traveler.</p></div><ChevronDown size={22} aria-hidden="true" /></summary>
    <div className="mds-brief-section-body">
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
        <StepContent incoming service="aurora" title="Two search indexes" detail="pgvector for meaning, tsvector for words" />
      </li>
      <li>
        <div><h3>Traveler facts</h3><p>Preferences and a saved budget</p></div>
        <StepContent incoming icon={ShieldCheck} title="Bind workload to traveler" detail="Identity grants establish the permitted traveler" />
        <StepContent incoming service="aurora" title="Scoped preferences" detail="Row-level security protects traveler context" />
      </li>
    </ol>
    <p className="mds-brief-note">The package ID connects descriptions, search candidates and current inventory. Query and catalog embeddings use the same model.</p>
    </div>
  </details>;
}

export function BriefingPhases({ onOpenPhase, busy }: { onOpenPhase?: (phase: CapabilityPhase) => void; busy?: boolean } = {}) {
  return <details name="solution-briefing" className="mds-brief-section mds-brief-walkthrough" aria-labelledby="brief-phases-heading">
    <summary className="mds-brief-section-heading"><div><h2 id="brief-phases-heading"><span className="mds-brief-section-number" aria-hidden="true">03</span>Build five capabilities</h2><p>SQL → MCP → retrieval → production → recovery.</p></div><ChevronDown size={22} aria-hidden="true" /></summary>
    <div className="mds-brief-section-body">
    <div className="mds-brief-phase-pair">
      <Phase number={1} name="SQL" claim="Answer from exact rows." onOpen={onOpenPhase} busy={busy}>
        <StepPath data={PHASE_PATHS[1]} />
        <p className="mds-brief-phase-evidence">Check the SQL and rows. Budget is per traveler. This is bounded filtering, not unrestricted text-to-SQL.</p>
      </Phase>
      <Phase number={2} name="MCP" claim="Put a reusable tool contract around the data." onOpen={onOpenPhase} busy={busy}>
        <StepPath data={PHASE_PATHS[2]} />
        <p className="mds-brief-phase-evidence">Check tool names, inputs and results. Gateway governs the production tools in Phase 4.</p>
      </Phase>
    </div>
    <Phase number={3} name="Retrieval" claim="Find the meaning as well as the words." onOpen={onOpenPhase} busy={busy}>
      <figure className="mds-brief-retrieval" aria-labelledby="brief-retrieval-query">
        <div className="mds-brief-query"><MessageSquare size={23} aria-hidden="true" /><div><p id="brief-retrieval-query">“A Bali villa with yoga and a beach day”</p><span>Illustrative query, seeded catalog example</span></div></div>
        <StepPath data={PHASE_PATHS[3]} />
        <figcaption>Check observed scores in Phase 3. Relevance is a candidate signal; tools still verify price, availability and access.</figcaption>
      </figure>
    </Phase>
    <div className="mds-brief-phase-pair">
      <Phase number={4} name="Production" claim="Remember context. Govern every tool call." onOpen={onOpenPhase} busy={busy}>
        <StepPath data={PHASE_PATHS[4]} note="Signed-in release shown: FastAPI, the Runtime and the Gateway each check the access token." />
        <p className="mds-brief-phase-evidence">Check both the policy decision and the Aurora receipt. Permission to call a tool is not proof of a committed write.</p>
      </Phase>
      <Phase number={5} name="Workflow" claim="Resume the work after a worker stops." onOpen={onOpenPhase} busy={busy}>
        <p className="mds-brief-workflow-host"><Workflow size={24} aria-hidden="true" /><strong>Strands Graph in its own AgentCore Runtime</strong><span>Saved steps in Amazon Aurora through the RDS Data API</span></p>
        <WorkflowSteps />
        <p>After lease release or expiry, resume the same thread with the same request and booking IDs. If a later step fails, the runner releases the hold through the Gateway under Cedar, fenced by the run&rsquo;s lease and journey.</p>
        <p className="mds-brief-phase-evidence">Check the hold record, the same booking ID and the original expiry. Saved steps in Aurora restore work; Phase 4 keeps conversation context in AgentCore Memory.</p>
      </Phase>
    </div>
    <p className="mds-brief-note">Fictional Meridian inventory. Holds and confirmations affect the sample catalog; no supplier is contacted and no payment is taken.</p>
    </div>
  </details>;
}
