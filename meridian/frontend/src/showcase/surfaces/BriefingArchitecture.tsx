import { useId } from 'react';
import { ChevronDown } from 'lucide-react';
import { ServiceMark } from '../components/ServiceMark';
import './briefingArchitecture.css';

type ArchNode = {
  x: number; y: number; width: number; height?: number; title: string; lines: string[];
  icon: string; step?: number;
};

function ArchIcon({ icon }: { icon: string }) {
  if (!icon.startsWith('agentcore-')) {
    return <image href={`/brand/aws-2026-07-31/${icon}.svg`} x={16} y={16} width={32} height={32} />;
  }
  const name = icon.slice('agentcore-'.length);
  return <>
    <image className="mds-brief-icon-light" href={`/brand/agentcore-purple/${name}.svg`} x={16} y={16} width={32} height={32} />
    <image className="mds-brief-icon-dark" href={`/brand/agentcore-purple/${name}-dark.svg`} x={16} y={16} width={32} height={32} />
  </>;
}

function Node({ x, y, width, height = 112, title, lines, icon, step }: ArchNode) {
  return <g transform={`translate(${x} ${y})`} className="mds-brief-arch-node">
    <rect width={width} height={height} rx={8} />
    {step && <g className="mds-brief-arch-number" transform={`translate(${width - 16} 0)`}><circle r={14} /><text textAnchor="middle" y={5}>{step}</text></g>}
    <ArchIcon icon={icon} />
    <text x={58} y={38} className="mds-brief-arch-title">{title}</text>
    {lines.map((line, index) => <text x={16} y={70 + index * 23} key={line}>{line}</text>)}
  </g>;
}

