-- Meridian — Travel concierge schema for Aurora PostgreSQL 17 + pgvector
-- Supports all demo phases:
--   1 Direct SQL filters on trip_packages
--   2 MCP tool access to the same tables
--   3 Hybrid semantic + lexical search (embeddings + tsvector)
--   4 Traveler profile, preferences, and conversation memory

CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- Trip catalog (Phases 1–4 search target)
-- =============================================================================
DROP TABLE IF EXISTS booking_lines CASCADE;
DROP TABLE IF EXISTS bookings CASCADE;
DROP TABLE IF EXISTS trip_interactions CASCADE;
DROP TABLE IF EXISTS conversation_messages CASCADE;
DROP TABLE IF EXISTS traveler_preferences CASCADE;
DROP TABLE IF EXISTS conversations CASCADE;
DROP TABLE IF EXISTS traveler_profiles CASCADE;
DROP TABLE IF EXISTS traveler_access_audit CASCADE;
DROP TABLE IF EXISTS traveler_identity_bindings CASCADE;
DROP TABLE IF EXISTS travelers CASCADE;
DROP TABLE IF EXISTS agent_traces CASCADE;
DROP TABLE IF EXISTS trip_packages CASCADE;

CREATE TABLE trip_packages (
    package_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    trip_type VARCHAR(100) NOT NULL,
    destination VARCHAR(150) NOT NULL,
    region VARCHAR(100),
    price_per_person DECIMAL(10, 2) NOT NULL,
    operator VARCHAR(100),
    description TEXT,
    image_url VARCHAR(500),
    durations JSONB NOT NULL DEFAULT '[]',
  -- e.g. ["5 nights", "7 nights"]
    availability JSONB NOT NULL DEFAULT '{}',
  -- e.g. {"5 nights": 8, "7 nights": 5}
    highlights JSONB,
  -- e.g. ["guided tours", "airport transfer"]
    embedding vector(1024),
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector(
            'english',
            name || ' ' || COALESCE(description, '') || ' ' ||
            COALESCE(operator, '') || ' ' || COALESCE(destination, '') || ' ' ||
            COALESCE(trip_type, '')
        )
    ) STORED,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_packages_embedding_hnsw ON trip_packages
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 100);
CREATE INDEX IF NOT EXISTS idx_packages_search_vector ON trip_packages USING gin(search_vector);
CREATE INDEX IF NOT EXISTS idx_packages_trip_type ON trip_packages(trip_type);
CREATE INDEX IF NOT EXISTS idx_packages_destination ON trip_packages(destination);
CREATE INDEX IF NOT EXISTS idx_packages_operator ON trip_packages(operator);
CREATE INDEX IF NOT EXISTS idx_packages_price ON trip_packages(price_per_person);
CREATE INDEX IF NOT EXISTS idx_packages_highlights ON trip_packages USING gin(highlights jsonb_path_ops);

CREATE OR REPLACE FUNCTION semantic_trip_search(
    query_embedding vector(1024),
    result_limit integer DEFAULT 5
) RETURNS TABLE (
    package_id varchar,
    name varchar,
    operator varchar,
    price_per_person decimal,
    description text,
    image_url varchar,
    trip_type varchar,
    destination varchar,
    region varchar,
    durations jsonb,
    similarity float
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        p.package_id,
        p.name,
        p.operator,
        p.price_per_person,
        p.description,
        p.image_url,
        p.trip_type,
        p.destination,
        p.region,
        p.durations,
        1 - (p.embedding <=> query_embedding) AS similarity
    FROM trip_packages p
    WHERE p.embedding IS NOT NULL
    ORDER BY p.embedding <=> query_embedding
    LIMIT result_limit;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- Travelers & profiles (Phase 4 long-term memory)
-- =============================================================================
CREATE TABLE travelers (
    traveler_id VARCHAR(50) PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    home_airport VARCHAR(10),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Application authorization boundary. RLS consumes a traveler_id; this table
-- proves the authenticated workload is allowed to claim that traveler first.
CREATE TABLE traveler_identity_bindings (
    binding_id VARCHAR(50) PRIMARY KEY,
    identity_provider VARCHAR(50) NOT NULL,
    subject_id VARCHAR(255) NOT NULL,
    traveler_id VARCHAR(50) NOT NULL REFERENCES travelers(traveler_id),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'revoked')),
    granted_by VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ,
    UNIQUE(identity_provider, subject_id, traveler_id)
);

CREATE INDEX idx_identity_bindings_subject
    ON traveler_identity_bindings(identity_provider, subject_id, status);

