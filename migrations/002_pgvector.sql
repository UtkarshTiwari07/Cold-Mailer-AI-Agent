-- @requires-extension: vector
--
-- Optional. Applied only when the pgvector extension is actually available;
-- the migration runner probes pg_available_extensions and skips this file
-- otherwise. Without it, `evidence` retrieval falls back to Python-side cosine
-- similarity, which is perfectly adequate at this corpus size (~10k companies,
-- a few hundred thousand chunks) and keeps the system deployable on managed
-- Postgres instances that do not ship pgvector.

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE evidence ADD COLUMN IF NOT EXISTS embedding vector(384);

CREATE INDEX IF NOT EXISTS evidence_embedding_idx
    ON evidence USING hnsw (embedding vector_cosine_ops);
