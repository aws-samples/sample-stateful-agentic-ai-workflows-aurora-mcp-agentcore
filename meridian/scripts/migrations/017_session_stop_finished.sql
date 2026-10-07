-- 017: a stop can also land after the run has finished. The worker had saved a
-- completed snapshot and only its lease release was outstanding, so the stop
-- closes the execution as succeeded and records 'finished', not a mid-run stop.
-- The check constraint keeps its live name and gains the third value.
ALTER TABLE workflow_session_stops DROP CONSTRAINT IF EXISTS workflow_session_stops_stopped_during_check;
ALTER TABLE workflow_session_stops ADD CONSTRAINT workflow_session_stops_stopped_during_check CHECK (stopped_during IN ('waiting', 'running', 'finished'));
