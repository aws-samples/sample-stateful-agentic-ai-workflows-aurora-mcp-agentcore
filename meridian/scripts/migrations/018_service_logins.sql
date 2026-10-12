-- 018: least-privilege Aurora logins for the App Runner backend, the gateway Lambdas
-- and the Cognito pre-token trigger.
--
-- Each login owns nothing, cannot bypass RLS, and holds only what its workload
-- queries. meridian_backend and meridian_gateway reach meridian_app's privileges
-- only through SET ROLE inside a scoped session. meridian_identity reads the
-- identity bindings and nothing else. The roles are created with NOLOGIN;
-- scripts/provision_service_logins.py sets LOGIN with a SCRAM verifier and keeps
-- each credential only in Secrets Manager. The master login stays for migrations
-- and operations scripts.
DO $$
DECLARE
    login_name TEXT;
BEGIN
    FOREACH login_name IN ARRAY ARRAY[
        'meridian_backend', 'meridian_gateway', 'meridian_identity'
    ] LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = login_name) THEN
            EXECUTE format(
                'CREATE ROLE %I NOLOGIN NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE', login_name);
        ELSIF EXISTS (
            SELECT FROM pg_roles WHERE rolname = login_name
               AND (rolsuper OR rolbypassrls OR rolinherit OR rolcreatedb OR rolcreaterole
                    OR rolreplication)
        ) THEN
            RAISE EXCEPTION
                '% exists with attributes 018 does not allow; fix the role by hand', login_name;
        END IF;
    END LOOP;
END
$$;

GRANT meridian_app TO meridian_backend WITH INHERIT FALSE, SET TRUE;
GRANT meridian_app TO meridian_gateway WITH INHERIT FALSE, SET TRUE;

-- scoped_session checks the traveler grant and writes the audit row as the login,
-- before SET ROLE. The Holds Lambda does the same, and also writes a DENY row on its own.
GRANT SELECT ON traveler_identity_bindings TO meridian_backend;
GRANT SELECT ON traveler_identity_bindings TO meridian_gateway;
GRANT INSERT ON traveler_access_audit TO meridian_backend;
GRANT INSERT ON traveler_access_audit TO meridian_gateway;
DROP POLICY IF EXISTS traveler_access_audit_backend_insert ON traveler_access_audit;
CREATE POLICY traveler_access_audit_backend_insert ON traveler_access_audit
    FOR INSERT TO meridian_backend WITH CHECK (true);
DROP POLICY IF EXISTS traveler_access_audit_gateway_insert ON traveler_access_audit;
CREATE POLICY traveler_access_audit_gateway_insert ON traveler_access_audit
    FOR INSERT TO meridian_gateway WITH CHECK (true);

-- The catalog is read outside any traveler scope: package search, package details and the
-- price check before the hold steps down to meridian_app. semantic_trip_search runs with
-- the caller's rights.
GRANT SELECT ON trip_packages TO meridian_backend;
GRANT SELECT ON trip_packages TO meridian_gateway;

-- The Cognito pre-token trigger resolves a sign-in to its traveler binding.
GRANT SELECT ON traveler_identity_bindings TO meridian_identity;

-- The presenter evidence endpoints count rows across every traveler: the RLS baseline,
-- the governance decisions and the snapshot total. The master login used to do this by
-- ignoring RLS. This definer function is the only cross-traveler read the backend login
-- holds, it returns a count and nothing else, and it answers only the kinds named here.
--
-- The function runs as its owner, the login that applies this migration (the master,
-- meridian_admin). Five of the counted tables force row level security, and their
-- policies target meridian_app or a traveler setting the owner never has, so the owner
-- would count zero rows. Each of those tables gets one SELECT policy for the owner
-- alone, created below with current_user. It names no service login and no
-- meridian_app, so the function stays the only cross-traveler read.
-- GRANT ... WITH INHERIT FALSE, SET TRUE above needs PostgreSQL 16 or later.
DO $$
DECLARE
    forced RECORD;
BEGIN
    FOR forced IN
        SELECT * FROM (VALUES
            ('traveler_preferences', 'traveler_preferences_admin_count_select'),
            ('trip_interactions', 'trip_interactions_admin_count_select'),
            ('conversations', 'conversations_admin_count_select'),
            ('conversation_messages', 'conversation_messages_admin_count_select'),
            ('agent_audit_log', 'agent_audit_admin_count_select')
        ) AS t (table_name, policy_name)
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', forced.policy_name, forced.table_name);
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR SELECT TO %I USING (true)',
            forced.policy_name, forced.table_name, current_user);
    END LOOP;
END
$$;

CREATE OR REPLACE FUNCTION backend_admin_count(
    p_kind TEXT,
    p_window INTERVAL DEFAULT NULL,
    p_key TEXT DEFAULT NULL
) RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_count BIGINT;
BEGIN
    IF (p_kind IN ('audit_allow', 'audit_deny', 'agent_audit') AND p_window IS NULL)
       OR (p_kind = 'workflow_snapshots' AND p_key IS NULL) THEN
        RAISE EXCEPTION 'backend_admin_count % needs its window or key', p_kind;
    END IF;
    IF p_kind = 'rls_traveler_preferences' THEN
        SELECT count(*) INTO v_count FROM traveler_preferences;
    ELSIF p_kind = 'rls_trip_interactions' THEN
        SELECT count(*) INTO v_count FROM trip_interactions;
    ELSIF p_kind = 'rls_conversations' THEN
        SELECT count(*) INTO v_count FROM conversations;
    ELSIF p_kind = 'rls_conversation_messages' THEN
        SELECT count(*) INTO v_count FROM conversation_messages;
    ELSIF p_kind IN ('audit_allow', 'audit_deny') THEN
        SELECT count(*) INTO v_count FROM traveler_access_audit
         WHERE decision = substr(p_kind, 7) AND decided_at > CURRENT_TIMESTAMP - p_window;
    ELSIF p_kind = 'agent_audit' THEN
        SELECT count(*) INTO v_count FROM agent_audit_log
         WHERE ran_at > CURRENT_TIMESTAMP - p_window;
    ELSIF p_kind = 'workflow_snapshots' THEN
        SELECT count(*) INTO v_count FROM workflow_snapshots WHERE session_id = p_key;
    ELSE
        RAISE EXCEPTION 'unknown backend_admin_count kind: %', p_kind;
    END IF;
    RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION backend_admin_count(TEXT, INTERVAL, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION backend_admin_count(TEXT, INTERVAL, TEXT) TO meridian_backend;
