/**
 * WorkflowGraph — Phase 5 (LangGraph) StateGraph visualization.
 *
 * The topology is fixed and known (START -> classify -> branch -> synthesize
 * -> END), so the layout is hand-laid. Which nodes, route labels, checkpoints,
 * and step numbers light up is derived from real OrchestrationAgent trace spans:
 *   - node spans:        name "Workflow node: <name>" + field {node: <name>}
 *   - classified intent: field {intent: search|plan|availability|memory_recall}
 *   - checkpoints:       span name "Checkpoint · PostgresSaver.put" + the
 *                        ACTUAL checkpointer kind in field {checkpointer: ...}
 *
 * During "Replay trace" the activation is keyed on replayIndex so the path
 * lights up node-by-node in step with the span replay.
 *
 * Backend reference: backend/agents/orchestration_05/workflow.py
 */
import { motion } from 'framer-motion';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import type { ShowcaseTraceSpan } from '../lib/showcaseAdapters';

const prefersReducedMotion =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

type NodeName = 'classify' | 'search' | 'availability' | 'memory_recall' | 'synthesize';
type GraphNodeName = 'start' | NodeName | 'end';

interface NodeLayout {
  x: number;
  y: number;
  width: number;
  height: number;
  label: string;
}

// Hand-laid coordinates on a 0..420 x 210 canvas. START/END bookend the
// workflow, while the middle column shows the conditional branches.
const NODE_LAYOUT: Record<GraphNodeName, NodeLayout> = {
  start: { x: 24, y: 105, width: 42, height: 24, label: 'START' },
  classify: { x: 92, y: 105, width: 74, height: 38, label: 'classify' },
  search: { x: 205, y: 46, width: 86, height: 40, label: 'search' },
  availability: { x: 205, y: 105, width: 104, height: 40, label: 'availability' },
  memory_recall: { x: 205, y: 164, width: 94, height: 40, label: 'memory' },
  synthesize: { x: 320, y: 105, width: 88, height: 40, label: 'synthesize' },
  end: { x: 406, y: 105, width: 42, height: 24, label: 'END' },
};

const WORKFLOW_NODES: NodeName[] = ['classify', 'search', 'availability', 'memory_recall', 'synthesize'];

// The edges that light for each classified intent. Mirrors the conditional
// routing in workflow.py: plan = classify→search→availability→synthesize;
// search = classify→search→synthesize; etc.
const INTENT_EDGES: Record<string, [NodeName, NodeName][]> = {
  search: [['classify', 'search'], ['search', 'synthesize']],
  plan: [
    ['classify', 'search'],
    ['search', 'availability'],
    ['availability', 'synthesize'],
  ],
  availability: [['classify', 'availability'], ['availability', 'synthesize']],
  memory_recall: [['classify', 'memory_recall'], ['memory_recall', 'synthesize']],
};

