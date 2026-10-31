import { describe, expect, it } from 'vitest';
import { withSnapshotNames } from './legacyFieldNames';
import { healthResponseToStatus } from '../showcase/lib/showcaseAdapters';

describe('withSnapshotNames', () => {
  it('reads a health response from a backend published before the rename', () => {
    const old = {
      status: 'healthy', bedrock_model_id: 'm', embedding_model_id: 'e',
      checkpoint_backend: 'Aurora workflow_snapshots', checkpoint_durable: true,
      checkpoint_required: true,
    };
    const health = withSnapshotNames({ ...old }) as Record<string, unknown>;
    expect(health.snapshot_store).toBe('Aurora workflow_snapshots');
    expect(health.snapshot_durable).toBe(true);
    expect(healthResponseToStatus(health)).toBe('online');
  });

  it('maps nested journey document fields and list items', () => {
    const document = withSnapshotNames({
      checkpoint_backend: { kind: 'Aurora workflow_snapshots', durable: true },
      checkpoint: { checkpoint_id: 7, parent_checkpoint_id: 6 },
      workflow: { resumed_from_checkpoint: 6 },
      journeys: [{ checkpoint_backend: 'Aurora workflow_snapshots' }],
    }) as Record<string, any>;
    expect(document.snapshot_store.durable).toBe(true);
    expect(document.snapshot.snapshot_id).toBe(7);
    expect(document.snapshot.parent_snapshot_id).toBe(6);
    expect(document.workflow.resumed_from_snapshot).toBe(6);
    expect(document.journeys[0].snapshot_store).toBe('Aurora workflow_snapshots');
  });

  it('keeps a current field when both names arrive', () => {
    const body = withSnapshotNames({ snapshot_store: 'now', checkpoint_backend: 'before' });
    expect(body.snapshot_store).toBe('now');
  });

  it('passes other values through', () => {
    expect(withSnapshotNames({ status: 'ok' })).toEqual({ status: 'ok' });
    expect(withSnapshotNames(null)).toBeNull();
    expect(withSnapshotNames([1, 'two'])).toEqual([1, 'two']);
  });
});
