import { useId } from 'react';
import { ServiceMark } from '../components/ServiceMark';
import './briefingArchitecture.css';

/** Phase 5 runs in FastAPI, not AgentCore Runtime. Both use governed tools. */
export function BriefingArchitecture() {
  const id = useId();
  const node = (x: number, y: number, width: number, title: string, lines: string[], icon?: string, step?: number) => (
    <g transform={`translate(${x} ${y})`} className="mds-brief-arch-node">
      <rect width={width} height={112} rx={8} />
      {step && <g className="mds-brief-arch-number" transform={`translate(${width - 16} 0)`}><circle r={14} /><text textAnchor="middle" y={5}>{step}</text></g>}
      {icon && (icon.startsWith('agentcore-') ? <>
        <image className="mds-brief-icon-light" href={`/brand/agentcore-purple/${icon.slice(10)}.png`} x={16} y={16} width={32} height={32} />
        <image className="mds-brief-icon-dark" href={`/brand/agentcore-purple/${icon.slice(10)}-dark.png`} x={16} y={16} width={32} height={32} />
      </> : <image href={`/brand/aws-2026-07-31/${icon}.svg`} x={16} y={16} width={32} height={32} />)}
      <text x={icon ? 58 : 16} y={38} className="mds-brief-arch-title">{title}</text>
      {lines.map((line, index) => <text x={16} y={70 + index * 23} key={line}>{line}</text>)}
    </g>
  );
  return <figure className="mds-brief-architecture">
    <div className="mds-brief-arch-delivery" aria-label="Published application delivery">
      <span><ServiceMark name="cloudfront" size={28} /><span><strong>CloudFront</strong>Browser access + API routing</span></span>
      <span><ServiceMark name="s3" size={28} /><span><strong>Amazon S3</strong>Static application files</span></span>
      <span><ServiceMark name="app-runner" size={28} /><span><strong>App Runner</strong>FastAPI application</span></span>
    </div>
    <svg className="mds-brief-arch-diagram" viewBox="0 0 1120 410" role="img" aria-labelledby={`${id}-title ${id}-description`}>
      <title id={`${id}-title`}>Meridian request and state architecture</title>
      <desc id={`${id}-description`}>FastAPI on App Runner invokes the Phase 4 Strands agent in AgentCore Runtime and runs the Phase 5 LangGraph workflow itself. Both call AgentCore Gateway, where Cedar policy governs calls before Lambda tools access Aurora PostgreSQL. FastAPI also reads catalog and traveler data through the Data API and persists workflow checkpoints and leases in Aurora. Bedrock supplies models and retrieval; AgentCore Memory retains Phase 4 conversation context.</desc>
      <defs><marker id={`${id}-arrow`} viewBox="0 0 10 10" refX={9} refY={5} markerWidth={6} markerHeight={6} orient="auto"><path d="M0 0 10 5 0 10z" /></marker></defs>
      <g className="mds-brief-arch-edges" markerEnd={`url(#${id}-arrow)`}>
        <path d="M218 158H240V94H268" />
        <path d="M218 190H240V258H268" />
        <path d="M504 94H578" />
        <path d="M504 258H538V110H578" />
        <path d="M696 154V202" />
        <path d="M814 258H866" />
        <path className="is-secondary" d="M118 230V358H984V286" />
        <path className="is-secondary" d="M386 314V358" />
      </g>
      <text x={18} y={90} className="mds-brief-arch-label">Phases 1–3 · data + retrieval</text>
      {node(18, 118, 200, 'FastAPI', ['SQL / MCP / retrieval', 'Identity + confirmation'], 'app-runner', 1)}
      <text x={268} y={18} className="mds-brief-arch-label">Phase 4 · managed concierge</text>
      {node(268, 42, 236, 'AgentCore Runtime', ['Strands agent', 'Conversation context'], 'agentcore-runtime', 2)}
      <text x={268} y={188} className="mds-brief-arch-label">Phase 5 · durable workflow</text>
      {node(268, 202, 236, 'LangGraph', ['Runs in FastAPI', 'Checkpoints + worker lease'])}
      <text x={578} y={18} className="mds-brief-arch-label">Shared governed tool path</text>
      {node(578, 42, 236, 'AgentCore Gateway', ['IAM-signed MCP', 'Policy: Cedar checks'], 'agentcore-gateway', 3)}
      {node(578, 202, 236, 'AWS Lambda', ['Search + package details', 'Holds + confirmation'], 'lambda', 4)}
      {node(866, 174, 236, 'Aurora PostgreSQL', ['Catalog + traveler state', 'Checkpoints + bookings'], 'aurora', 5)}
      <text x={250} y={390} className="mds-brief-arch-label">Direct Data API access · catalog, scoped preferences and workflow state</text>
    </svg>
    <ol className="mds-brief-arch-mobile" aria-label="Meridian request and state architecture">
      <li><strong>1. FastAPI - bind the traveler</strong><p>Establish identity and capture confirmation.</p></li>
      <li><strong>2. AgentCore Runtime - run the agent</strong><p>Phase 4 uses Strands with AgentCore Memory.</p></li>
      <li><strong>3. AgentCore Gateway - authorize the call</strong><p>AgentCore Policy evaluates Cedar before the tool runs.</p></li>
      <li><strong>4. AWS Lambda - execute the tool</strong><p>Validate the input and recheck the action at the target.</p></li>
      <li><strong>5. Aurora PostgreSQL - record the outcome</strong><p>Read scoped facts and commit replay-safe business writes.</p></li>
      <li><strong>Phase 5: LangGraph in FastAPI</strong><p>Uses the same Gateway tool path. The Data API persists checkpoints and worker leases in Aurora.</p></li>
    </ol>
    <div className="mds-brief-arch-support">
      <div><ServiceMark name="bedrock" size={28} /><p><strong>Amazon Bedrock</strong><span>Agent models, embeddings and reranking</span></p></div>
      <div><ServiceMark name="agentcore-memory" size={28} /><p><strong>AgentCore Memory</strong><span>Phase 4 conversation context</span></p></div>
      <div><ServiceMark name="agentcore-policy" size={28} /><p><strong>AgentCore Policy</strong><span>Cedar checks before tool execution</span></p></div>
    </div>
    <figcaption><span className="mds-brief-arch-legend">Phase 4: follow 1–5. Phase 5: LangGraph joins at Gateway. Solid: requests. Dashed: Data API reads and workflow state.</span></figcaption>
  </figure>;
}
