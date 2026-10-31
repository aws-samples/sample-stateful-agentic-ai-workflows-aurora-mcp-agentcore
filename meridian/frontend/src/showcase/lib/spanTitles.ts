/** Titles of the Phase 5 spans the backend writes for a saved step. */
export const SNAPSHOT_PREFIX = 'Snapshot saved: ';
export const PAUSED_TITLE = 'Workflow paused at a saved step';
export const RESUMED_TITLE = 'Workflow resumed from a saved step';
export const DURABLE_FIELD = 'snapshot_durable';

/** The fields that name the store and the table a snapshot went to. */
export const SNAPSHOT_STORE_FIELD = 'snapshot_store';
export const SNAPSHOT_TABLE_FIELD = 'snapshot_table';

/** The part of a span title after its last colon. */
export const lastTitlePart = (name: string) => name.split(':').pop()?.trim() ?? name;

/** The Runtime's closing span. */
export const RUNTIME_TURN_COMPLETE = /AgentCore Runtime: turn complete/i;

/** The MCP details line: "args=..., result: summary". */
export const MCP_ARGS_DETAILS = /^args=(.*?),\s+result:\s+(.*)$/s;

/** The span that records one snapshot written to Aurora. */
export const isSnapshotTitle = (name: string) => name.startsWith(SNAPSHOT_PREFIX);

/** The spans that report a run paused at, or resumed from, a saved step. */
export const isSavedStepStatusTitle = (name: string) =>
  name.startsWith(PAUSED_TITLE) || name.startsWith(RESUMED_TITLE);

/** Any span that says progress was saved. */
export const isSavedStepTitle = (name: string) =>
  isSnapshotTitle(name) || isSavedStepStatusTitle(name);

/** The field that says a snapshot reached Aurora. */
export const isDurableField = (label: string) => label === DURABLE_FIELD;

/** Trace steps that run as code carry these agent names. The Phase 1 reference agent's own
 *  entries carry its class name, SQLAgent. */
export const CODE_AGENTS = {
  sql: 'SQL search (code)',
  retrieval: 'Hybrid retrieval (code)',
} as const;

export const isSqlStep = (agent?: string) => agent === CODE_AGENTS.sql || agent === 'SQLAgent';

export const isRetrievalStep = (agent?: string) => agent === CODE_AGENTS.retrieval;
