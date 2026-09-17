import { useState } from 'react';
import './briefingArchitecture.css';

const FOCUS_VIEWS = {
  context: {
    label: 'Trusted context',
    steps: [
      ['HTTP principal', 'The application binds the caller to the traveler. A prompt cannot choose another traveler.'],
      ['Authorized recall', 'Aurora traveler grants and row-level security scope profile and preference reads.'],
      ['Managed conversation', 'AgentCore Runtime receives the scoped context; AgentCore Memory retains conversation context.'],
    ],
    boundary: 'Preferences and conversation history guide a turn. They are separate from execution checkpoints and booking receipts.',
  },
  action: {
    label: 'Governed action',
    steps: [
      ['Application confirmation', 'The clicked action supplies the traveler, party, budget and explicit confirmation.'],
      ['Runtime → Gateway', 'The managed agent calls the tool. Pinned arguments and Cedar policy govern the call before execution.'],
      ['Lambda → Aurora', 'The transaction checks inventory and records the hold. Read the receipt to verify the write.'],
    ],
    boundary: 'A permitted tool call is not proof of a committed booking. The Aurora record supplies that evidence.',
  },
  recovery: {
    label: 'Durable recovery',
    steps: [
      ['Checkpoint the thread', 'LangGraph runs in FastAPI and saves progress plus the intended hold in Aurora.'],
      ['Replace the worker', 'The previous execution releases its lease or the lease expires. The replacement claims the same thread.'],
      ['Resume the same intent', 'The governed hold reuses its request identity. A lost acknowledgement can be reconciled without a second hold.'],
    ],
    boundary: 'The checkpoint restores execution. The transaction and stable request identity protect this business action; they do not promise universal exactly-once execution.',
  },
} as const;


/** Phase 5 runs in FastAPI and shares Phase 4's governed hold path. */
export function BriefingArchitecture() {
  const [focus, setFocus] = useState<'all' | keyof typeof FOCUS_VIEWS>('all');
  const detail = focus === 'all' ? null : FOCUS_VIEWS[focus];
  const node = (x: number, y: number, width: number, title: string, lines: string[], icon?: string, accent = false) => (
    <g transform={`translate(${x} ${y})`} className={`mds-brief-arch-node${accent ? ' is-accent' : ''}`}>
      <rect width={width} height={76} rx={9} />
      {icon && <image href={`/brand/aws-2026-07-31/${icon}.svg`} x={12} y={9} width={32} height={32} />}
      <text x={icon ? 54 : 14} y={29} className="mds-brief-arch-title">{title}</text>
      {lines.map((line, index) => <text x={14} y={52 + index * 15} key={line}>{line}</text>)}
    </g>
  );
  return <figure className="mds-brief-architecture">
    <div className="mds-brief-arch-controls" role="group" aria-label="Architecture focus">
      <button type="button" aria-pressed={focus === 'all'} onClick={() => setFocus('all')}>Full architecture</button>
      {(Object.entries(FOCUS_VIEWS) as [keyof typeof FOCUS_VIEWS, typeof FOCUS_VIEWS[keyof typeof FOCUS_VIEWS]][]).map(([id, view]) =>
        <button type="button" key={id} aria-pressed={focus === id} onClick={() => setFocus(id)}>{view.label}</button>)}
    </div>
    {detail && <div className="mds-brief-arch-focus">
      <h3>{detail.label}</h3>
      <ol>{detail.steps.map(([title, text], index) => <li key={title}><span>{index + 1}</span><h4>{title}</h4><p>{text}</p></li>)}</ol>
      <p className="mds-brief-arch-boundary">{detail.boundary}</p>
    </div>}
    <div hidden={focus !== 'all'} className="mds-brief-arch-scroll" tabIndex={0} role="region" aria-label="Meridian architecture diagram. Scroll horizontally on smaller screens.">
      <svg viewBox="0 0 1020 442" role="img" aria-labelledby="meridian-arch-title meridian-arch-description">
        <title id="meridian-arch-title">Meridian request and state architecture</title>
        <desc id="meridian-arch-description">Browser to CloudFront to FastAPI on App Runner. CloudFront also delivers the static site from S3. Phase 4 invokes Strands in AgentCore Runtime, which calls Gateway with Cedar policies, then Lambda targets and Aurora. Phase 5 runs LangGraph in FastAPI, saves checkpoints and leases in Aurora, and calls the same Gateway hold tool. Bedrock supplies model inference and retrieval models. AgentCore Memory stores Phase 4 conversation context.</desc>
        <defs><marker id="meridian-brief-arrow" viewBox="0 0 10 10" refX={9} refY={5} markerWidth={6} markerHeight={6} orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" /></marker></defs>
        <g className="mds-brief-arch-edges" markerEnd="url(#meridian-brief-arrow)">
          <path d="M156 58H190" /><path d="M366 58H406" />
          <path className="is-secondary" d="M278 20V8H864V20" />
          <path d="M512 96V118H224V160" />
          <path d="M244 198H288" /><path d="M510 198H550" /><path d="M748 198H792" />
          <path d="M244 333H264V210H288" />
          <path className="is-secondary" d="M132 236V264H400V295" />
          <path className="is-secondary" d="M216 236V250H649V295" />
          <path className="is-secondary" d="M132 371V412H988V236" />
        </g>
        {node(20, 20, 136, 'Browser', ['Travel concierge'])}
        {node(190, 20, 176, 'CloudFront', ['Viewer access + routing'], 'cloudfront')}
        {node(406, 20, 284, 'FastAPI · App Runner', ['Phases 1–3: SQL, MCP, retrieval', 'Invokes Phase 4 · runs Phase 5'], 'app-runner')}
        {node(734, 20, 266, 'Amazon S3', ['Static site via CloudFront'], 's3')}
        <text x={20} y={146} className="mds-brief-arch-label">Phase 4 · managed concierge</text>
        <text x={299} y={146} className="mds-brief-arch-label">MCP over HTTPS · IAM signed</text>
        {node(20, 160, 224, 'AgentCore Runtime', ['Strands agent', 'ADOT traces → CloudWatch'], 'agentcore', true)}
        {node(288, 160, 222, 'Gateway + Policy', ['Four MCP tools', 'Cedar before execution'], 'agentcore', true)}
        {node(550, 160, 198, 'Lambda targets', ['Semantic search', 'Details, holds and booking'], 'lambda')}
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
    <figcaption hidden={focus !== 'all'}>Solid lines show the request path. Dashed lines show supporting calls and persisted workflow state.</figcaption>
  </figure>;
}
