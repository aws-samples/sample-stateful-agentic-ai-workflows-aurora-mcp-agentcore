import { ArrowRight } from 'lucide-react';

/**
 * Solution briefing: what Meridian is, what it runs on, and where the
 * boundaries sit. Every statement here describes the deployed system as it is
 * wired today; nothing is aspirational. Copy that names a resource, a policy or
 * an argument uses the real identifier so a reader can find it in the code.
 */

const PHASES: [string, string, string][] = [
  ['SQL', 'Ground the assistant in live rows.', 'Parameterised filters over trip_packages in Aurora PostgreSQL through the RDS Data API. The trace shows the SQL that ran and the rows it returned.'],
  ['MCP', 'Give the agent tools it can reuse.', 'Search, compare and currency conversion behind named MCP tool contracts. The trace shows each tool name, its inputs and its result.'],
  ['Retrieval', 'Find trips by meaning.', 'pgvector similarity and full-text search fused into one candidate list, then reranked by Cohere Rerank 3.5 on Bedrock. The trace shows candidate scores and the rerank order.'],
  ['Production', 'Run the concierge on managed infrastructure under policy.', 'A Strands agent in Bedrock AgentCore Runtime calls its tools through AgentCore Gateway over MCP; a Cedar policy engine decides every call; AgentCore Memory carries the conversation. A courtesy hold is a governed write.'],
  ['Workflow', 'Make multi-step work survive a dead worker.', 'A LangGraph state graph checkpoints every node into Aurora, holds a worker lease, and places its hold through the same gateway tool. Kill the worker; a second one resumes the same thread and finds one hold.'],
];

const MODEL_DECIDES: [string, string][] = [
  ['Interpretation', 'What the traveler is asking for: destination, duration, party size, the preferences worth recalling.'],
  ['Sequencing', 'Which gateway tool to call next, with which arguments drawn from the search results.'],
  ['Prose', 'The reply the traveler reads, written from the tool results and the recalled facts.'],
];

const CODE_DECIDES: [string, string][] = [
  ['Who the caller is', 'STS or AgentCore Identity names the workload; Aurora binds that subject to a traveler before any row is read.'],
  ['What a hold may cost', 'The budget ceiling comes from the traveler’s saved budget fact, read under RLS; the runtime pins it onto the hold call. The model never chooses it.'],
  ['Whether a hold runs', 'The traveler’s confirmation flag and the ceiling travel as tool arguments; Cedar evaluates them before the Lambda runs.'],
  ['Inventory and replay', 'create_courtesy_hold in Aurora takes the capacity lock, decrements seats and replays an identical request instead of holding twice.'],
];

const TOOLS: [string, string, string][] = [
  ['SemanticTripSearchLambda___semantic_trip_search', 'Read', 'Embeds the query with Cohere Embed v4, searches pgvector and full-text indexes in Aurora, and returns ranked packages with scores.'],
  ['MeridianHolds___get_package_details', 'Read', 'One package with its live durations, availability and highlights, read under the traveler’s RLS scope.'],
  ['MeridianHolds___create_courtesy_hold', 'Write', 'Places a courtesy hold on one package duration. Requires the traveler id, confirmation flag, budget ceiling and journey reference the platform pinned; accepts a checkpointed request id, booking id and worker execution id for replay.'],
];

const POLICIES: [string, string, string][] = [
  ['meridian_read_tools', 'Any authenticated caller may search packages and read package details.',
   'permit(principal,\n  action in [AgentCore::Action::"SemanticTripSearchLambda___semantic_trip_search",\n             AgentCore::Action::"MeridianHolds___get_package_details"],\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:...:gateway/meridianv2-meridian-aurora-temzt21jg0");'],
  ['meridian_hold_governance', 'A courtesy hold runs only after the traveler confirmed it, for at most 12 hours, for at most 6 travelers, and within the traveler’s saved budget ceiling. Nothing else permits the hold, so any other call is denied by default.',
   'permit(principal,\n  action == AgentCore::Action::"MeridianHolds___create_courtesy_hold",\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:...:gateway/meridianv2-meridian-aurora-temzt21jg0")\nwhen {\n  context.input.travelerConfirmed == true &&\n  context.input.holdMinutes <= 720 &&\n  context.input.travelers <= 6 &&\n  context.input.totalCents <= context.input.budgetCeilingCents\n};'],
];

