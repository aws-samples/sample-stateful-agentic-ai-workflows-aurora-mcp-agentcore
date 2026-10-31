/**
 * Accept a backend published before the snapshot rename, for one release.
 *
 * The static site and the backend image publish separately, so for one release each side also
 * speaks the other's field names: the backend sends both (backend/legacy_field_names.py) and
 * this reads either. The commit that adds migration 025 removes this file and its call sites.
 */

// The name each response field had before the rename, mapped to its name now.
const NAME_NOW: Record<string, string> = {
  checkpoint_backend: 'snapshot_store',
  checkpoint_durable: 'snapshot_durable',
  checkpoint_required: 'snapshot_required',
  checkpoint: 'snapshot',
  checkpoint_id: 'snapshot_id',
  parent_checkpoint_id: 'parent_snapshot_id',
  resumed_from_checkpoint: 'resumed_from_snapshot',
  durable_checkpoints: 'durable_snapshots',
  checkpoint_backend_durable: 'snapshot_store_durable',
};

/** Copy each old-named field to its current name, in place, through nested objects and arrays. */
export function withSnapshotNames<T>(value: T): T {
  if (Array.isArray(value)) {
    value.forEach((item) => withSnapshotNames(item));
    return value;
  }
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    Object.values(record).forEach((item) => withSnapshotNames(item));
    for (const [old, now] of Object.entries(NAME_NOW)) {
      if (old in record && !(now in record)) record[now] = record[old];
    }
  }
  return value;
}
