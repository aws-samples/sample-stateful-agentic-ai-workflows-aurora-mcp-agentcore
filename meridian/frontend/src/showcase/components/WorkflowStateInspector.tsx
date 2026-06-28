import { useState } from 'react';
import { ChevronDown, DatabaseZap, GitBranch, Route } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { deriveWorkflowState } from '../lib/showcaseProof';

export function WorkflowStateInspector({ state }: { state: MeridianShowcaseState }) {
  const [collapsed, setCollapsed] = useState(true);
  const workflow = deriveWorkflowState(state.traceSpans);

  return (
    <section
      className={`mds-workflow-state is-${workflow.status}${collapsed ? ' is-collapsed' : ''}`}
      aria-label="Workflow state inspector"
    >
      <button
        type="button"
        className="mds-workflow-state-head"
        onClick={() => setCollapsed((value) => !value)}
        aria-expanded={!collapsed}
        aria-label={collapsed ? 'Expand workflow state proof' : 'Collapse workflow state proof'}
      >
        <ChevronDown className="mds-workflow-state-chevron" size={15} strokeWidth={2.4} aria-hidden="true" />
        <span className="mds-workflow-state-icon" aria-hidden="true">
          <Route size={16} strokeWidth={2.2} />
        </span>
        <div>
          <strong>Workflow state</strong>
          <small>{workflow.status === 'ready' ? 'waiting for a Phase 5 prompt' : workflow.status}</small>
        </div>
      </button>

      {!collapsed && (
        <>
          <div className="mds-workflow-kv">
            <div>
              <span>Intent</span>
              <b>{workflow.intent}</b>
            </div>
            <div>
              <span>Next</span>
              <b>{workflow.nextNode}</b>
            </div>
            <div>
              <span>Checkpoint</span>
              <b>{workflow.checkpoint}</b>
            </div>
          </div>

          <div className="mds-workflow-path" aria-label="Executed workflow path">
            <GitBranch size={14} strokeWidth={2.1} aria-hidden="true" />
            {workflow.path.map((node) => (
              <span
                key={node}
                className={workflow.visited.includes(node) ? 'is-visited' : ''}
              >
                {node}
              </span>
            ))}
          </div>

          <div className="mds-workflow-checkpoint">
            <DatabaseZap size={14} strokeWidth={2.1} aria-hidden="true" />
            <span>
              {workflow.checkpointCount
                ? `${workflow.checkpointCount} write${workflow.checkpointCount === 1 ? '' : 's'} to ${workflow.table}`
                : `writes to ${workflow.table} after worker nodes`}
            </span>
          </div>
        </>
      )}
    </section>
  );
}
