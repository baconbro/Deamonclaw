-- DAEMON sidecar database initialisation
-- Runs automatically when the pgvector/pgvector:pg16 container first starts.

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Episodes table (episodic memory)
CREATE TABLE IF NOT EXISTS episodes (
    id           TEXT PRIMARY KEY,
    type         TEXT        NOT NULL DEFAULT 'episodic',
    goal         TEXT        NOT NULL,
    outcome      TEXT        NOT NULL,
    success      BOOLEAN     NOT NULL DEFAULT TRUE,
    tools_used   JSONB       NOT NULL DEFAULT '[]',
    tags         JSONB       NOT NULL DEFAULT '[]',
    embedding    vector(1536),          -- OpenAI / Claude embedding dimension
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ANN index for semantic recall (requires >= 100 rows to be useful)
CREATE INDEX IF NOT EXISTS episodes_embedding_idx
    ON episodes USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Text-search index for keyword recall fallback
CREATE INDEX IF NOT EXISTS episodes_goal_gin_idx
    ON episodes USING gin(to_tsvector('english', goal || ' ' || outcome));

-- Interactions log (raw message history)
CREATE TABLE IF NOT EXISTS interactions (
    id         TEXT PRIMARY KEY,
    role       TEXT        NOT NULL,  -- 'user' | 'agent'
    content    TEXT        NOT NULL,
    channel    TEXT,                  -- 'whatsapp' | 'telegram' | 'slack' | ...
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Side-effect audit ledger
CREATE TABLE IF NOT EXISTS ledger (
    id               TEXT PRIMARY KEY,
    tool_name        TEXT        NOT NULL,
    tool_input       JSONB       NOT NULL DEFAULT '{}',
    tool_output      TEXT        NOT NULL DEFAULT '',
    permission_tier  INTEGER     NOT NULL DEFAULT 0,
    side_effect_type TEXT        NOT NULL DEFAULT 'unknown',
    reversible       BOOLEAN     NOT NULL DEFAULT TRUE,
    timestamp        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ledger_timestamp_idx ON ledger (timestamp DESC);
CREATE INDEX IF NOT EXISTS ledger_tier_idx      ON ledger (permission_tier);