const CONTROLS: [string, string][] = [
  ['Authenticate the workload', 'AgentCore Identity or AWS STS names the caller: the backend, the runtime, or the holds Lambda, each with its own role.'],
  ['Authorize the traveler', 'traveler_identity_bindings in Aurora grants that subject a traveler. A missing grant fails before any row-level scope is set, and both allow and deny land in traveler_access_audit.'],
  ['Scope every row', 'Row-Level Security filters rows to the authorized traveler under the least-privilege meridian_app role, inside one Data API transaction.'],
  ['Decide every tool call', 'AgentCore Gateway serves the tools over MCP with SigV4; its Cedar policy engine, MeridianGovernance in ENFORCE mode, decides each call on the arguments before any Lambda runs.'],
  ['Make the writer a workload too', 'The MeridianHolds Lambda holds its own grant, sets the traveler scope, steps down to meridian_app and calls create_courtesy_hold, so a retried call replays the same booking.'],
];

const SERVICES: [string, string][] = [
  ['Amazon Aurora PostgreSQL', 'Catalog, traveler profile and preferences, identity bindings and audit, LangGraph checkpoints, journeys, leases and holds. pgvector HNSW for retrieval; RLS for scope; the RDS Data API as the connectionless transport.'],
  ['Amazon Bedrock AgentCore Runtime', 'Hosts the Phase 4 Strands agent in its own microVM with the AWS Distro for OpenTelemetry attached.'],
  ['Amazon Bedrock AgentCore Gateway', 'Serves the three tools over MCP with IAM authorization and names them Target___tool.'],
  ['Amazon Bedrock AgentCore Policy', 'Cedar policy engine attached to the gateway in ENFORCE mode; default deny.'],
  ['Amazon Bedrock AgentCore Memory', 'Semantic memory strategy over the concierge session, namespaced per traveler and conversation.'],
  ['Amazon Bedrock', 'Claude Sonnet 5 for the agents, Cohere Embed v4 and Cohere Rerank 3.5 for retrieval.'],
  ['AWS Lambda', 'The semantic search target and the MeridianHolds target behind the gateway.'],
  ['Amazon CloudWatch', 'ADOT spans and structured logs in the runtime’s log group; every Phase 4 span in the trace panel links to its trace id.'],
  ['AWS App Runner and Amazon CloudFront', 'The published site: the FastAPI backend as a container, the Vite build in S3, basic authentication and the API bearer token at the edge.'],
];

function Steps({ items }: { items: [string, string][] }) {
  return (
    <ol className="mds-brief-steps">
      {items.map(([title, body], i) => (
        <li key={title}>
          <span className="mds-brief-index">{i + 1}</span>
          <div><strong>{title}</strong><p>{body}</p></div>
        </li>
      ))}
    </ol>
  );
}

