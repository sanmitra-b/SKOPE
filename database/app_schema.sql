BEGIN;

CREATE SCHEMA IF NOT EXISTS rag;
CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS rag.ingestion_run (
    run_id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    status VARCHAR(30) NOT NULL,
    corpus_version VARCHAR(80) NOT NULL,
    embedding_model VARCHAR(160),
    collection_name VARCHAR(160),
    source_document_count INTEGER NOT NULL DEFAULT 0,
    extracted_document_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    failed_document_count INTEGER NOT NULL DEFAULT 0,
    configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS rag.document_chunk (
    chunk_id UUID PRIMARY KEY,
    document_id CHAR(64) NOT NULL REFERENCES skope.document_index(document_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    section_label TEXT,
    content TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    token_estimate INTEGER NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    access_classification VARCHAR(40) NOT NULL DEFAULT 'INTERNAL',
    parser_version VARCHAR(40) NOT NULL,
    chunker_version VARCHAR(40) NOT NULL,
    indexed_at TIMESTAMPTZ,
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(section_label, '') || ' ' || content)
    ) STORED,
    UNIQUE (document_id, chunk_index, content_sha256)
);

CREATE INDEX IF NOT EXISTS idx_document_chunk_document
    ON rag.document_chunk(document_id);
CREATE INDEX IF NOT EXISTS idx_document_chunk_search
    ON rag.document_chunk USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_document_chunk_metadata
    ON rag.document_chunk USING GIN(metadata);

CREATE TABLE IF NOT EXISTS app.app_user (
    firebase_uid VARCHAR(160) PRIMARY KEY,
    email TEXT NOT NULL,
    display_name TEXT,
    role VARCHAR(40) NOT NULL DEFAULT 'OPERATIONS_USER'
        CHECK (role IN ('OPERATIONS_USER', 'MANAGER', 'ADMINISTRATOR', 'PII_OPERATOR')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app.conversation (
    conversation_id UUID PRIMARY KEY,
    firebase_uid VARCHAR(160) NOT NULL,
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversation_user_updated
    ON app.conversation(firebase_uid, updated_at DESC);

CREATE TABLE IF NOT EXISTS app.message (
    message_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES app.conversation(conversation_id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    routing JSONB NOT NULL DEFAULT '{}'::jsonb,
    claims JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_message_conversation_created
    ON app.message(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS app.audit_event (
    event_id UUID PRIMARY KEY,
    firebase_uid VARCHAR(160),
    request_id UUID,
    event_type VARCHAR(80) NOT NULL,
    resource_type VARCHAR(80),
    resource_id TEXT,
    purpose TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_event_created
    ON app.audit_event(created_at DESC);

CREATE TABLE IF NOT EXISTS app.flagged_response (
    flag_id UUID PRIMARY KEY,
    message_id UUID REFERENCES app.message(message_id) ON DELETE SET NULL,
    request_id UUID,
    reason_code VARCHAR(80) NOT NULL,
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH')),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'REVIEWED', 'DISMISSED', 'RESOLVED')),
    reviewed_by VARCHAR(160),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app.evaluation_run (
    evaluation_run_id UUID PRIMARY KEY,
    version VARCHAR(80) NOT NULL,
    status VARCHAR(30) NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ
);

GRANT USAGE ON SCHEMA rag TO skope_rag_reader;
GRANT SELECT ON rag.document_chunk, rag.ingestion_run TO skope_rag_reader;

COMMIT;