/** Phase 4 and Phase 5 each run in their own AgentCore Runtime. Both use governed tools. */
export function BriefingArchitecture() {
  const id = useId();
  return <figure className="mds-brief-architecture">
    <svg className="mds-brief-arch-diagram" viewBox="0 -40 1120 450" role="img" aria-labelledby={`${id}-title ${id}-description`}>
      <title id={`${id}-title`}>Meridian request and state architecture</title>
      <desc id={`${id}-description`}>Amazon Cognito signs the traveler in and issues an access token. FastAPI on App Runner checks the token and invokes the Phase 4 Strands agent and the Phase 5 Strands Graph workflow, each in its own AgentCore Runtime. Both call AgentCore Gateway, which checks the token, pins the traveler and applies Cedar policy before Lambda tools access Amazon Aurora PostgreSQL. FastAPI also reads catalog and traveler data through the Data API. The workflow Runtime saves its steps and worker leases in Amazon Aurora. Bedrock supplies models and retrieval; AgentCore Memory retains Phase 4 conversation context.</desc>
      <defs><marker id={`${id}-arrow`} viewBox="0 0 10 10" refX={9} refY={5} markerWidth={6} markerHeight={6} orient="auto"><path d="M0 0 10 5 0 10z" /></marker></defs>
      <g className="mds-brief-arch-edges" markerEnd={`url(#${id}-arrow)`}>
        <path d="M150 48V118" />
        <path d="M234 158H250V94H268" />
        <path d="M234 190H250V258H268" />
        <path d="M504 94H578" />
        <path d="M504 258H538V110H578" />
        <path d="M702 154V202" />
        <path d="M826 258H866" />
        <path className="is-secondary" d="M126 230V358H984V286" />
        <path className="is-secondary" d="M386 314V358" />
      </g>
      <Node x={18} y={-36} width={216} height={84} title="Amazon Cognito" lines={['Traveler sign-in']} icon="cognito" />
      <text x={160} y={76} className="mds-brief-arch-label">access token</text>
      <text x={18} y={96} className="mds-brief-arch-label">Phases 1 to 3</text>
      <Node x={18} y={118} width={216} title="FastAPI" lines={['SQL / MCP / retrieval', 'Identity + confirmation']} icon="app-runner" step={1} />
      <text x={268} y={18} className="mds-brief-arch-label">Phase 4: managed concierge</text>
      <Node x={268} y={42} width={236} title="AgentCore Runtime" lines={['Strands agent', 'Conversation context']} icon="agentcore-runtime" step={2} />
      <text x={268} y={188} className="mds-brief-arch-label">Phase 5: durable workflow</text>
      <Node x={268} y={202} width={236} title="AgentCore Runtime" lines={['Strands Graph workflow', 'Saved steps + worker lease']} icon="agentcore-runtime" />
      <text x={578} y={18} className="mds-brief-arch-label">Shared governed tool path</text>
      <Node x={578} y={42} width={248} title="AgentCore Gateway" lines={['Token checked, traveler pinned', 'Cedar policy on every call']} icon="agentcore-gateway" step={3} />
      <Node x={578} y={202} width={248} title="AWS Lambda" lines={['Search, details, loyalty', 'Hold, confirm, release']} icon="lambda" step={4} />
      <Node x={866} y={174} width={236} title="Amazon Aurora" lines={['Catalog + traveler state', 'Saved steps + bookings']} icon="aurora" step={5} />
      <text x={250} y={390} className="mds-brief-arch-label">Direct Data API: catalog, preferences, availability, saved steps, leases</text>
    </svg>
    <ol className="mds-brief-arch-mobile" aria-label="Meridian request and state architecture">
      <li><strong>Amazon Cognito - sign the traveler in</strong><p>Issues the access token that FastAPI, both Runtimes and the Gateway check in the signed-in release.</p></li>
      <li><strong>1. FastAPI - bind the traveler</strong><p>Establish identity and capture confirmation.</p></li>
      <li><strong>2. AgentCore Runtime - run the agent</strong><p>Phase 4 uses Strands with AgentCore Memory.</p></li>
      <li><strong>3. AgentCore Gateway - authorize the call</strong><p>Check the token and pin the traveler. AgentCore Policy evaluates Cedar before the tool runs.</p></li>
      <li><strong>4. AWS Lambda - execute the tool</strong><p>Validate the input and recheck the action at the target.</p></li>
      <li><strong>5. Amazon Aurora - record the outcome</strong><p>Read scoped facts and commit replay-safe business writes.</p></li>
      <li><strong>Phase 5: Strands Graph in its own AgentCore Runtime</strong><p>Searches, holds and releases through the Gateway under Cedar. Its saved steps and worker leases live in Amazon Aurora.</p></li>
    </ol>
    <figcaption><span className="mds-brief-arch-legend">Signed-in release shown: FastAPI, both Runtimes and the Gateway each check the access token. Phase 4: follow 1 to 5. Phase 5 joins at Gateway. Dashed path: direct Data API access.</span></figcaption>
    <details className="mds-brief-platform">
      <summary>Supporting services &amp; app delivery<ChevronDown size={18} aria-hidden="true" /></summary>
    <div className="mds-brief-arch-delivery" aria-label="Published application delivery">
      <span><ServiceMark name="cloudfront" size={28} /><span><strong>CloudFront</strong>Browser access + API routing</span></span>
      <span><ServiceMark name="s3" size={28} /><span><strong>Amazon S3</strong>Static application files</span></span>
      <span><ServiceMark name="app-runner" size={28} /><span><strong>App Runner</strong>FastAPI application</span></span>
    </div>
    <div className="mds-brief-arch-support">
      <div><ServiceMark name="bedrock" size={28} /><p><strong>Amazon Bedrock</strong><span>Agent models, embeddings and reranking</span></p></div>
      <div><ServiceMark name="agentcore-memory" size={28} /><p><strong>AgentCore Memory</strong><span>Phase 4 conversation context</span></p></div>
      <div><ServiceMark name="agentcore-policy" size={28} /><p><strong>AgentCore Policy</strong><span>Cedar checks before tool execution</span></p></div>
    </div>
    </details>
  </figure>;
}
