-- Catalog index (the markdown files are the source of truth), indexing runs, background
-- tasks and chat data.

CREATE TABLE catalogs (
    id             bigserial PRIMARY KEY,
    application_id bigint      NOT NULL UNIQUE REFERENCES applications (id) ON DELETE CASCADE,
    path           text        NOT NULL,                   -- relative to STC_CATALOGS
    content_sha256 text        NOT NULL,
    code_version   text,                                   -- code version it was generated from (LCY-08)
    llm_provider   text,
    llm_model      text,
    language       text        NOT NULL DEFAULT 'en',
    generated_at   timestamptz NOT NULL DEFAULT now()
);

-- One row per catalog section, searchable separately from code (QRY-05).
CREATE TABLE catalog_sections (
    id            bigserial PRIMARY KEY,
    catalog_id    bigint  NOT NULL REFERENCES catalogs (id) ON DELETE CASCADE,
    section       text    NOT NULL,
    content       text    NOT NULL,
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    UNIQUE (catalog_id, section)
);
CREATE INDEX catalog_sections_tsv_idx ON catalog_sections USING gin (tsv);

CREATE TABLE catalog_section_embeddings (
    section_id    bigint      PRIMARY KEY REFERENCES catalog_sections (id) ON DELETE CASCADE,
    provider      text        NOT NULL,
    model         text        NOT NULL,
    model_version text        NOT NULL DEFAULT '',
    embedding     vector(1536) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX catalog_section_embeddings_hnsw_idx
    ON catalog_section_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE indexing_runs (
    id             bigserial PRIMARY KEY,
    kind           text        NOT NULL CHECK (kind IN ('full', 'incremental', 'application')),
    application_id bigint      REFERENCES applications (id) ON DELETE SET NULL,
    status         text        NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'succeeded', 'failed', 'partial')),
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    summary        jsonb       NOT NULL DEFAULT '{}'::jsonb,   -- counts, cost estimate, per-app errors
    error          text
);

-- Background tasks claimed by workers with FOR UPDATE SKIP LOCKED.
CREATE TABLE tasks (
    id         bigserial PRIMARY KEY,
    kind       text        NOT NULL,
    payload    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    status     text        NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'running', 'done', 'failed')),
    attempts   integer     NOT NULL DEFAULT 0,
    run_after  timestamptz NOT NULL DEFAULT now(),
    locked_at  timestamptz,
    locked_by  text,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tasks_claim_idx ON tasks (run_after) WHERE status = 'pending';

CREATE TABLE conversations (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    title      text,
    scope      jsonb       NOT NULL DEFAULT '{"type": "root"}'::jsonb,   -- root | applications (CHT-06)
    profile    text        NOT NULL DEFAULT 'technical'
               CHECK (profile IN ('technical', 'functional')),         -- CHT-07
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id              bigserial PRIMARY KEY,
    conversation_id uuid        NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role            text        NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text        NOT NULL,
    citations       jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- application, file, lines, version (CHT-03)
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_conversation_idx ON messages (conversation_id, id);

CREATE TABLE feedback (
    id              bigserial PRIMARY KEY,
    message_id      bigint      NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    rating          text        NOT NULL CHECK (rating IN ('incorrect', 'incomplete')),
    correction      text,
    proposal_status text        NOT NULL DEFAULT 'none'
                    CHECK (proposal_status IN ('none', 'proposed', 'approved', 'rejected')),  -- CHT-10
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX feedback_message_idx ON feedback (message_id);
