-- 019: the workflow login may lock the execution row its snapshot write is fenced on.
--
-- AuroraSnapshotStorage reads the execution row FOR SHARE inside the snapshot INSERT.
-- A takeover, a stop or a release then cannot change the execution while a write is in
-- flight, and a write that had to wait sees the changed row and is refused.
-- PostgreSQL lets a role lock rows only when it holds UPDATE on at least one column of
-- the table. The workflow never updates through this grant: claims, renewals and
-- releases still run in a scoped session as meridian_app. The grant is column-level, so
-- has_table_privilege(..., 'UPDATE') stays false for the login.
GRANT UPDATE (lease_expires_at) ON journey_executions TO meridian_workflow;
