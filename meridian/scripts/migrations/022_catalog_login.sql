-- 022: a least-privilege Aurora login for the MeridianCatalog Lambda.
--
-- The meridian-catalog-tools Lambda answers the six catalog tools. It reads trip_packages,
-- directly and through semantic_trip_search (which runs with the caller's rights), reads places
-- left through package_places_left, and never writes. meridian_gateway, the login the holds
-- Lambda uses, can also read the identity bindings, append to the access audit and SET ROLE
-- meridian_app (migration 018); the catalog needs none of that. meridian_catalog owns nothing,
-- cannot bypass RLS, is a member of no role, and holds one table read and one function. The role
-- is created with NOLOGIN; scripts/provision_service_logins.py --login catalog sets LOGIN with a
-- SCRAM verifier and keeps the credential only in Secrets Manager.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'meridian_catalog') THEN
        CREATE ROLE meridian_catalog NOLOGIN NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE;
    ELSIF EXISTS (
        SELECT FROM pg_roles WHERE rolname = 'meridian_catalog'
           AND (rolsuper OR rolbypassrls OR rolinherit OR rolcreatedb OR rolcreaterole
                OR rolreplication)
    ) THEN
        RAISE EXCEPTION
            'meridian_catalog exists with attributes 022 does not allow; fix the role by hand';
    ELSIF EXISTS (
        SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
         WHERE r.rolname = 'meridian_catalog'
    ) OR EXISTS (
        SELECT FROM pg_class c
          CROSS JOIN LATERAL aclexplode(c.relacl) a
          JOIN pg_roles r ON r.oid = a.grantee
         WHERE r.rolname = 'meridian_catalog'
           AND NOT (c.relname = 'trip_packages' AND a.privilege_type = 'SELECT')
    ) THEN
        RAISE EXCEPTION
            'meridian_catalog exists with memberships or table grants 022 does not allow';
    END IF;
END
$$;

GRANT SELECT ON trip_packages TO meridian_catalog;

-- package_places_left is a definer function closed to PUBLIC (migration 020). The catalog reads
-- places left only through it, so bookings and booking_lines stay out of its reach.
GRANT EXECUTE ON FUNCTION package_places_left(TEXT) TO meridian_catalog;
