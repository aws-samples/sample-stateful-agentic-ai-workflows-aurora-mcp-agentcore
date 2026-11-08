-- Aurora PostgreSQL 18.6 provides pgvector 0.8.2. An engine upgrade leaves an installed
-- extension at its old version, so update it explicitly after the engine reaches 18.6.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_available_extension_versions
        WHERE name = 'vector' AND version = '0.8.2'
    ) THEN
        RAISE EXCEPTION 'pgvector 0.8.2 is not available: upgrade Aurora PostgreSQL to 18.6 first';
    END IF;
END
$$;

ALTER EXTENSION vector UPDATE TO '0.8.2';
