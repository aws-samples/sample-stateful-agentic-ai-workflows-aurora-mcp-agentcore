/**
 * Phase 5 LangGraph execution view.
 *
 * The right rail is narrow, so this renders the executed route as large,
 * readable cards instead of a dense canvas. The route, facts, checkpoints, and
 * replay focus still come from real OrchestrationAgent trace spans.
 */
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import type { ShowcaseTraceSpan } from '../lib/showcaseAdapters';

type NodeName = 'classify' | 'search' | 'availability' | 'memory_recall' | 'synthesize';
type GraphNodeName = 'start' | NodeName | 'end';

const WORKFLOW_NODES: NodeName[] = ['classify', 'search', 'availability', 'memory_recall', 'synthesize'];

const NODE_LABELS: Record<GraphNodeName, string> = {
  start: 'START',
  classify: 'Classify intent',
  search: 'Retrieve packages',
  availability: 'Check availability',
  memory_recall: 'Recall memory',
  synthesize: 'Compose answer',
  end: 'END',
};

const INTENT_PATHS: Record<string, NodeName[]> = {
  search: ['classify', 'search', 'synthesize'],
  plan: ['classify', 'search', 'availability', 'synthesize'],
  availability: ['classify', 'availability', 'synthesize'],
  memory_recall: ['classify', 'memory_recall', 'synthesize'],
};

const ROUTE_LABELS: Record<string, string> = {
  'start-classify': 'invoke graph',
  'classify-search': 'intent=search | plan',
  'classify-availability': 'intent=availability',
  'classify-memory_recall': 'intent=memory',
  'search-availability': 'plan step 2',
  'search-synthesize': 'search result',
  'availability-synthesize': 'availability result',
  'memory_recall-synthesize': 'memory result',
  'synthesize-end': 'response',
};

const NODE_RE = /Workflow node:\s*(classify|search|availability|memory_recall|synthes)/i;

interface GraphActivation {
  litNodes: Set<GraphNodeName>;
  intent: string | null;
  checkpointer: string | null;
  checkpointAfter: Set<NodeName>;
  currentNode: GraphNodeName | null;
  nextNode: GraphNodeName | null;
  pathNodes: GraphNodeName[];
  nodeFacts: Map<NodeName, string>;
}

function spanNode(span: ShowcaseTraceSpan): NodeName | null {
  const field = span.fields.find((f) => f.label.toLowerCase() === 'node')?.value;
  if (field && WORKFLOW_NODES.includes(field as NodeName)) return field as NodeName;
  const match = NODE_RE.exec(span.name || '');
  if (!match) return null;
  return (match[1].startsWith('synthes') ? 'synthesize' : match[1]) as NodeName;
}

function deriveActivation(
  spans: ShowcaseTraceSpan[],
  isReplaying: boolean,
  replayIndex: number,
): GraphActivation {
  const litNodes = new Set<GraphNodeName>();
  const checkpointAfter = new Set<NodeName>();
  const nodeFacts = new Map<NodeName, string>();
  let intent: string | null = null;
  let checkpointer: string | null = null;
  let lastNode: NodeName | null = null;

  spans.forEach((span, index) => {
    if (isReplaying && replayIndex >= 0 && index > replayIndex) return;

    const node = spanNode(span);
    if (node) {
      litNodes.add(node);
      lastNode = node;
      const fact = nodeFact(node, span);
      if (fact) nodeFacts.set(node, fact);
      const intentField = fieldValue(span, 'intent');
      if (intentField) intent = intentField;
    }

    if (/checkpoint/i.test(span.name || '')) {
      const ck = fieldValue(span, 'checkpointer');
      if (ck) checkpointer = ck;
      if (lastNode) checkpointAfter.add(lastNode);
    }
  });

  const workflowPath: NodeName[] = intent && INTENT_PATHS[intent]
    ? INTENT_PATHS[intent]
    : ['classify', 'synthesize'];
  const pathNodes: GraphNodeName[] = ['start', ...workflowPath, 'end'];

  if (litNodes.size > 0) litNodes.add('start');
  if (litNodes.has('synthesize')) litNodes.add('end');

  const nextNode = isReplaying ? pathNodes.find((node) => !litNodes.has(node)) ?? null : null;
  const currentNode = isReplaying
    ? (lastNode ?? (litNodes.has('start') ? 'start' : null))
    : null;

  return {
    litNodes,
    intent,
    checkpointer,
    checkpointAfter,
    currentNode,
    nextNode,
    pathNodes,
    nodeFacts,
  };
}

