/** Phase 5 runs in FastAPI and shares Phase 4's governed hold path. */
export function BriefingArchitecture() {
  const node = (x: number, y: number, width: number, title: string, lines: string[], icon?: string, accent = false) => (
    <g transform={`translate(${x} ${y})`} className={`mds-brief-arch-node${accent ? ' is-accent' : ''}`}>
      <rect width={width} height={76} rx={9} />
      {icon && <image href={`/brand/${icon}.svg`} x={14} y={13} width={23} height={23} />}
      <text x={icon ? 46 : 14} y={29} className="mds-brief-arch-title">{title}</text>
      {lines.map((line, index) => <text x={14} y={49 + index * 15} key={line}>{line}</text>)}
    </g>
  );
  return <figure className="mds-brief-architecture">
    <div className="mds-brief-arch-scroll" tabIndex={0} role="region" aria-label="Meridian architecture diagram. Scroll horizontally on smaller screens.">
      <svg viewBox="0 0 1020 442" role="img" aria-labelledby="meridian-arch-title meridian-arch-description">
        <title id="meridian-arch-title">Meridian request and state architecture</title>
        <desc id="meridian-arch-description">Browser to CloudFront to FastAPI on App Runner. CloudFront also delivers the static site from S3. Phase 4 invokes Strands in AgentCore Runtime, which calls Gateway with Cedar policies, then Lambda targets and Aurora. Phase 5 runs LangGraph in FastAPI, saves checkpoints and leases in Aurora, and calls the same Gateway hold tool. Bedrock supplies model inference and retrieval models. AgentCore Memory stores Phase 4 conversation context.</desc>
        <defs><marker id="meridian-brief-arrow" viewBox="0 0 10 10" refX={9} refY={5} markerWidth={6} markerHeight={6} orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" /></marker></defs>
        <g className="mds-brief-arch-edges" markerEnd="url(#meridian-brief-arrow)">
          <path d="M156 58H190" /><path d="M366 58H406" />
          <path className="is-secondary" d="M278 20V8H864V20" />
          <path d="M512 96V118H132V160" />
          <path d="M244 198H288" /><path d="M510 198H550" /><path d="M748 198H792" />
          <path d="M244 333H264V210H288" />
          <path className="is-secondary" d="M132 236V264H400V295" />
          <path className="is-secondary" d="M216 236V250H649V295" />
          <path className="is-secondary" d="M132 371V412H988V236" />
        </g>
        {node(20, 20, 136, 'Browser', ['Travel concierge'])}
        {node(190, 20, 176, 'CloudFront', ['Viewer access + routing'])}
        {node(406, 20, 284, 'FastAPI · App Runner', ['Phases 1–3: SQL, MCP, retrieval', 'Invokes Phase 4 · runs Phase 5'])}
        {node(734, 20, 266, 'Amazon S3', ['Static site via CloudFront'])}
        <text x={20} y={146} className="mds-brief-arch-label">Phase 4 · managed concierge</text>
        <text x={299} y={146} className="mds-brief-arch-label">MCP over HTTPS · IAM signed</text>
        {node(20, 160, 224, 'AgentCore Runtime', ['Strands agent', 'ADOT traces → CloudWatch'], 'agentcore', true)}
        {node(288, 160, 222, 'Gateway + Policy', ['Four MCP tools', 'Cedar before execution'], 'agentcore', true)}
        {node(550, 160, 198, 'Lambda targets', ['Semantic search', 'Details, holds and booking'])}
        {node(792, 160, 208, 'Aurora PostgreSQL', ['Catalog + traveler state', 'Data API · row-level security'], 'aurora', true)}
        <text x={20} y={281} className="mds-brief-arch-label">Phase 5 · durable workflow</text>
        {node(20, 295, 224, 'LangGraph · FastAPI', ['Classify → search → availability', 'Hold → synthesize'])}
        {node(288, 295, 222, 'Amazon Bedrock', ['Agent model inference', 'Embeddings + reranking'], 'bedrock')}
        {node(550, 295, 198, 'AgentCore Memory', ['Phase 4 conversation context', 'Traveler + conversation scope'], 'agentcore')}
        <text x={790} y={301} className="mds-brief-arch-label">Aurora also persists</text>
        <text x={790} y={325}>Workflow checkpoints</text><text x={790} y={345}>Worker leases + hold intent</text>
        <text x={284} y={405} className="mds-brief-arch-label">Checkpoint and resume the same thread · preserve the same hold</text>
      </svg>
    </div>
    <figcaption>Solid lines show the request path. Dashed lines show supporting calls and persisted workflow state.</figcaption>
  </figure>;
}
