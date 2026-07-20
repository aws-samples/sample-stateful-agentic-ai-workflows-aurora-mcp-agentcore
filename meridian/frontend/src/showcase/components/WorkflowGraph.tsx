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

function stepForNode(node: GraphNodeName, pathNodes: GraphNodeName[]): number | null {
  if (!WORKFLOW_NODES.includes(node as NodeName)) return null;
  const workflowPath = pathNodes.filter((n): n is NodeName => WORKFLOW_NODES.includes(n as NodeName));
  const index = workflowPath.indexOf(node as NodeName);
  return index >= 0 ? index + 1 : null;
}

function isWorkflowNode(node: GraphNodeName): node is NodeName {
  return WORKFLOW_NODES.includes(node as NodeName);
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

  // The caller only mounts this at Phase 5 with spans present, so an empty
  // node set means workflow spans arrived but none matched the expected node
  // labels (e.g. a backend span-title change). Surface that explicitly rather
  // than silently hiding the entire Phase 5 proof surface on stage.
  if (litNodes.size === 0) {
    return (
      <div className="mds-wfgraph is-unmatched" role="status">
        <div className="mds-wfgraph-head">
          <span className="mds-wfgraph-title">LangGraph route</span>
        </div>
        <p className="mds-wfgraph-empty">
          Workflow spans received, but node labels were not recognized. Check
          that the backend emits “Workflow node: …” span titles.
        </p>
      </div>
    );
  }

  const branchIntents = [
    ['search', 'search'],
    ['plan', 'plan'],
    ['availability', 'avail'],
    ['memory_recall', 'memory'],
  ] as const;
  const workflowPathNodes = pathNodes.filter(isWorkflowNode);

  return (
    <div className="mds-wfgraph" role="img" aria-label="LangGraph workflow path">
      <div className="mds-wfgraph-head">
        <span className="mds-wfgraph-title">LangGraph route</span>
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

      <div className="mds-wfgraph-route-summary" aria-label="Executed route from START to END">
        <span className={litNodes.has('start') ? 'is-lit' : ''}>START</span>
        <i aria-hidden="true" />
        <b>{workflowPathNodes.length} steps</b>
        <i aria-hidden="true" />
        <span className={litNodes.has('end') ? 'is-lit' : ''}>END</span>
      </div>

      <div className="mds-wfgraph-route" aria-label="Executed workflow nodes">
        {workflowPathNodes.map((node) => {
          const visited = litNodes.has(node);
          const current = currentNode === node;
          const next = nextNode === node;
          const step = stepForNode(node, pathNodes);
          const fact = nodeFacts.get(node as NodeName);
          const checkpointed = checkpointAfter.has(node as NodeName);

          return (
            <div className="mds-wfgraph-route-row" key={node}>
              <div
                className={[
                  'mds-wfgraph-route-node',
                  visited ? 'is-visited' : 'is-pending',
                  current ? 'is-current' : '',
                  next ? 'is-next' : '',
                ].filter(Boolean).join(' ')}
              >
                <span className="mds-wfgraph-route-step">
                  {step}
                </span>
                <span className="mds-wfgraph-route-copy">
                  <b>{NODE_LABELS[node]}</b>
                  <span className="mds-wfgraph-route-meta">
                    {fact && <em>{fact}</em>}
                    {checkpointed && (
                      <span className="mds-wfgraph-route-ckpt">
                        checkpoint
                      </span>
                    )}
                  </span>
                </span>
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
