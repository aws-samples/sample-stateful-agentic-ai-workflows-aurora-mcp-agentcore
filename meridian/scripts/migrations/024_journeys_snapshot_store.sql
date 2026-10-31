-- 024: journeys.snapshot_store, added beside journeys.checkpoint_backend (the expand step).
--
-- Phase 5 saves snapshots, so the column that records which store serves a journey takes the
-- same word. The rename is split in two so every deployed piece keeps working while the system
-- is updated one piece at a time. This migration adds snapshot_store and keeps it equal to
-- checkpoint_backend with a trigger. Code from before this commit writes checkpoint_backend
-- only; code from this commit writes snapshot_store only; both keep working. A later migration
-- (025, shipped after the deploy is verified) drops checkpoint_backend and the trigger.
--
-- The trigger runs before the NOT NULL checks, so an insert naming one column fills the other
-- first. When both are given and differ on insert, or both change on an update, snapshot_store
-- wins. Grants on journeys are table level (007, 015), so snapshot_store needs none of its own.
ALTER TABLE journeys ADD COLUMN IF NOT EXISTS snapshot_store VARCHAR(32);

CREATE OR REPLACE FUNCTION journeys_sync_snapshot_store()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.snapshot_store IS NULL THEN
            NEW.snapshot_store := NEW.checkpoint_backend;
        ELSE
            NEW.checkpoint_backend := NEW.snapshot_store;
        END IF;
    ELSIF NEW.snapshot_store IS DISTINCT FROM OLD.snapshot_store THEN
        NEW.checkpoint_backend := NEW.snapshot_store;
    ELSIF NEW.checkpoint_backend IS DISTINCT FROM OLD.checkpoint_backend
          OR NEW.snapshot_store IS NULL THEN
        NEW.snapshot_store := NEW.checkpoint_backend;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS journeys_sync_snapshot_store ON journeys;
CREATE TRIGGER journeys_sync_snapshot_store
    BEFORE INSERT OR UPDATE ON journeys
    FOR EACH ROW EXECUTE FUNCTION journeys_sync_snapshot_store();

UPDATE journeys SET snapshot_store = checkpoint_backend WHERE snapshot_store IS NULL;

ALTER TABLE journeys ALTER COLUMN snapshot_store SET NOT NULL;
