-- 034: clone logins and a rotation role, for Secrets Manager alternating users rotation.
--
-- Rotation with alternating users keeps two database users per login. The secret names one of
-- them, and each rotation sets a new password on the other and then makes the secret name it, so
-- a consumer that still holds the previous secret keeps working until the next rotation
-- (docs/OPERATIONS.md, "Rotate workload passwords automatically"). This migration creates the
-- second user of each pair and the role that sets the passwords. It changes no password, grants
-- no login and does nothing to a running workload; nothing uses the clones until rotation is
-- turned on for a login.
--
-- <login>_clone is a member of <login> with INHERIT TRUE and SET TRUE. It holds no grant of its
-- own: every table, sequence and function privilege, every row policy that names <login> (a policy
-- applies to a member that inherits the role) and the SET ROLE path to meridian_app (<login> holds
-- it with SET TRUE) reach the clone through the membership. A privilege added to <login> later
-- reaches the clone with it, so the two cannot drift apart. Role settings are not inherited, so the
-- clone gets a copy of the login's role settings (the 031 timeouts, and TimeZone for the audit
-- export login); this migration re-copies them each time it runs. The clone has the same
-- attributes as the login: NOLOGIN until rotation sets a password, NOBYPASSRLS, NOINHERIT (the
-- attribute only sets the default for new grants; the membership above names INHERIT TRUE),
-- NOCREATEDB, NOCREATEROLE, and no role of its own besides <login>.
--
-- meridian_rotator is the role the rotation Lambda connects as through the Data API. It holds
-- CREATEROLE, which PostgreSQL 16 and later require to change another role's password, and ADMIN
-- OPTION on exactly the twelve roles it rotates (INHERIT FALSE, SET FALSE: it can alter them and
-- grant them, it does not hold their privileges). A CREATEROLE role cannot create or alter a role
-- with SUPERUSER, REPLICATION or BYPASSRLS unless it has the attribute itself, and it cannot alter
-- the master or any role it holds no ADMIN OPTION on. It owns nothing, cannot bypass RLS, and
-- holds no table privilege. scripts/provision_rotation_login.py sets its LOGIN and password.
--
-- GRANT ... WITH INHERIT, SET and ADMIN needs PostgreSQL 16 or later (migration 018 needs it too).
DO $$
DECLARE
    login_name TEXT;
    clone_name TEXT;
    setting TEXT;
BEGIN
    FOREACH login_name IN ARRAY ARRAY[
        'meridian_backend', 'meridian_gateway', 'meridian_identity', 'meridian_catalog',
        'meridian_workflow', 'meridian_audit_export'
    ] LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = login_name) THEN
            RAISE EXCEPTION '% does not exist; apply migrations 015, 018, 022 and 033 first',
                login_name;
        ELSIF EXISTS (
            SELECT FROM pg_roles WHERE rolname = login_name
               AND (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication)
        ) THEN
            RAISE EXCEPTION '% has attributes 034 does not allow; fix the role by hand',
                login_name;
        END IF;

        clone_name := login_name || '_clone';
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = clone_name) THEN
            EXECUTE format(
                'CREATE ROLE %I NOLOGIN NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE', clone_name);
        ELSIF EXISTS (
            SELECT FROM pg_roles WHERE rolname = clone_name
               AND (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication
                    OR rolinherit)
        ) THEN
            RAISE EXCEPTION '% has attributes 034 does not allow; fix the role by hand',
                clone_name;
        ELSIF EXISTS (
            SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
              JOIN pg_roles g ON g.oid = m.roleid
             WHERE r.rolname = clone_name AND g.rolname <> login_name
        ) THEN
            RAISE EXCEPTION '% is a member of a role other than % that 034 does not allow',
                clone_name, login_name;
        END IF;

        EXECUTE format('GRANT %I TO %I WITH INHERIT TRUE, SET TRUE', login_name, clone_name);

        EXECUTE format('ALTER ROLE %I RESET ALL', clone_name);
        FOR setting IN
            SELECT unnest(s.setconfig)
              FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole
             WHERE r.rolname = login_name AND s.setdatabase = 0
        LOOP
            EXECUTE format(
                'ALTER ROLE %I SET %I = %L', clone_name,
                split_part(setting, '=', 1), substr(setting, strpos(setting, '=') + 1));
        END LOOP;
    END LOOP;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'meridian_rotator') THEN
        CREATE ROLE meridian_rotator NOLOGIN NOBYPASSRLS NOINHERIT NOCREATEDB CREATEROLE;
    ELSIF EXISTS (
        SELECT FROM pg_roles WHERE rolname = 'meridian_rotator'
           AND (rolsuper OR rolbypassrls OR rolinherit OR rolcreatedb OR rolreplication
                OR NOT rolcreaterole)
    ) THEN
        RAISE EXCEPTION 'meridian_rotator exists with attributes 034 does not allow';
    ELSIF EXISTS (
        SELECT FROM pg_class c
          CROSS JOIN LATERAL aclexplode(c.relacl) a
          JOIN pg_roles r ON r.oid = a.grantee
         WHERE r.rolname = 'meridian_rotator'
    ) THEN
        RAISE EXCEPTION 'meridian_rotator holds table grants 034 does not allow';
    END IF;

    FOREACH login_name IN ARRAY ARRAY[
        'meridian_backend', 'meridian_gateway', 'meridian_identity', 'meridian_catalog',
        'meridian_workflow', 'meridian_audit_export'
    ] LOOP
        EXECUTE format(
            'GRANT %I TO meridian_rotator WITH ADMIN TRUE, INHERIT FALSE, SET FALSE', login_name);
        EXECUTE format(
            'GRANT %I TO meridian_rotator WITH ADMIN TRUE, INHERIT FALSE, SET FALSE',
            login_name || '_clone');
    END LOOP;
END
$$;
