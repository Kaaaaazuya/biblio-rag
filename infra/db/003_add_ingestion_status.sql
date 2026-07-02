-- Ingestion status tracking table for persistent storage across restarts.
-- Records current and historical ingestion state with timestamps.

CREATE TABLE IF NOT EXISTS ingestion_status (
    id           BIGSERIAL PRIMARY KEY,
    book_id      TEXT        NOT NULL,
    status       TEXT        NOT NULL,  -- 'pending', 'processing', 'completed', 'failed'
    chunks_processed INTEGER        DEFAULT 0,
    error_msg    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (book_id, status, created_at)  -- Allow historical tracking
);

-- Index for fast lookup by book_id and most recent status
CREATE INDEX IF NOT EXISTS ingestion_status_book_id_idx ON ingestion_status (book_id DESC, created_at DESC);

-- Index for query filtering by status
CREATE INDEX IF NOT EXISTS ingestion_status_status_idx ON ingestion_status (status);