const EDGE_LABELS: Record<string, string> = {
  'start-classify': 'invoke',
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

function spanNode(span: ShowcaseTraceSpan): NodeName | null {
  const field = span.fields?.find((f) => f.label.toLowerCase() === 'node')?.value;
  if (field && WORKFLOW_NODES.includes(field as NodeName)) return field as NodeName;
  const m = NODE_RE.exec(span.name || '');
  if (!m) return null;
  return (m[1].startsWith('synthes') ? 'synthesize' : m[1]) as NodeName;
}

interface GraphActivation {
  litNodes: Set<GraphNodeName>;
  activeEdges: [GraphNodeName, GraphNodeName][];
  intent: string | null;
  checkpointer: string | null;
  checkpointAfter: Set<NodeName>;
  currentNode: GraphNodeName | null;
  nextNode: GraphNodeName | null;
  pathNodes: GraphNodeName[];
  nodeFacts: Map<NodeName, string>;
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

  spans.forEach((span, i) => {
    // During replay, only consider spans up to the current replay cursor.
    if (isReplaying && replayIndex >= 0 && i > replayIndex) return;

    const node = spanNode(span);
    if (node) {
      litNodes.add(node);
      lastNode = node;
      const fact = nodeFact(node, span);
      if (fact) nodeFacts.set(node, fact);
      const f = span.fields?.find((x) => x.label.toLowerCase() === 'intent')?.value;
      if (f) intent = f;
    }
    if (/checkpoint/i.test(span.name || '')) {
      const ck = span.fields?.find((x) => x.label.toLowerCase() === 'checkpointer')?.value;
      if (ck) checkpointer = ck;
      if (lastNode) checkpointAfter.add(lastNode);
    }
  });

  const workflowPath: NodeName[] = intent && INTENT_EDGES[intent]
    ? nodesFromEdges(INTENT_EDGES[intent])
    : ['classify', 'synthesize'];
  const pathNodes: GraphNodeName[] = ['start', ...workflowPath, 'end'];

  if (litNodes.size > 0) litNodes.add('start');
  if (litNodes.has('synthesize')) litNodes.add('end');

  const planned: [GraphNodeName, GraphNodeName][] = [
    ['start', 'classify'],
    ...((intent && INTENT_EDGES[intent]) || []),
    ['synthesize', 'end'],
  ];
  const activeEdges = planned.filter(([a, b]) => litNodes.has(a) && litNodes.has(b));
  const nextNode = isReplaying ? pathNodes.find((node) => !litNodes.has(node)) ?? null : null;
  const currentNode = isReplaying
    ? (lastNode ?? (litNodes.has('start') ? 'start' : null))
    : null;

  return {
    litNodes,
    activeEdges,
    intent,
    checkpointer,
    checkpointAfter,
    currentNode,
    nextNode,
    pathNodes,
    nodeFacts,
  };
}

function nodesFromEdges(edges: [NodeName, NodeName][]): NodeName[] {
  const nodes: NodeName[] = [];
  edges.forEach(([a, b]) => {
    if (!nodes.includes(a)) nodes.push(a);
    if (!nodes.includes(b)) nodes.push(b);
  });
  return nodes;
}

function nodeFact(node: NodeName, span: ShowcaseTraceSpan): string | null {
  const field = (label: string) => span.fields.find((f) => f.label.toLowerCase() === label)?.value;
  if (node === 'classify') return field('intent') ? `intent=${field('intent')}` : null;
  if (node === 'search') return field('packages') ? `packages=${field('packages')}` : compactDetails(span.details);
  if (node === 'availability') {
    const rows = field('rows');
    const step = field('step');
    return [rows ? `rows=${rows}` : null, step].filter(Boolean).join(' · ') || compactDetails(span.details);
  }
  if (node === 'memory_recall') return compactDetails(span.details) ?? 'context recalled';
  if (node === 'synthesize') return field('packages') ? `packages=${field('packages')}` : 'response ready';
  return null;
}

function compactDetails(details?: string): string | null {
  if (!details) return null;
  return details.length > 20 ? `${details.slice(0, 20)}...` : details;
}

// Build a smooth-ish path between the right and left edges of two nodes.
function edgePath(a: GraphNodeName, b: GraphNodeName): string {
  const p = NODE_LAYOUT[a];
  const q = NODE_LAYOUT[b];
  if (Math.abs(p.x - q.x) < 6) {
    const startY = p.y < q.y ? p.y + p.height / 2 : p.y - p.height / 2;
    const endY = p.y < q.y ? q.y - q.height / 2 : q.y + q.height / 2;
    const midY = (startY + endY) / 2;
    return `M ${p.x} ${startY} C ${p.x} ${midY}, ${q.x} ${midY}, ${q.x} ${endY}`;
  }
  const startX = p.x + p.width / 2;
  const endX = q.x - q.width / 2;
  const midX = (p.x + q.x) / 2;
  return `M ${startX} ${p.y} C ${midX} ${p.y}, ${midX} ${q.y}, ${endX} ${q.y}`;
}

function edgeLabelPosition(a: GraphNodeName, b: GraphNodeName): { x: number; y: number } {
  const p = NODE_LAYOUT[a];
  const q = NODE_LAYOUT[b];
  return {
    x: (p.x + q.x) / 2,
    y: (p.y + q.y) / 2 - (p.y === q.y ? 9 : 0),
  };
}

function edgeKey(a: GraphNodeName, b: GraphNodeName): string {
  return `${a}-${b}`;
}

function stepForNode(node: GraphNodeName, pathNodes: GraphNodeName[]): number | null {
  if (!WORKFLOW_NODES.includes(node as NodeName)) return null;
  const workflowPath = pathNodes.filter((n): n is NodeName => WORKFLOW_NODES.includes(n as NodeName));
  const index = workflowPath.indexOf(node as NodeName);
  return index >= 0 ? index + 1 : null;
}

export function WorkflowGraph({ state }: { state: MeridianShowcaseState }) {
  const { litNodes, activeEdges, intent, checkpointer, checkpointAfter, currentNode, nextNode, pathNodes, nodeFacts } = deriveActivation(
    state.traceSpans,
    state.isReplaying,
    state.replayIndex,
  );

  if (litNodes.size === 0) return null;

  const allEdges: [GraphNodeName, GraphNodeName][] = [
    ['start', 'classify'],
    ['classify', 'search'],
    ['classify', 'availability'],
    ['classify', 'memory_recall'],
    ['search', 'availability'],
    ['search', 'synthesize'],
    ['availability', 'synthesize'],
    ['memory_recall', 'synthesize'],
    ['synthesize', 'end'],
  ];
  const isActiveEdge = (a: GraphNodeName, b: GraphNodeName) =>
    activeEdges.some(([x, y]) => x === a && y === b);

  return (
    <div className="mds-wfgraph" role="img" aria-label="LangGraph workflow path">
      <div className="mds-wfgraph-head">
        <span className="mds-wfgraph-title">LangGraph StateGraph</span>
        {intent && <span className="mds-wfgraph-intent">intent: {intent}</span>}
      </div>
      <svg viewBox="0 0 430 210" className="mds-wfgraph-svg" preserveAspectRatio="xMidYMid meet">
        {/* edges: idle ones faint, active ones draw + glow */}
        {allEdges.map(([a, b]) => {
          const active = isActiveEdge(a, b);
          return (
            <g key={edgeKey(a, b)}>
              <motion.path
                d={edgePath(a, b)}
                className={`mds-wfgraph-edge${active ? ' is-active' : ''}`}
                fill="none"
                initial={false}
                animate={
                  prefersReducedMotion
                    ? { pathLength: active ? 1 : 0.001, opacity: active ? 1 : 0.12 }
                    : { pathLength: active ? 1 : 0.001, opacity: active ? 1 : 0.12 }
                }
                transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.45, ease: 'easeInOut' }}
              />
              {(a === 'classify' || active) && (
                <EdgeLabel
                  label={EDGE_LABELS[edgeKey(a, b)]}
                  active={active}
                  x={edgeLabelPosition(a, b).x}
                  y={edgeLabelPosition(a, b).y}
                />
              )}
            </g>
          );
        })}
        {/* nodes */}
        {(Object.keys(NODE_LAYOUT) as GraphNodeName[]).map((name) => {
          const { x, y, width, height, label } = NODE_LAYOUT[name];
          const lit = litNodes.has(name);
          const current = currentNode === name;
          const next = nextNode === name;
          const step = stepForNode(name, pathNodes);
          const fact = nodeFacts.get(name as NodeName);
          return (
            <g
              key={name}
              className={[
                'mds-wfgraph-node',
                lit ? 'is-lit' : '',
                current ? 'is-current' : '',
                next ? 'is-next' : '',
              ].filter(Boolean).join(' ')}
            >
              <motion.rect
                x={x - width / 2}
                y={y - height / 2}
                width={width}
                height={height}
                rx={8}
                initial={false}
                animate={
                  prefersReducedMotion
                    ? { opacity: lit ? 1 : 0.35 }
                    : { opacity: lit ? 1 : 0.35, scale: lit ? 1 : 0.96 }
                }
                transition={prefersReducedMotion ? { duration: 0 } : { type: 'spring', stiffness: 360, damping: 26 }}
                style={{ transformBox: 'fill-box', transformOrigin: 'center' }}
              />
              {lit && step && (
                <>
                  <circle cx={x - width / 2 + 10} cy={y - height / 2 + 9} r={7} className="mds-wfgraph-step" />
                  <text x={x - width / 2 + 10} y={y - height / 2 + 9.8} className="mds-wfgraph-step-label">
                    {step}
                  </text>
                </>
              )}
              <text x={x} y={fact ? y - 4 : y + 1} className="mds-wfgraph-node-label">
                {label}
              </text>
              {fact && (
                <text x={x} y={y + 10} className="mds-wfgraph-node-fact">
                  {fact}
                </text>
              )}
              {checkpointAfter.has(name as NodeName) && name !== 'synthesize' && (
                <g className="mds-wfgraph-ckpt-badge">
                  <rect x={x + width / 2 - 35} y={y - height / 2 - 15} width={70} height={15} rx={7} />
                  <text x={x + width / 2} y={y - height / 2 - 7}>checkpoint</text>
                  <circle cx={x + width / 2 - 27} cy={y - height / 2 - 7.5} r={3.2} className="mds-wfgraph-ckpt" />
                  <title>
                    Checkpoint saved after {label} · {checkpointer ?? 'checkpointer'}
                  </title>
                </g>
              )}
            </g>
          );
        })}
      </svg>
      {checkpointer && (
        <div className="mds-wfgraph-foot">
          <span className="mds-wfgraph-ckpt-dot" aria-hidden="true" />
          checkpointed · {checkpointer}
        </div>
      )}
    </div>
  );
}

function EdgeLabel({ label, active, x, y }: { label?: string; active: boolean; x: number; y: number }) {
  if (!label) return null;
  const width = Math.min(94, Math.max(38, label.length * 4.4 + 12));
  return (
    <g className={`mds-wfgraph-edge-label${active ? ' is-active' : ''}`}>
      <rect x={x - width / 2} y={y - 7} width={width} height={14} rx={6} />
      <text x={x} y={y + 0.8}>{label}</text>
    </g>
  );
}