export function SolutionBriefing({ onOpenLadder }: { onOpenLadder: () => void }) {
  return (
    <section className="mds-brief" aria-labelledby="mds-brief-title">
      <header className="mds-brief-head">
        <span className="mds-brief-eyebrow">re:Invent 2026 · fictional inventory, live AWS execution</span>
        <h1 id="mds-brief-title">Solution briefing</h1>
        <p>
          Meridian is a travel concierge that keeps its state in Aurora PostgreSQL and its
          judgment under policy. Five phases take one traveler from a SQL query to a governed
          hold placed by a managed agent, and then to a workflow that survives the worker
          that ran it. This is how it is built and where the boundaries sit.
        </p>
      </header>

      <section className="mds-brief-section">
        <h2>Overview</h2>
        <p>
          Alex Morgan asks for a trip. The answer has to respect what Alex told the concierge
          weeks ago, stay inside a saved budget, come from live inventory, and leave behind a
          record that a second worker or a second person can pick up. The same Aurora cluster
          holds the catalog, the traveler’s memory, the authorization bindings, the workflow
          checkpoints and the holds, so every proof on the System evidence surface is read back
          from the database rather than narrated.
        </p>
        <p>
          <strong>The claim this demonstrates:</strong> an agent is only as trustworthy as the
          state it reads and the boundary it acts through. Retrieval quality, identity,
          policy and durability are separate controls, and each one is checkable on stage.
        </p>
      </section>

      <section className="mds-brief-section">
        <h2>The five phases</h2>
        <ol className="mds-brief-phases">
          {PHASES.map(([name, claim, body], i) => (
            <li key={name}>
              <span className="mds-brief-index">{i + 1}</span>
              <div>
                <span className="mds-brief-phase-name">{name}</span>
                <strong>{claim}</strong>
                <p>{body}</p>
              </div>
            </li>
          ))}
        </ol>
        <button type="button" className="mds-brief-link" onClick={onOpenLadder}>
          Open the capability ladder <ArrowRight size={16} aria-hidden="true" />
        </button>
      </section>

      <section className="mds-brief-section">
        <h2>What the model decides, and what the code decides</h2>
        <div className="mds-brief-columns">
          <div className="mds-brief-panel">
            <h3>The model decides</h3>
            <p>Three things, and nothing else.</p>
            <Steps items={MODEL_DECIDES} />
          </div>
          <div className="mds-brief-panel">
            <h3>The platform decides</h3>
            <p>Each is code or policy the model cannot reach.</p>
            <Steps items={CODE_DECIDES} />
          </div>
        </div>
        <p className="mds-brief-boundary">
          <strong>Boundary:</strong> the runtime overwrites the traveler id, the confirmation flag,
          the budget ceiling and the journey reference on every hold call from the request the
          backend authorized. A confirmed hold is placed by the platform before the model speaks;
          the model narrates the receipt. The model proposes the hold; it cannot confirm it or move
          the ceiling.
        </p>
      </section>

      <section className="mds-brief-section">
        <h2>Tools over MCP</h2>
        <p>
          Three tools, two Lambda targets, one gateway. The runtime discovers them with an MCP
          <code> tools/list</code> call and invokes them with <code>tools/call</code>, signing each
          request with SigV4 from its own execution role. The Phase 5 workflow calls the hold tool
          the same way from the backend.
        </p>
        <div className="mds-brief-tools">
          {TOOLS.map(([name, kind, body]) => (
            <article key={name} className="mds-brief-tool">
              <span className={`mds-brief-kind is-${kind.toLowerCase()}`}>{kind}</span>
              <div><strong><code>{name}</code></strong><p>{body}</p></div>
            </article>
          ))}
        </div>
      </section>

      <section className="mds-brief-section">
        <h2>Governance with Cedar</h2>
        <p>
          Policy in AgentCore attaches a Cedar policy engine, <code>MeridianGovernance</code>, to
          the gateway in ENFORCE mode. The engine is default deny: a tool call runs only when a
          policy permits it. Two policies govern Meridian, written as they are provisioned.
        </p>
        <div className="mds-brief-policies">
          {POLICIES.map(([name, plain, statement]) => (
            <article key={name} className="mds-brief-policy">
              <h3><code>{name}</code></h3>
              <p>{plain}</p>
              <pre>{statement}</pre>
            </article>
          ))}
        </div>
        <p>
          The arguments those conditions read, <code>travelerConfirmed</code>,
          <code> holdMinutes</code>, <code>travelers</code>, <code>totalCents</code> and
          <code> budgetCeilingCents</code>, are required by the tool schema, and the amounts are
          integer cents because Cedar has no floating point type. A denied call comes back to the
          agent as an explained refusal and renders on the trace as <em>Denied by policy</em>.
        </p>
        <p className="mds-brief-boundary">
          <strong>Boundary:</strong> the policies govern the fictional Meridian inventory. They show
          where such rules belong, outside the agent’s code and enforced on every call.
        </p>
      </section>

      <section className="mds-brief-section">
        <h2>Identity, authorization and row-level security</h2>
        <p>Stateful reads and writes pass five independent controls.</p>
        <Steps items={CONTROLS} />
        <p>
          The RLS tab proves the chain live: the same workload is allowed for Alex and denied for the
          decoy traveler, and both decisions are in <code>traveler_access_audit</code>.
        </p>
      </section>

      <section className="mds-brief-section">
        <h2>Memory</h2>
        <p>
          Long-term facts about the traveler (a shellfish allergy, a budget cap, a preference for
          boutique hotels) live in Aurora as <code>traveler_preferences</code> and are recalled under
          RLS. The Phase 4 conversation itself is held by AgentCore Memory with a semantic strategy,
          namespaced per traveler and conversation, so the runtime restores context across turns
          without the backend replaying transcripts.
        </p>
      </section>

      <section className="mds-brief-section">
        <h2>Durable workflow</h2>
        <p>
          Phase 5 is a LangGraph state graph: classify, search, availability, hold, synthesize. Every
          node commits a checkpoint to Aurora through the Data API saver; the worker holds a lease in
          <code> journey_executions</code> and renews it while it runs. The hold node verifies its
          lease, then places the hold through the gateway tool with its checkpointed request id,
          booking id and execution id, so the Lambda checks the lease again inside the write
          transaction and a restarted worker replays the same booking with its original expiry.
        </p>
        <p>
          The scripted proof kills worker one after the hold, watches a second worker be refused
          until the lease clears, and reads back one hold, one booking id and the original expiry.
          The System evidence surface shows the same facts for the session on stage.
        </p>
      </section>

      <section className="mds-brief-section">
        <h2>Observability</h2>
        <p>
          The runtime is instrumented with the AWS Distro for OpenTelemetry. Model calls, gateway tool
          calls and Memory operations become spans in the agent’s CloudWatch log group, and the
          trace panel is fed from those spans rather than from a script: each Phase 4 span carries
          its trace id and a link into CloudWatch. The Phase 5 trace shows the LangGraph nodes, the
          checkpoint writes and the Cedar decision on the workflow’s hold.
        </p>
      </section>

      <section className="mds-brief-section">
        <h2>Holds</h2>
        <p>
          A courtesy hold reserves seats on one package duration for twelve hours. It is a governed
          write: the traveler confirms, the platform pins the arguments, Cedar decides, and the
          Lambda writes under the traveler’s scope. No payment is taken and nothing is sent to a
          supplier; the inventory is fictional and the seats return when the hold expires.
        </p>
      </section>

      <section className="mds-brief-section">
        <h2>Architecture</h2>
        <div className="mds-brief-flow" role="img" aria-label="Browser to CloudFront to App Runner, which runs the FastAPI backend. Phases 1 to 3 read Aurora through the RDS Data API and MCP servers. Phase 4 invokes the AgentCore Runtime agent, which calls Bedrock and the gateway tools under Cedar, backed by two Lambda functions over Aurora, with AgentCore Memory for the session. Phase 5 runs LangGraph in the backend with Aurora checkpoints and calls the same gateway tool for its hold. OpenTelemetry spans go to CloudWatch.">
          <div><span>Browser</span><b>→</b><span>CloudFront</span><b>→</b><span>App Runner</span><b>→</b><strong>FastAPI backend</strong></div>
          <div><strong>Phases 1 to 3</strong><b>→</b><span>SQL, MCP tools, hybrid retrieval</span><b>→</b><span>Aurora PostgreSQL via the RDS Data API</span><b>→</b><span>Bedrock embeddings and rerank</span></div>
          <div><strong>Phase 4</strong><b>→</b><span>AgentCore Runtime (Strands)</span><b>→</b><span>AgentCore Gateway + Cedar</span><b>→</b><span>Lambda targets</span><b>→</b><span>Aurora under RLS</span><b>+</b><span>AgentCore Memory</span></div>
          <div><strong>Phase 5</strong><b>→</b><span>LangGraph in the backend</span><b>→</b><span>Aurora checkpoints and leases</span><b>→</b><span>the same gateway hold tool</span></div>
          <div><strong>OpenTelemetry</strong><b>→</b><span>ADOT on the runtime</span><b>→</b><span>CloudWatch spans and trace ids</span></div>
        </div>
      </section>

      <section className="mds-brief-section">
        <h2>Key AWS services</h2>
        <div className="mds-brief-services">
          {SERVICES.map(([name, role]) => (
            <article key={name}><h3>{name}</h3><p>{role}</p></article>
          ))}
        </div>
      </section>

      <section className="mds-brief-section">
        <h2>Delivery and access</h2>
        <p>
          The site is published behind CloudFront with basic authentication and a bearer token
          injected at the edge for the API; the backend runs as a container on App Runner in the same
          region as Aurora and AgentCore, and reaches them with its own instance role. Locally the same
          backend runs on port 8000 behind the Vite dev server. The AgentCore project is declarative:
          the runtime, gateway, targets, policy engine and memory are one <code>agentcore.json</code>
          deployed with the AgentCore CLI and CDK.
        </p>
      </section>
    </section>
  );
}
