/** The spans a paused recovery returns, in order, as the live backend records them:
 *  classify, search, then a durable Aurora snapshot, each with its measured time. */
export const PAUSED_ACTIVITIES = [
  ['Workflow node: classify → plan', 'Strands Graph', 0, []],
  ['Workflow node: search', 'Strands Graph → SearchAgent', 956, []],
  ['Snapshot saved: AuroraSnapshotStorage.write', 'Aurora workflow_snapshots', 458,
    [{ label: 'snapshot_durable', value: 'true' }]],
].map(([title, component, ms, fields], index) => ({
  id: `paused-${index}`, timestamp: '2026-09-28T18:00:00Z', activity_type: 'tool_call', title,
  execution_time_ms: ms,
  telemetry: { category: 'orchestration', component, status: 'ok', fields },
}));

/** The `/api/chat` reply for a recovery paused at its checkpoint.
 *
 * @param products The ranked options the reply carries.
 * @param conversationId The thread the request named, echoed back as the backend does.
 */
export function pausedRecovery(products: object[], conversationId: string) {
  return {
    message: 'Workflow paused after a committed checkpoint.',
    workflow_status: 'paused',
    products,
    activities: PAUSED_ACTIVITIES,
    conversation_id: conversationId,
  };
}