CREATE TABLE traveler_access_audit (
    audit_id VARCHAR(50) PRIMARY KEY,
    identity_provider VARCHAR(50) NOT NULL,
    subject_id VARCHAR(255) NOT NULL,
    principal TEXT,
    requested_traveler_id VARCHAR(50) NOT NULL,
    decision VARCHAR(10) NOT NULL CHECK (decision IN ('allow', 'deny')),
    reason TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_traveler_access_audit_subject
    ON traveler_access_audit(identity_provider, subject_id, decided_at DESC);

CREATE TABLE traveler_profiles (
    traveler_id VARCHAR(50) PRIMARY KEY REFERENCES travelers(traveler_id),
    party_size INTEGER DEFAULT 1,
    budget_min DECIMAL(10, 2),
    budget_max DECIMAL(10, 2),
    preferred_cabin VARCHAR(50),
    seat_preference VARCHAR(100),
    dietary_notes TEXT,
    trip_goal TEXT,
    loyalty_programs JSONB,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE traveler_preferences (
    preference_id VARCHAR(50) PRIMARY KEY,
    traveler_id VARCHAR(50) NOT NULL REFERENCES travelers(traveler_id),
    preference_type VARCHAR(50) NOT NULL,
  -- destination, activity, dining, logistics, budget
    preference_key VARCHAR(100) NOT NULL,
    preference_value TEXT NOT NULL,
    confidence FLOAT DEFAULT 0.5,
    signal_count INTEGER DEFAULT 1,
    source VARCHAR(80),
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(traveler_id, preference_type, preference_key)
);

CREATE INDEX IF NOT EXISTS idx_traveler_prefs ON traveler_preferences(traveler_id, confidence DESC);

-- =============================================================================
-- Bookings (demo order flow)
-- =============================================================================
CREATE TABLE bookings (
    booking_id VARCHAR(50) PRIMARY KEY,
    traveler_id VARCHAR(50) REFERENCES travelers(traveler_id),
    status VARCHAR(50) DEFAULT 'pending',
    total_amount DECIMAL(10, 2) NOT NULL,
    hold_expires_at TIMESTAMPTZ,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TIMESTAMP
);

CREATE TABLE booking_lines (
    line_id SERIAL PRIMARY KEY,
    booking_id VARCHAR(50) REFERENCES bookings(booking_id),
    package_id VARCHAR(50) REFERENCES trip_packages(package_id),
    duration VARCHAR(50),
    travelers_count INTEGER DEFAULT 1,
    unit_price DECIMAL(10, 2) NOT NULL
);

-- =============================================================================
-- Conversation memory (Phase 4 short-term + semantic recall)
-- =============================================================================
CREATE TABLE conversations (
    conversation_id VARCHAR(50) PRIMARY KEY,
    traveler_id VARCHAR(50) NOT NULL REFERENCES travelers(traveler_id),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER DEFAULT 0,
    summary TEXT
);

CREATE TABLE conversation_messages (
    message_id VARCHAR(50) PRIMARY KEY,
    conversation_id VARCHAR(50) REFERENCES conversations(conversation_id),
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    embedding vector(1024),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE trip_interactions (
    interaction_id VARCHAR(50) PRIMARY KEY,
    traveler_id VARCHAR(50) NOT NULL REFERENCES travelers(traveler_id),
    conversation_id VARCHAR(50) REFERENCES conversations(conversation_id),
    query_text TEXT NOT NULL,
    response_summary TEXT,
    packages_shown JSONB,
  -- [{package_id, name, was_selected}]
    embedding vector(1024),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversations_traveler ON conversations(traveler_id, last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON conversation_messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_interactions_traveler ON trip_interactions(traveler_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_embedding ON conversation_messages
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_interactions_embedding ON trip_interactions
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- =============================================================================
-- Agent observability
-- =============================================================================
CREATE TABLE agent_traces (
    trace_id VARCHAR(50) PRIMARY KEY,
    parent_trace_id VARCHAR(50) REFERENCES agent_traces(trace_id),
    conversation_id VARCHAR(50),
    agent_name VARCHAR(100) NOT NULL,
    phase INTEGER NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    embedding_calls INTEGER DEFAULT 0,
    db_queries INTEGER DEFAULT 0,
    total_latency_ms INTEGER,
    estimated_cost_usd DECIMAL(10, 6),
    status VARCHAR(20) DEFAULT 'success',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_traces_conversation ON agent_traces(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_traces_phase ON agent_traces(phase, created_at DESC);

COMMENT ON TABLE trip_packages IS 'Curated trip catalog with hybrid search vectors';
COMMENT ON TABLE travelers IS 'Registered travelers for bookings and personalization';
COMMENT ON TABLE traveler_identity_bindings IS 'Authorization grants from authenticated workload subjects to traveler records';
COMMENT ON TABLE traveler_access_audit IS 'Allow and deny decisions recorded before an RLS traveler scope is accepted';
COMMENT ON TABLE traveler_profiles IS 'Structured travel context used in Phase 4';
COMMENT ON TABLE traveler_preferences IS 'Learned preference signals for memory recall';
COMMENT ON TABLE trip_interactions IS 'Semantic memory of past concierge turns';
