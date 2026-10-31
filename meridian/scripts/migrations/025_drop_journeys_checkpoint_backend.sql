-- 025: drop journeys.checkpoint_backend and the trigger that kept it equal to snapshot_store
-- (the contract step after 024).
--
-- Apply only after every deployed writer of journeys runs code from the 024 commit or later,
-- which writes snapshot_store: the backend, the MeridianHolds Lambda and the MeridianWorkflow
-- Runtime. Code from before that commit names checkpoint_backend and fails once this runs.
DROP TRIGGER IF EXISTS journeys_sync_snapshot_store ON journeys;

DROP FUNCTION IF EXISTS journeys_sync_snapshot_store();

ALTER TABLE journeys DROP COLUMN IF EXISTS checkpoint_backend;
