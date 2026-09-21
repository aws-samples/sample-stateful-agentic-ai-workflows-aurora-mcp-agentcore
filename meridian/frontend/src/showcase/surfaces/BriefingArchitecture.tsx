import { useId } from 'react';
import { ServiceMark } from '../components/ServiceMark';
import './briefingArchitecture.css';

/** Phase 5 runs in FastAPI, not AgentCore Runtime. Both use governed tools. */
export function BriefingArchitecture() {
  const id = useId();
  const node = (x: number, y: number, width: number, title: string, lines: string[], icon?: string) => (
    <g transform={`translate(${x} ${y})`} className="mds-brief-arch-node">
      <rect width={width} height={112} rx={8} />
      {icon && <image href={`/brand/aws-2026-07-31/${icon}.svg`} x={16} y={16} width={32} height={32} />}
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
      <text x={18} y={104} className="mds-brief-arch-label">Phases 1–3 · data + retrieval</text>
      {node(18, 118, 200, 'FastAPI', ['SQL · MCP · retrieval', 'Identity + confirmation'], 'app-runner')}
      <text x={268} y={28} className="mds-brief-arch-label">Phase 4 · managed concierge</text>
      {node(268, 42, 236, 'AgentCore Runtime', ['Strands agent', 'Conversation context'], 'agentcore')}
      <text x={268} y={188} className="mds-brief-arch-label">Phase 5 · durable workflow</text>
      {node(268, 202, 236, 'LangGraph', ['Runs in FastAPI', 'Checkpoints + worker lease'])}
      <text x={578} y={28} className="mds-brief-arch-label">Shared governed tool path</text>
      {node(578, 42, 236, 'Gateway + Policy', ['IAM-signed MCP', 'Cedar before execution'], 'agentcore')}
      {node(578, 202, 236, 'Lambda tools', ['Search + package details', 'Holds + confirmation'], 'lambda')}
      {node(866, 174, 236, 'Aurora PostgreSQL', ['Catalog + traveler state', 'Checkpoints + bookings'], 'aurora')}
      <text x={250} y={390} className="mds-brief-arch-label">Direct Data API access · catalog, scoped preferences and workflow state</text>
    </svg>
    <ol className="mds-brief-arch-mobile" aria-label="Meridian request and state architecture">
      <li><strong>Application · FastAPI on App Runner</strong><p>Binds the workload to the traveler and captures confirmation.</p></li>
      <li><strong>Two execution paths</strong><p>Phase 4: Strands in AgentCore Runtime.<br />Phase 5: LangGraph runs in FastAPI.</p></li>
      <li><strong>Shared governed tools</strong><p>IAM-signed MCP → AgentCore Gateway + Cedar policy → Lambda targets.</p></li>
      <li><strong>Aurora PostgreSQL</strong><p>Catalog, traveler state and bookings. FastAPI also uses the Data API directly for scoped reads, checkpoints and worker leases.</p></li>
    </ol>
    <div className="mds-brief-arch-support">
      <div><ServiceMark name="bedrock" size={28} /><p><strong>Amazon Bedrock</strong><span>Agent models, embeddings and reranking</span></p></div>
      <div><ServiceMark name="agentcore" size={28} /><p><strong>AgentCore Memory</strong><span>Phase 4 conversation context</span></p></div>
    </div>
    <figcaption><span className="mds-brief-arch-legend">Solid: governed request path. Dashed: direct data and workflow state. </span>Aurora enforces traveler scope and replay-safe writes.</figcaption>
  </figure>;
}
