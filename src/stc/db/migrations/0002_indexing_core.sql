-- Core model of the indexer: applications, files, chunks, symbols, embeddings, relations.
-- Embeddings are stored at 1536 dimensions. Changing the embedding dimension needs a new
-- migration and a full reindex (see docs/tech-stack.md, section 5).

CREATE TABLE applications (
    id              bigserial PRIMARY KEY,
    name            text        NOT NULL UNIQUE,
    path            text        NOT NULL,                 -- relative to the Root
    detected_by     text        NOT NULL DEFAULT 'auto'
                    CHECK (detected_by IN ('auto', 'manual')),
    indexed_version text,                                  -- code version the knowledge matches (LCY-08)
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE files (
    id               bigserial PRIMARY KEY,
    application_id   bigint      NOT NULL REFERENCES applications (id) ON DELETE CASCADE,
    path             text        NOT NULL,                 -- relative to the application
    language         text,
    size_bytes       bigint      NOT NULL,
    status           text        NOT NULL
                     CHECK (status IN ('indexed', 'excluded', 'secret', 'unsupported', 'error')),
    exclusion_reason text,                                 -- never holds the secret value (IDX-06)
    content_sha256   text,
    indexed_at       timestamptz,
    UNIQUE (application_id, path)
);
CREATE INDEX files_status_idx ON files (application_id, status);

CREATE TABLE chunks (
    id             bigserial PRIMARY KEY,
    file_id        bigint  NOT NULL REFERENCES files (id) ON DELETE CASCADE,
    kind           text    NOT NULL,                       -- function, class, module, text, ...
    name           text,
    start_line     integer NOT NULL CHECK (start_line >= 1),
    end_line       integer NOT NULL CHECK (end_line >= start_line),
    content        text    NOT NULL,
    content_sha256 text    NOT NULL,
    tsv            tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
);
CREATE INDEX chunks_file_idx ON chunks (file_id);
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_name_trgm_idx ON chunks USING gin (name gin_trgm_ops);

CREATE TABLE chunk_embeddings (
    chunk_id      bigint      PRIMARY KEY REFERENCES chunks (id) ON DELETE CASCADE,
    provider      text        NOT NULL,
    model         text        NOT NULL,
    model_version text        NOT NULL DEFAULT '',
    embedding     vector(1536) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX chunk_embeddings_hnsw_idx
    ON chunk_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE symbols (
    id         bigserial PRIMARY KEY,
    file_id    bigint  NOT NULL REFERENCES files (id) ON DELETE CASCADE,
    chunk_id   bigint  REFERENCES chunks (id) ON DELETE SET NULL,
    name       text    NOT NULL,
    kind       text    NOT NULL,                           -- function, class, method, variable, ...
    is_definition boolean NOT NULL DEFAULT true,           -- false = usage
    start_line integer NOT NULL CHECK (start_line >= 1),
    end_line   integer NOT NULL CHECK (end_line >= start_line)
);
CREATE INDEX symbols_file_idx ON symbols (file_id);
CREATE INDEX symbols_name_trgm_idx ON symbols USING gin (name gin_trgm_ops);

-- Directed application -> application relations, each backed by evidence (CAT-07).
CREATE TABLE relations (
    id                   bigserial PRIMARY KEY,
    source_app_id        bigint  NOT NULL REFERENCES applications (id) ON DELETE CASCADE,
    target_app_id        bigint  NOT NULL REFERENCES applications (id) ON DELETE CASCADE,
    type                 text    NOT NULL,                 -- http, messaging, database, library, config, ...
    detail               text,
    evidence_file_id     bigint  REFERENCES files (id) ON DELETE SET NULL,
    evidence_start_line  integer,
    evidence_end_line    integer,
    confidence           text    NOT NULL DEFAULT 'medium'
                         CHECK (confidence IN ('low', 'medium', 'high')),
    confirmed_by_llm     boolean NOT NULL DEFAULT false,
    CHECK (source_app_id <> target_app_id)
);
CREATE INDEX relations_source_idx ON relations (source_app_id);
CREATE INDEX relations_target_idx ON relations (target_app_id);