function fieldValue(span: ShowcaseTraceSpan, label: string): string | null {
  return span.fields.find((f) => f.label.toLowerCase() === label)?.value ?? null;
}

function nodeFact(node: NodeName, span: ShowcaseTraceSpan): string | null {
  if (node === 'classify') {
    const intent = fieldValue(span, 'intent');
    return intent ? `intent=${intent}` : null;
  }
  if (node === 'search') {
    const packages = fieldValue(span, 'packages');
    return packages ? `packages=${packages}` : compactDetails(span.details);
  }
  if (node === 'availability') {
    const rows = fieldValue(span, 'rows');
    const step = fieldValue(span, 'step');
    return [rows ? `rows=${rows}` : null, step].filter(Boolean).join(' · ') || compactDetails(span.details);
  }
  if (node === 'memory_recall') return compactDetails(span.details) ?? 'context recalled';
  if (node === 'synthesize') {
    const packages = fieldValue(span, 'packages');
    return packages ? `packages=${packages}` : 'response ready';
  }
  return null;
}

function compactDetails(details?: string): string | null {
  if (!details) return null;
  return details.length > 34 ? `${details.slice(0, 34)}...` : details;
}

function edgeLabel(from: GraphNodeName, to: GraphNodeName): string {
  return ROUTE_LABELS[`${from}-${to}`] ?? 'then';
}

function stepForNode(node: GraphNodeName, pathNodes: GraphNodeName[]): number | null {
  if (!WORKFLOW_NODES.includes(node as NodeName)) return null;
  const workflowPath = pathNodes.filter((n): n is NodeName => WORKFLOW_NODES.includes(n as NodeName));
  const index = workflowPath.indexOf(node as NodeName);
  return index >= 0 ? index + 1 : null;
}

export function WorkflowGraph({ state }: { state: MeridianShowcaseState }) {
  const {
    litNodes,
    intent,
    checkpointer,
    checkpointAfter,
    currentNode,
    nextNode,
    pathNodes,
    nodeFacts,
  } = deriveActivation(state.traceSpans, state.isReplaying, state.replayIndex);

  if (litNodes.size === 0) return null;

  const branchIntents = [
    ['search', 'search'],
    ['plan', 'plan'],
    ['availability', 'avail'],
    ['memory_recall', 'memory'],
  ] as const;

  return (
    <div className="mds-wfgraph" role="img" aria-label="LangGraph workflow path">
      <div className="mds-wfgraph-head">
        <span className="mds-wfgraph-title">LangGraph StateGraph</span>
        {intent && <span className="mds-wfgraph-intent">intent: {intent}</span>}
      </div>

      <div className="mds-wfgraph-branches" aria-label="Conditional routes">
        <span>Routes</span>
        {branchIntents.map(([branch, label]) => (
          <b key={branch} className={intent === branch ? 'is-active' : ''}>
            {label}
          </b>
        ))}
      </div>

      <div className="mds-wfgraph-route" aria-label="Executed route">
        {pathNodes.map((node, index) => {
          const visited = litNodes.has(node);
          const current = currentNode === node;
          const next = nextNode === node;
          const step = stepForNode(node, pathNodes);
          const fact = nodeFacts.get(node as NodeName);
          const checkpointed = checkpointAfter.has(node as NodeName);
          const previous = pathNodes[index - 1];

          return (
            <div className="mds-wfgraph-route-row" key={node}>
              {previous && (
                <div className={`mds-wfgraph-route-edge${visited ? ' is-active' : ''}`}>
                  <span>{edgeLabel(previous, node)}</span>
                </div>
              )}
              <div
                className={[
                  'mds-wfgraph-route-node',
                  visited ? 'is-visited' : 'is-pending',
                  current ? 'is-current' : '',
                  next ? 'is-next' : '',
                ].filter(Boolean).join(' ')}
              >
                <span className="mds-wfgraph-route-step">
                  {step ?? (node === 'start' ? 'S' : 'E')}
                </span>
                <span className="mds-wfgraph-route-copy">
                  <b>{NODE_LABELS[node]}</b>
                  {fact && <em>{fact}</em>}
                </span>
                {checkpointed && (
                  <span className="mds-wfgraph-route-ckpt">
                    checkpoint
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {checkpointer && (
        <div className="mds-wfgraph-foot">
          <span className="mds-wfgraph-ckpt-dot" aria-hidden="true" />
          checkpointed · {checkpointer}
        </div>
      )}
    </div>
  );
}
