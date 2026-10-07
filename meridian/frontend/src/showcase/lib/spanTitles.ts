/** Titles of the Phase 5 spans the backend writes for a saved step. */
export const SNAPSHOT_PREFIX = 'Snapshot saved: ';
export const PAUSED_TITLE = 'Workflow paused at a saved step';
export const RESUMED_TITLE = 'Workflow resumed from a saved step';
export const DURABLE_FIELD = 'snapshot_durable';

/** The wording from before the Strands Graph. A journey saved earlier replays its spans from
 *  the stored snapshot, so the old titles and field name still reach every parser here. */
export const LEGACY_TITLES = {
  snapshotPrefix: 'Checkpoint · ',
  paused: 'Workflow paused at checkpoint',
  resumed: 'Workflow resumed from checkpoint',
  durableField: 'checkpoint_durable',
} as const;

/** Phases 3 and 4 and journeys saved earlier still carry a middle dot between the parts of a
 *  span title or its details, so the parsers split on it. Nothing here shows it to the room. */
export const LEGACY_SEPARATOR = '·';

/** The component name earlier workflow spans carried, kept so a saved journey still routes. */
export const isLegacyGraphComponent = (component: string) => /^LangGraph/i.test(component);

const startsWithAny = (name: string, prefixes: string[]) =>
  prefixes.some(prefix => name.startsWith(prefix));

/** The span that records one snapshot written to Aurora. */
export const isSnapshotTitle = (name: string) =>
  startsWithAny(name, [SNAPSHOT_PREFIX, LEGACY_TITLES.snapshotPrefix]);

/** The spans that report a run paused at, or resumed from, a saved step. */
export const isSavedStepStatusTitle = (name: string) =>
  startsWithAny(name, [PAUSED_TITLE, RESUMED_TITLE, LEGACY_TITLES.paused, LEGACY_TITLES.resumed]);

/** Any span that says progress was saved. */
export const isSavedStepTitle = (name: string) =>
  isSnapshotTitle(name) || isSavedStepStatusTitle(name);

/** The field that says a snapshot reached Aurora. */
export const isDurableField = (label: string) =>
  label === DURABLE_FIELD || label === LEGACY_TITLES.durableField;
